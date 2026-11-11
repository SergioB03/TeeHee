"""The autonomy planner as a ROS 2 node.

Each observation that arrives on `edge_sentry/observation` is handed to the
`edge-node` program (the same C++ planner that runs on an edge computer) and
its answer is published on `edge_sentry/plan`. The node keeps nothing between
messages, so it can be restarted at any time.
"""
import json
import subprocess

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from edge_sentry_msgs.msg import Observation, Plan

from .wire import observation_to_wire, plan_from_wire


class AutonomyNode(Node):
    def __init__(self):
        super().__init__("edge_sentry_autonomy")
        binary = self.declare_parameter("node", "edge-node").value
        variant = self.declare_parameter("variant", "full").value
        self.publisher = None
        self.planner = subprocess.Popen([binary, "--stdin", "--variant", variant], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, bufsize=1)
        # Subscribe first: the simulator starts once it sees this node's publisher.
        self.create_subscription(Observation, "edge_sentry/observation", self.on_observation, 10)
        self.publisher = self.create_publisher(Plan, "edge_sentry/plan", 10)

    def on_observation(self, msg):
        if self.publisher is None:
            return
        self.planner.stdin.write(json.dumps(observation_to_wire(msg), separators=(",", ":")) + "\n")
        self.planner.stdin.flush()
        reply = self.planner.stdout.readline()
        try:
            data = json.loads(reply)
        except ValueError:
            data = {"ok": False}
        plan = plan_from_wire(data, self.get_clock().now().to_msg(), msg.tick)
        self.publisher.publish(plan)
        for order in plan.orders:
            self.get_logger().info(f"tick {msg.tick}: {order.id} {order.mode}: {order.reason}")

    def destroy_node(self):
        self.planner.terminate()
        super().destroy_node()


def main():
    rclpy.init()
    node = AutonomyNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
