"""Thin ROS2 bridge node for HackRF IQ data and device state.

BridgeNode subscribes to Redis Pub/Sub channel hackrf:iq:notify (D-15),
reads the latest IQ entry from hackrf:iq:stream, and publishes it to
/hackrf/iq as Float32MultiArray (D-18: same type and topic as before).

Also reads hackrf:state hash and publishes JSON to /hackrf/state (REF-06).

No pyhackrf2, no serial, no MayhemSerial, no TXController imports.
"""

import json
import threading

import numpy as np

_MIN_BACKOFF = 1.0   # D-07: exponential backoff start (seconds)
_MAX_BACKOFF = 30.0  # D-07: exponential backoff ceiling (seconds)

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import Float32MultiArray, String

try:
    import redis
    import redis.exceptions as _redis_exceptions
except ImportError:
    redis = None
    _redis_exceptions = None

from hackrf_ros.bridge_services import register_bridge_services


class BridgeNode(Node):
    """ROS2 bridge: reads IQ and state from Redis, publishes to ROS2 topics.

    Subscribes to hackrf:iq:notify Pub/Sub channel (D-15).
    Publishes Float32MultiArray to /hackrf/iq (D-18).
    Publishes JSON String to /hackrf/state (REF-06).
    Exposes /hackrf/cmd subscription and /hackrf/mayhem/* Trigger services (D-16).
    """

    def __init__(self):
        super().__init__('hackrf_bridge_node')
        self.get_logger().info('BridgeNode starting...')

        # Declare ROS2 parameters
        self.declare_parameter('redis_host', 'localhost')
        self.declare_parameter('redis_port', 6379)

        redis_host = self.get_parameter('redis_host').get_parameter_value().string_value
        redis_port = self.get_parameter('redis_port').get_parameter_value().integer_value

        # Connect to Redis (decode_responses=False for float32 bytes compatibility)
        self._redis = None
        self._stop_event = threading.Event()
        if redis is not None:
            try:
                self._redis = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    decode_responses=False,
                )
                self._redis.ping()
                self.get_logger().info(
                    f'BridgeNode connected to Redis at {redis_host}:{redis_port}'
                )
            except Exception as e:
                self.get_logger().warning(
                    f'BridgeNode: Redis unavailable: {e}. Topics will not publish.'
                )
                self._redis = None
        else:
            self.get_logger().warning('BridgeNode: redis package not available.')

        # Create publisher for /hackrf/iq (Float32MultiArray) — same QoS as hackrf_node.py
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.publisher_ = self.create_publisher(Float32MultiArray, '/hackrf/iq', qos_profile)
        self.get_logger().info("BridgeNode: publisher ready on '/hackrf/iq'")

        # Create publisher for /hackrf/state (String/JSON)
        self._state_publisher = self.create_publisher(String, '/hackrf/state', 10)
        self.get_logger().info("BridgeNode: state publisher ready on '/hackrf/state'")

        # Register command and Mayhem proxy services (D-16)
        if self._redis is not None:
            try:
                register_bridge_services(self, self._redis)
                self.get_logger().info('BridgeNode: bridge services registered')
            except Exception as e:
                self.get_logger().warning(f'BridgeNode: service registration failed: {e}')
        else:
            self.get_logger().warning(
                'BridgeNode: Redis unavailable — skipping service registration'
            )

        # Start bridge loop thread
        self._bridge_thread = threading.Thread(
            target=self._bridge_loop,
            daemon=True,
            name='bridge_loop',
        )
        self._bridge_thread.start()
        self.get_logger().info('BridgeNode initialisation complete.')

    def _bridge_loop(self):
        """Subscribe to hackrf:iq:notify with auto-reconnect on Redis errors.

        Outer loop handles reconnection with exponential backoff (1s-30s).
        Inner loop (_run_pubsub_loop) processes messages — raises on Redis error
        to trigger outer reconnect.
        """
        if self._redis is None:
            self.get_logger().warning('BridgeNode._bridge_loop: no Redis connection, exiting.')
            return

        backoff = _MIN_BACKOFF
        while not self._stop_event.is_set():
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                pubsub.subscribe('hackrf:iq:notify')
                self.get_logger().info('BridgeNode: subscribed to hackrf:iq:notify')
                backoff = _MIN_BACKOFF  # reset on successful connect
                self._publish_state()  # flush state immediately after reconnect
                self._run_pubsub_loop(pubsub)
            except Exception as e:
                self.get_logger().warning(
                    f'BridgeNode: Redis error: {e}. Retrying in {backoff:.0f}s.'
                )
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
            if self._stop_event.wait(backoff):
                break  # stop requested during backoff
            backoff = min(backoff * 2, _MAX_BACKOFF)

        self.get_logger().info('BridgeNode: bridge loop exited.')

    def _run_pubsub_loop(self, pubsub):
        """Inner loop — raises on Redis error to trigger outer reconnect."""
        while not self._stop_event.is_set():
            msg = pubsub.get_message(timeout=0.1)  # raises on Redis error
            if msg is None:
                continue
            if msg['type'] != 'message':
                continue

            # Get latest IQ entry from stream
            entries = self._redis.xrevrange('hackrf:iq:stream', '+', '-', count=1)
            if not entries:
                continue

            _entry_id, fields = entries[0]
            float_bytes = fields.get(b'data', b'')
            if not float_bytes:
                continue

            arr = np.frombuffer(float_bytes, dtype=np.float32)
            ros_msg = Float32MultiArray()
            ros_msg.data = arr.tolist()
            self.publisher_.publish(ros_msg)

            # Also update state after each IQ publish
            self._publish_state()

    def _publish_state(self):
        """Read hackrf:state hash and publish JSON to /hackrf/state topic."""
        if self._redis is None:
            return
        try:
            state = self._redis.hgetall('hackrf:state')
        except Exception as e:
            self.get_logger().warning(f'BridgeNode._publish_state: hgetall failed: {e}')
            return

        if not state:
            return

        decoded = {k.decode(): v.decode() for k, v in state.items()}
        msg = String()
        msg.data = json.dumps(decoded)
        self._state_publisher.publish(msg)

    def destroy_node(self):
        """Shut down bridge: signal stop event, close Redis, call super."""
        self.get_logger().info('BridgeNode shutting down...')
        self._stop_event.set()
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                pass
        super().destroy_node()


def main(args=None):
    """Entry point for hackrf_node console script."""
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('KeyboardInterrupt — shutting down BridgeNode.')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
