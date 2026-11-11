#!/usr/bin/env python3
"""Run the simulator and the autonomy as separate ROS 2 processes and check the topics between them.

    source /opt/ros/jazzy/setup.bash && source ros2/install/setup.bash
    python3 ros2/check_bridge.py --sim /path/to/edge-sim --node /path/to/edge-node

It starts `sim_node` and `autonomy_node` with `ros2 run`, listens on both
topics from a third node, and checks that every plan answers an observation,
that the plan matches what the planner says for that exact observation, and
that the orders took effect in the simulator.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import rclpy
from rclpy.node import Node

from edge_sentry_bridge.wire import observation_to_wire, order_key
from edge_sentry_msgs.msg import Observation, Plan

p = argparse.ArgumentParser()
p.add_argument("--sim", required=True)
p.add_argument("--node", required=True)
p.add_argument("--ticks", type=int, default=12)
p.add_argument("--period", type=float, default=0.3)
p.add_argument("--timeout", type=float, default=90.0)
args = p.parse_args()

world = f"/tmp/edge-sentry-ros2-{os.getpid()}/world.json"
rclpy.init()
probe = Node("edge_sentry_probe")
observations, plans = {}, {}
probe.create_subscription(Observation, "edge_sentry/observation", lambda m: observations.setdefault(m.tick, m), 50)
probe.create_subscription(Plan, "edge_sentry/plan", lambda m: plans.setdefault(m.tick, m), 50)

procs = [
    subprocess.Popen(["ros2", "run", "edge_sentry_bridge", "autonomy_node", "--ros-args", "-p", f"node:={args.node}"]),
    subprocess.Popen(["ros2", "run", "edge_sentry_bridge", "sim_node", "--ros-args", "-p", f"sim:={args.sim}",
                      "-p", f"world:={world}", "-p", f"period:={args.period}", "-p", f"ticks:={args.ticks}",
                      "-p", "listeners:=2"]),
]
deadline = time.time() + args.timeout
try:
    while time.time() < deadline and procs[1].poll() is None:
        rclpy.spin_once(probe, timeout_sec=0.1)
    for _ in range(20):  # let the last messages arrive
        rclpy.spin_once(probe, timeout_sec=0.05)
finally:
    finished = procs[1].poll() is not None
    for proc in procs:
        proc.terminate()
    probe.destroy_node()
    rclpy.try_shutdown()

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


ticks = sorted(observations)
check("the simulator node ran all of its ticks and stopped", finished)
check("an observation was published on the topic for every tick", ticks == list(range(0, args.ticks + 1)))
check("a plan came back over the topic for every tick that was played", all(t in plans for t in range(0, args.ticks)))
check("every plan answers an observation that was published", all(t in observations for t in plans))
check("every plan reports success and a planning time", all(m.ok and m.planning_micros >= 0 for m in plans.values()))

# The plan on the topic must be what the planner says for that exact observation.
mismatches = 0
for tick, plan in plans.items():
    if tick not in observations:
        continue
    line = json.dumps(observation_to_wire(observations[tick]), separators=(",", ":")) + "\n"
    direct = json.loads(subprocess.run([args.node, "--stdin"], input=line, capture_output=True, text=True).stdout)
    if [order_key(o) for o in direct["orders"]] != [order_key(o) for o in plan.orders]:
        mismatches += 1
check("each plan matches the planner run directly on the same observation", mismatches == 0)

ordered = {o.id for plan in plans.values() for o in plan.orders}
check("the plans carry orders for blue units only", len(ordered) >= 1 and all(i.startswith("B-") for i in ordered))
check("the orders took effect: headquarters is defending in a later observation",
      any(u.id == "B-HQ" and u.action == "defend" for m in observations.values() for u in m.own))
log = json.loads(subprocess.run([args.sim, "--world", world, "decisions", "--n", "200"], capture_output=True, text=True).stdout)
check("the simulator logged the orders as coming from ROS 2 with their reasons",
      any(d["actor"] == "ros2" and d["reason"] for d in log.get("decisions", [])))

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
