#!/usr/bin/env python3
"""Software-in-the-loop test: the simulator on this machine, the autonomy in a
separate edge-node process reached over TCP.

    python3 sil/run_sil.py                       # start a local edge-node, run, rewrite sil/RESULTS.md
    python3 sil/run_sil.py --check --no-write    # CI: fail if a floor is missed
    python3 sil/run_sil.py --node 192.168.1.50:7700   # a node already running on a Pi or Jetson
    python3 sil/run_sil.py --node-wrapper qemu-aarch64-static --node-bin /tmp/edge-node-arm64
                                                 # an arm64 build under emulation

Standard library only. Every tick the harness asks the simulator for blue's
observation, sends it to the node, and gives the simulator whatever orders
come back in time. It measures:

    agreement   the node's orders against the planner running inside the
                simulator on the same observation (they must be identical)
    latency     how long the node takes to decide, and the full round trip
    deadline    how often the reply misses the tick budget
    bad links   mission value when the link adds delay or loses messages.
                A late or lost reply is not used: units carry on with the
                orders they already have.
"""
import argparse
import json
import os
import platform
import random
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml"))
import simlab  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=40, help="random rollouts (seeds from 500000)")
p.add_argument("--budget-ms", type=float, default=50.0, help="a reply slower than this is late and is not used")
p.add_argument("--node", help="host:port of a node that is already running; default starts one locally")
p.add_argument("--node-bin", default=os.path.join(simlab.ROOT, "native", "build", "edge-node"))
p.add_argument("--node-wrapper", help="program to run the node under, e.g. qemu-aarch64-static")
p.add_argument("--label", help="what the node is running on, for the report")
p.add_argument("--check", action="store_true", help="fail if results fall below the floors")
p.add_argument("--no-write", action="store_true", help="do not rewrite sil/RESULTS.md")
args = p.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
TICKS = 22


class Node:
    """One TCP connection to an edge-node, reconnecting if it drops."""

    def __init__(self, host, port):
        self.addr = (host, port)
        self.conn = None
        self.stream = None

    def connect(self, tries=100):
        for _ in range(tries):
            try:
                self.conn = socket.create_connection(self.addr, timeout=5)
                self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.stream = self.conn.makefile("rw")
                return True
            except OSError:
                time.sleep(0.1)
        return False

    def ask(self, message):
        """Returns (reply, round trip in ms). Reconnects once if the link was closed."""
        line = json.dumps(message, separators=(",", ":")) + "\n"
        for attempt in (0, 1):
            try:
                start = time.perf_counter()
                self.stream.write(line)
                self.stream.flush()
                reply = self.stream.readline()
                if reply:
                    return json.loads(reply), (time.perf_counter() - start) * 1000.0
            except (OSError, ValueError):
                pass
            if attempt == 0 and not self.connect(tries=20):
                break
        return None, float("inf")


def start_local_node():
    if not os.path.exists(args.node_bin):
        raise SystemExit(f"edge-node not found at {args.node_bin}. Build it with: npm run build:native")
    port = 46000 + os.getpid() % 1000
    cmd = ([args.node_wrapper] if args.node_wrapper else []) + [args.node_bin, "--listen", str(port)]
    proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
    return proc, "127.0.0.1", port


def blue_value(world, blue):
    return sum((u["hp"] / 100.0) * u["integrity"] for u in world["units"] if u["id"] in blue and u["alive"]) / len(blue)


def key(order):
    return (order["id"], order["action"], order["target"], order["tx"], order["ty"], order["tz"])


def play(sim, node, units, orders, link, stats, rng):
    """One rollout. link = None plays the planner inside the simulator; otherwise a dict of delay_ms, jitter_ms, drop."""
    state = sim.load(units)
    blue = [u["id"] for u in units if u["team"] == "blue"]
    values = []
    for t in range(TICKS):
        for when, a in orders:
            if when == t:
                res = sim.run(*a)
                if res.get("ok"):
                    state = res
        if link is None:
            sim.run("autopilot")
        else:
            seen = sim.run("observe")["observation"]
            stats["ticks"] += 1
            if rng.random() < link["drop"]:
                stats["lost"] += 1
            else:
                emulated = max(0.0, link["delay_ms"] + rng.uniform(-link["jitter_ms"], link["jitter_ms"]))
                reply, trip = node.ask(seen)
                total = trip + emulated  # the emulated link delay is added to the clock, not slept
                if reply is None or not reply.get("ok"):
                    stats["lost"] += 1
                elif total > args.budget_ms:
                    stats["late"] += 1
                else:
                    stats["trip_ms"].append(trip)
                    stats["plan_us"].append(reply["micros"])
                    if link["perfect"]:
                        inside = sim.run("autopilot", "--dry-run")["orders"]
                        if [key(o) for o in inside] != [key(o) for o in reply["orders"]]:
                            stats["disagree"] += 1
                    for o in reply["orders"]:
                        cmd = ["order", "--id", o["id"], "--action", o["action"]]
                        if o["tx"] >= 0:
                            cmd += ["--tx", o["tx"], "--ty", o["ty"]]
                        if o["tz"] >= 0:
                            cmd += ["--tz", o["tz"]]
                        if o["target"]:
                            cmd += ["--target", o["target"]]
                        sim.run(*cmd)
        state = sim.run("tick")
        values.append(blue_value(state["world"], blue))
    return sum(values) / len(values)


def pct(values, q):
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def run(node, link):
    sim = simlab.Sim()
    rng = random.Random(1234)
    stats = {"ticks": 0, "lost": 0, "late": 0, "disagree": 0, "trip_ms": [], "plan_us": []}
    total = 0.0
    for i in range(args.rollouts):
        units, orders = simlab.fighter_scenario(500000 + i)
        total += play(sim, node, units, orders, link, stats, rng)
    stats["value"] = total / args.rollouts
    return stats


