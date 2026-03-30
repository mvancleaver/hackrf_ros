"""ROS2 service and subscription handlers for BridgeNode.

Provides register_bridge_services(node, redis_client) which attaches:
  - /hackrf/cmd subscription (std_msgs/String): each message is JSON
    command dict RPUSHed to hackrf:cmd Redis list (D-16).
  - /hackrf/mayhem/appstart  (std_srvs/Trigger): queues appstart command
  - /hackrf/mayhem/setfreq   (std_srvs/Trigger): reads setfreq_request from
    hackrf:state and queues setfreq command
  - /hackrf/mayhem/radioinfo (std_srvs/Trigger): queues radioinfo command

All services write to hackrf:cmd Redis list via RPUSH so the hackrf_driver
process can consume them (D-16).
"""

import json

from std_msgs.msg import String
from std_srvs.srv import Trigger


def _make_cmd_handler(node, redis_client):
    """Return a subscription callback that RPUSHes JSON commands to hackrf:cmd."""
    def _handler(msg):
        try:
            cmd = json.loads(msg.data)
        except json.JSONDecodeError as e:
            node.get_logger().warning(
                f'BridgeServices: invalid JSON on /hackrf/cmd: {e}'
            )
            return
        try:
            redis_client.rpush('hackrf:cmd', json.dumps(cmd))
        except Exception as e:
            node.get_logger().warning(
                f'BridgeServices: rpush failed: {e}'
            )
    return _handler


def _make_mayhem_handler(redis_client, cmd_name):
    """Return a Trigger service callback that queues a named Mayhem command."""
    def _handler(request, response):
        try:
            redis_client.rpush('hackrf:cmd', json.dumps({'cmd': cmd_name, 'args': {}}))
            response.success = True
            response.message = f'{cmd_name} queued'
        except Exception as e:
            response.success = False
            response.message = f'{cmd_name} failed: {e}'
        return response
    return _handler


def _make_setfreq_handler(node, redis_client):
    """Return a Trigger service callback that reads setfreq_request from hackrf:state."""
    def _handler(request, response):
        try:
            freq_bytes = redis_client.hget('hackrf:state', 'setfreq_request')
        except Exception as e:
            response.success = False
            response.message = f'setfreq: redis error: {e}'
            return response

        if freq_bytes is None:
            response.success = False
            response.message = 'setfreq_request not set in hackrf:state'
            return response

        try:
            freq = int(freq_bytes.decode() if isinstance(freq_bytes, bytes) else freq_bytes)
        except (ValueError, AttributeError) as e:
            response.success = False
            response.message = f'setfreq: invalid frequency value: {e}'
            return response

        try:
            redis_client.rpush(
                'hackrf:cmd',
                json.dumps({'cmd': 'setfreq', 'args': {'freq_hz': freq}})
            )
            response.success = True
            response.message = 'setfreq queued'
        except Exception as e:
            response.success = False
            response.message = f'setfreq rpush failed: {e}'
        return response
    return _handler


def _make_antenna_confirm_handler(redis_client):
    """Return a Trigger service callback that sets the antenna confirmation key."""
    def _handler(request, response):
        try:
            redis_client.set('hackrf:tx:antenna_confirmed', b'1')
            response.success = True
            response.message = 'Antenna confirmed - TX guard cleared'
        except Exception as e:
            response.success = False
            response.message = f'Antenna confirmation failed: {e}'
        return response
    return _handler


def register_bridge_services(node, redis_client):
    """Attach command subscription and Mayhem proxy services to node.

    Args:
        node: A rclpy Node instance (BridgeNode).
        redis_client: Connected redis.Redis instance.
    """
    # /hackrf/cmd subscription — RPUSHes each valid JSON message to hackrf:cmd
    node.create_subscription(
        String,
        '/hackrf/cmd',
        _make_cmd_handler(node, redis_client),
        10,
    )

    # /hackrf/mayhem/appstart — Trigger service
    node.create_service(
        Trigger,
        '/hackrf/mayhem/appstart',
        _make_mayhem_handler(redis_client, 'appstart'),
    )

    # /hackrf/mayhem/setfreq — Trigger service (reads freq from hackrf:state)
    node.create_service(
        Trigger,
        '/hackrf/mayhem/setfreq',
        _make_setfreq_handler(node, redis_client),
    )

    # /hackrf/mayhem/radioinfo — Trigger service
    node.create_service(
        Trigger,
        '/hackrf/mayhem/radioinfo',
        _make_mayhem_handler(redis_client, 'radioinfo'),
    )

    # /hackrf/confirm_antenna -- Trigger service (TXS-02)
    node.create_service(
        Trigger,
        '/hackrf/confirm_antenna',
        _make_antenna_confirm_handler(redis_client),
    )
