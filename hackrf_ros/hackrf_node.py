#!/usr/bin/env python3

import numpy as np
import rclpy
import time
from rclpy.node import Node
from hackrf import HackRF
from hackrf_ros.msg import HackRfRx, HackRfConfig
from rclpy.executors import SingleThreadedExecutor
import matplotlib.pyplot as plt


class HackRfNode(Node):
    def __init__(self):
        super().__init__('hackrf_node')

        # self.declare_parameter('timer_period', 0.5)
        # self.declare_parameter('sample_rate', 20e6)
        # self.declare_parameter('center_freq', 88.5e6)
        # self.declare_parameter('num_samples', 2e6)

        # self.timer_period = self.get_parameter('timer_period').get_parameter_value().
        # self.sample_rate = self.get_parameter('sample_rate').get_parameter_value()
        # self.center_freq = self.get_parameter('center_freq').get_parameter_value()
        # self.num_samples = self.get_parameter('num_samples').get_parameter_value()
        self.frame_id = "hackrf_antenna"
        self.timer_period = 0.5
        self.sample_rate = 20e6
        self.center_freq = 88.5e6
        self.num_samples = 1e6

        # Create a subscriber to for configuration updates
        self.hackrf_config_sub = self.create_subscription(HackRfConfig, 'hackrf_config', self.hackrf_config_callback, 10)
        self.hackrf_config_sub

        # Create a publisher to publish hackrf data
        self.timer = self.create_timer(self.timer_period, self.hackrf_callback)
        self.hackrf_pub = self.create_publisher(HackRfRx, 'hackrf_output', 10)

        self.hackrf_hw = HackRF()
        self.hackrf_hw.sample_rate = self.sample_rate
        self.hackrf_hw.center_freq = self.center_freq
        

    def hackrf_config_callback(self, msg):
        # Update Variables from msg
        self.sample_rate = msg.sample_rate
        self.center_freq = msg.center_freq
        self.num_samples = msg.num_samples
        # update settings on connected Hackrf
        self.hackrf_hw.sample_rate = self.sample_rate
        self.hackrf_hw.center_freq = self.center_freq

    def hackrf_callback(self):
        self.hackrf_hw.sample_rate = self.sample_rate
        self.hackrf_hw.center_freq = self.center_freq
        samples = self.hackrf_hw.read_samples(self.num_samples)
        hackrf_rx_msg = HackRfRx()
        now = self.get_clock().now()
        hackrf_rx_msg.header.stamp = now.to_msg()
        hackrf_rx_msg.header.frame_id = self.frame_id
        hackrf_rx_msg.sample_rate = self.sample_rate
        hackrf_rx_msg.center_freq = self.center_freq
        hackrf_rx_msg.num_samples = self.num_samples
        hackrf_rx_msg.i.extend(samples.real)
        hackrf_rx_msg.q.extend(samples.imag)
        print("Real: ", samples.real )
        print("Imag: ", samples.imag )
        self.hackrf_pub.publish(hackrf_rx_msg)

    def disable_hackrf(self):
        # Cleanup and Destory Connection
        self.hackrf_hw.stop_rx()
        print('Disabled hackrf...')

    def destroy_node(self):
        self.disable_hackrf()
        super().destroy_node()

def main():
    rclpy.init()
    print('Hi from hackrf_ros.')
    try:
        hackrf_node = HackRfNode()
        rclpy.spin(hackrf_node)
        # executor = SingleThreadedExecutor()
        # executor.add_node(hackrf_node)
        # try:
        #     executor.spin()
        # finally:
        #     executor.shutdown()
        #     hackrf_node.destroy_node()
    except KeyboardInterrupt:
        hackrf_node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()