proc = None
if args.node:
    host, port = args.node.rsplit(":", 1)
    port = int(port)
else:
    proc, host, port = start_local_node()
node = Node(host, port)
try:
    if not node.connect():
        raise SystemExit(f"could not reach an edge-node at {host}:{port}")
    pong, _ = node.ask({"ping": 1})
    if not pong or pong.get("pong") != 1:
        raise SystemExit("the program at that address did not answer like an edge-node")

    inside = run(None, None)  # the planner inside the simulator, no link at all
    idle_sim = simlab.Sim()
    idle = 0.0
    for i in range(args.rollouts):
        units, orders = simlab.fighter_scenario(500000 + i)
        state = idle_sim.load(units)
        blue = [u["id"] for u in units if u["team"] == "blue"]
        values = []
        for t in range(TICKS):
            for when, a in orders:
                if when == t:
                    idle_sim.run(*a)
            values.append(blue_value(idle_sim.run("tick")["world"], blue))
        idle += sum(values) / len(values)
    idle /= args.rollouts

    links = [
        ("Direct link", {"delay_ms": 0, "jitter_ms": 0, "drop": 0.0, "perfect": True}),
        ("10 ms delay", {"delay_ms": 10, "jitter_ms": 2, "drop": 0.0, "perfect": False}),
        ("40 ms delay, 20 ms jitter", {"delay_ms": 40, "jitter_ms": 20, "drop": 0.0, "perfect": False}),
        ("10% of messages lost", {"delay_ms": 0, "jitter_ms": 0, "drop": 0.10, "perfect": False}),
        ("30% of messages lost", {"delay_ms": 0, "jitter_ms": 0, "drop": 0.30, "perfect": False}),
        ("60% of messages lost", {"delay_ms": 0, "jitter_ms": 0, "drop": 0.60, "perfect": False}),
        ("Link down (delay over the budget)", {"delay_ms": args.budget_ms * 2, "jitter_ms": 0, "drop": 0.0, "perfect": False}),
    ]
    results = [(name, run(node, link)) for name, link in links]
finally:
    if proc:
        proc.terminate()

direct = results[0][1]
where = args.label or ("a node at " + args.node if args.node else
                       ("an arm64 build under " + os.path.basename(args.node_wrapper) + " emulation" if args.node_wrapper
                        else f"a local process on the development machine ({platform.machine()}, {os.cpu_count()} cores)"))
out = ["# Software-in-the-loop results", "", "Generated by `python3 sil/run_sil.py`. Do not edit by hand.", ""]
out.append(f"The simulator ran here and the autonomy ran in a separate `edge-node` process: {where}, reached over TCP. "
           f"{args.rollouts} random rollouts of {TICKS} ticks; blue has point defense and one fighter. "
           f"A reply slower than the {args.budget_ms:g} ms tick budget is late and is not used.")
out.append("")
out.append("## Agreement and latency (direct link)")
out.append("")
out.append("| Measure | Result |")
out.append("| --- | --- |")
out.append(f"| Ticks planned by the node | {direct['ticks']} |")
out.append(f"| Ticks where the node's orders differed from the planner inside the simulator | {direct['disagree']} |")
out.append(f"| Mission value, node in the loop | {direct['value']:.4f} |")
out.append(f"| Mission value, planner inside the simulator | {inside['value']:.4f} |")
out.append(f"| Planning time in the node: median, 99th percentile, worst | {pct(direct['plan_us'], 0.5):.0f} µs, {pct(direct['plan_us'], 0.99):.0f} µs, {max(direct['plan_us']):.0f} µs |")
out.append(f"| Round trip over TCP: median, 99th percentile, worst | {pct(direct['trip_ms'], 0.5):.2f} ms, {pct(direct['trip_ms'], 0.99):.2f} ms, {max(direct['trip_ms']):.2f} ms |")
out.append(f"| Replies that missed the {args.budget_ms:g} ms budget | {direct['late']} |")
out.append("")
out.append("## When the link gets worse")
out.append("")
out.append("| Link | Mission value | Replies used | Late | Lost |")
out.append("| --- | --- | --- | --- | --- |")
for name, s in results:
    used = s["ticks"] - s["late"] - s["lost"]
    out.append(f"| {name} | {s['value']:.3f} | {used / s['ticks']:.0%} | {s['late']} | {s['lost']} |")
out.append(f"| No autonomy at all | {idle:.3f} | | | |")
out.append("")
out.append("Delay and jitter are added to the measured round trip rather than slept, so the run stays fast; loss is drawn "
           "from a fixed random seed. When a reply is late or lost, units keep the orders they already have, which is "
           "why losing a share of messages costs little: the next reply that arrives catches up.")
out.append("")
report = "\n".join(out)
print(report)
if not args.no_write:
    with open(os.path.join(HERE, "RESULTS.md"), "w") as f:
        f.write(report)

if args.check:
    down = results[-1][1]
    floors = [
        ("the node agrees with the in-simulator planner on every tick", direct["disagree"] == 0),
        ("same mission value with the node in the loop", abs(direct["value"] - inside["value"]) < 1e-9),
        ("no reply misses the tick budget on a direct link", direct["late"] == 0 and direct["lost"] == 0),
        ("99th percentile round trip is under the budget", pct(direct["trip_ms"], 0.99) < args.budget_ms),
        ("with the link down, nothing from the node is used", down["late"] + down["lost"] == down["ticks"]),
        ("autonomy over a link losing 30% of messages still beats no autonomy", results[4][1]["value"] > idle),
    ]
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(ok for _, ok in floors) else 1)
