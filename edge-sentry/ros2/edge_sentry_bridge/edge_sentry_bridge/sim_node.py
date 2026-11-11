"""The simulator as a ROS 2 node.

Publishes blue's observation on `edge_sentry/observation` once per tick and
listens for a plan on `edge_sentry/plan`. A plan is used only if it answers
the tick that is current when the next step is taken; an older one is stale
and is dropped, exactly as docs/INTERFACE.md requires.
"""
import json
import os
import subprocess

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from edge_sentry_msgs.msg import Observation, Plan

from .wire import KEPT, observation_from_wire


class SimNode(Node):
    def __init__(self):
        super().__init__("edge_sentry_sim")
        self.sim = self.declare_parameter("sim", "edge-sim").value
        self.world = self.declare_parameter("world", "/tmp/edge-sentry-ros2/world.json").value
        self.period = float(self.declare_parameter("period", 0.25).value)
        self.ticks = int(self.declare_parameter("ticks", 0).value)  # 0 = run until stopped
        self.scenario = self.declare_parameter("scenario", "skirmish").value
        self.red_ai = bool(self.declare_parameter("red_ai", True).value)
        self.listeners = int(self.declare_parameter("listeners", 1).value)  # subscribers to wait for before tick 0
        os.makedirs(os.path.dirname(self.world), exist_ok=True)

        self.publisher = self.create_publisher(Observation, "edge_sentry/observation", KEPT)
        self.create_subscription(Plan, "edge_sentry/plan", self.on_plan, 10)
        self.pending = None   # the plan for the current tick, if one has arrived
        self.tick = -1        # tick of the observation last published
        self.done = 0
        self.used = 0
        self.stale = 0
        self.ready = 0
        self.started = False
        self.finished = False
        self.run("reset", "--scenario", self.scenario)
        self.timer = self.create_timer(self.period, self.step)

    def run(self, *args):
        out = subprocess.run([self.sim, "--world", self.world, *[str(a) for a in args]], capture_output=True, text=True).stdout
        try:
            return json.loads(out)
        except ValueError:
            return {"ok": False, "error": "unreadable simulator output"}

    def publish(self):
        seen = self.run("observe").get("observation")
        if not seen:
            self.get_logger().error("the simulator returned no observation")
            return
        self.tick = int(seen["tick"])
        self.publisher.publish(observation_from_wire(seen, self.get_clock().now().to_msg()))

    def on_plan(self, msg):
        if msg.ok and msg.tick == self.tick:
            self.pending = msg
        else:
            self.stale += 1  # late, or for a tick that has already been played

    def step(self):
        if self.finished:
            return
        if not self.started:
            # Hold the first observation until an autonomy node is listening for it.
            if (self.count_subscribers("edge_sentry/observation") < self.listeners
                    or self.count_publishers("edge_sentry/plan") == 0):
                self.ready = 0
                return
            self.ready += 1
            if self.ready < 3:  # give discovery a moment to finish in both directions
                return
            self.started = True
            self.publish()
            return
        if self.pending is not None:
            for order in self.pending.orders:
                cmd = ["order", "--id", order.id, "--action", order.action, "--actor", "ros2",
                       "--reason", f"{order.mode}: {order.reason}"]
                if order.tx >= 0:
                    cmd += ["--tx", order.tx, "--ty", order.ty]
                if order.tz >= 0:
                    cmd += ["--tz", order.tz]
                if order.target:
                    cmd += ["--target", order.target]
                self.run(*cmd)
            self.used += 1
            self.pending = None
        if self.red_ai:
            self.run("opponent", "--side", "red")
        self.run("tick")
        self.done += 1
        self.publish()
        if self.ticks and self.done >= self.ticks:
            self.get_logger().info(f"finished {self.done} ticks: {self.used} plans used, {self.stale} dropped as stale")
            self.finished = True


def main():
    rclpy.init()
    node = SimNode()
    try:
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
