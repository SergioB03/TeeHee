#!/usr/bin/env python3
"""Build the Docker images and check them the way they are meant to run.

    python3 docker/check.py           # console, planner node, software-in-the-loop across two containers
    python3 docker/check.py --ros2    # the ROS 2 image instead (a large download)

Standard library only; needs Docker with the compose plugin. It uses its own
compose project name and spare ports, so it does not disturb a stack that is
already running, and it removes everything it started.
"""
import argparse
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = "edge-sentry-check"

p = argparse.ArgumentParser()
p.add_argument("--ros2", action="store_true", help="check the ROS 2 image instead of the main stack")
p.add_argument("--no-build", action="store_true", help="use images that are already built")
args = p.parse_args()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


CONSOLE_PORT, NODE_PORT = free_port(), free_port()
ENV = {**os.environ, "CONSOLE_PORT": str(CONSOLE_PORT), "NODE_PORT": str(NODE_PORT)}
failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or not detail else "\n      " + str(detail)[-600:]), flush=True)
    if not ok:
        failures.append(name)


def sh(*cmd, timeout=900, stdin=None):
    """Run a command. Returns (exit code, stdout and stderr together); 124 if it ran out of time."""
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=ENV, input=stdin, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).decode(errors="replace")
    except subprocess.TimeoutExpired as e:
        return 124, ((e.stdout or b"") + (e.stderr or b"")).decode(errors="replace") + "\n(timed out)"


def compose(*a, timeout=900):
    return sh("docker", "compose", "-p", PROJECT, "-f", "compose.yaml", *a, timeout=timeout)


def inspect(name):
    code, out = sh("docker", "inspect", name)
    return json.loads(out)[0] if code == 0 else {}


def container(service):
    return compose("ps", "-q", service)[1].strip().splitlines()[0]


def http(route, body=None, session="dockercheck00001"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{CONSOLE_PORT}{route}", data=data,
                                 headers={"content-type": "application/json", "x-session": session})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError as e:
        return 0, str(e)


def planner(message):
    with socket.create_connection(("127.0.0.1", NODE_PORT), timeout=5) as conn:
        stream = conn.makefile("rw")
        stream.write(json.dumps(message) + "\n")
        stream.flush()
        return json.loads(stream.readline())


def process_status(pid):
    """Uid, capabilities and no-new-privileges of a container's main process, as the kernel reports them.
    None where the containers run inside a virtual machine (Docker Desktop) and are not visible here."""
    try:
        fields = dict(line.split(":\t", 1) for line in open(f"/proc/{pid}/status").read().splitlines() if ":\t" in line)
        return {"uid": int(fields["Uid"].split()[1]), "caps": int(fields["CapEff"], 16), "nnp": fields.get("NoNewPrivs", "").strip()}
    except (OSError, KeyError, ValueError):
        return None


def non_root(image):
    user = inspect(image).get("Config", {}).get("User", "")
    return user not in ("", "0", "root") and not user.startswith("0:") and not user.startswith("root:")


def stack():
    compose("down", "-v", "--remove-orphans")
    if not args.no_build:
        code, out = compose("--profile", "sil", "build", "console", "edge-node", "sil", timeout=1800)
        check("the console, planner and tools images build", code == 0, out)
        if code != 0:
            return

    # --- what is in the images ----------------------------------------------------
    code, cid = sh("docker", "create", "edge-sentry-node")
    files = {}
    if code == 0:
        export = subprocess.run(["docker", "export", cid.strip()], capture_output=True).stdout
        sh("docker", "rm", cid.strip())
        with tarfile.open(fileobj=io.BytesIO(export)) as tar:
            files = {m.name: m.size for m in tar.getmembers() if m.isfile() and m.size > 0}
    check("the planner image holds one file, the planner, and it is under 5 MB",
          list(files) == ["edge-node"] and files["edge-node"] < 5 * 1024 * 1024, files)
    check("no image runs as root", all(non_root(i) for i in ("edge-sentry-console", "edge-sentry-node", "edge-sentry-tools")),
          {i: inspect(i).get("Config", {}).get("User") for i in ("edge-sentry-console", "edge-sentry-node", "edge-sentry-tools")})

    # --- start the console and the planner ---------------------------------------
    code, out = compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "console", "edge-node", timeout=300)
    check("the console and the planner start and report healthy", code == 0, out)
    if code != 0:
        print(compose("logs", "--tail", "40")[1])
        return
    code, page = http("/")
    tick_code, tick = http("/api/tick", {"n": 3})
    check("the console answers on its published port",
          code == 200 and "edge-sentry console" in page and tick_code == 200 and json.loads(tick)["world"]["tick"] == 3, (code, tick[:200]))
    node = inspect(container("edge-node"))
    console = inspect(container("console"))
    bound = (node.get("NetworkSettings", {}).get("Ports", {}).get("7700/tcp") or [{}])[0].get("HostIp")
    try:
        pong = planner({"ping": 1})
        plan = planner({"v": 1, "tick": 4, "width": 12, "height": 12, "ceiling": 5, "watchAt": 0.24,
                        "own": [{"id": "B-HQ", "type": "hq", "x": 2, "y": 2, "z": 0, "hp": 100, "act": "none", "tgt": "", "tx": -1, "ty": -1, "tz": -1}],
                        "contacts": [{"id": "R-JET1", "cls": "jet", "x": 3, "y": 2, "z": 2, "vx": 0, "vy": 0, "vz": 0, "snr": 0.6,
                                      "locked": 1, "score": 0.6, "jam": 0, "hit": ""}]})
    except (OSError, ValueError) as e:
        pong, plan = {}, {"error": str(e)}
    check("the planner answers on its published port, which is open to this machine only",
          pong.get("pong") == 1 and plan.get("ok") is True and plan["orders"][0]["target"] == "R-JET1" and bound == "127.0.0.1", (pong, plan, bound))

    # --- how they are confined ----------------------------------------------------
    confined = True
    seen = {}
    for name, info in (("console", console), ("edge-node", node)):
        host = info.get("HostConfig", {})
        by_config = (host.get("ReadonlyRootfs") is True and [c.upper() for c in host.get("CapDrop") or []] == ["ALL"]
                     and any("no-new-privileges" in o for o in host.get("SecurityOpt") or []))
        status = process_status(info.get("State", {}).get("Pid", 0))
        by_kernel = status is None or (status["uid"] != 0 and status["caps"] == 0 and status["nnp"] == "1")
        seen[name] = {"config": by_config, "kernel": status}
        confined = confined and by_config and by_kernel
    check("each container runs as a non-root user with no capabilities, on a read-only filesystem", confined, seen)
    cid = container("console")
    refused, _ = sh("docker", "exec", cid, "node", "-e", "require('fs').writeFileSync('/app/probe', '1')")
    allowed, _ = sh("docker", "exec", cid, "node", "-e", "require('fs').writeFileSync('/data/probe', '1')")
    check("the console can write to its data volume and nowhere else in its image", refused != 0 and allowed == 0, (refused, allowed))
    limits = node.get("HostConfig", {})
    check("the planner container is held to half a CPU core and 32 MB of memory",
          limits.get("NanoCpus") == 500_000_000 and limits.get("Memory") == 32 * 1024 * 1024, (limits.get("NanoCpus"), limits.get("Memory")))

    # --- the data volume ----------------------------------------------------------
    code, out = compose("up", "-d", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "120", "console", timeout=300)
    _, state = http("/api/state")
    try:
        kept = json.loads(state)["world"]["tick"]
    except (ValueError, KeyError):
        kept = state[:200]
    check("worlds survive the console container being replaced", code == 0 and kept == 3 and container("console") != cid, kept)

    # --- software-in-the-loop across two containers ------------------------------
    code, out = compose("run", "--rm", "--no-deps", "sil", timeout=900)
    for line in out.splitlines():
        if line.startswith("| Planning time") or line.startswith("| Round trip") or line.startswith("| Replies that missed"):
            print("      " + line)
    check("software-in-the-loop passes with the simulator and the planner in separate containers",
          code == 0 and "PASS  the node agrees with the in-simulator planner on every tick" in out and "FAIL" not in out, out)
    code_cli, out_cli = compose("run", "--rm", "--no-deps", "sil", "python3", "native/tests/test_cli.py", timeout=600)
    code_node, out_node = compose("run", "--rm", "--no-deps", "sil", "python3", "native/tests/test_planner.py", timeout=600)
    check("the simulator's and the planner's own test suites pass inside the tools image",
          code_cli == 0 and code_node == 0 and "all checks passed" in out_cli and "all checks passed" in out_node, out_cli[-300:] + out_node[-300:])

    # --- stopping -----------------------------------------------------------------
    ids = [container("console"), container("edge-node")]
    start = time.time()
    compose("stop", "-t", "10", "console", "edge-node")
    took = time.time() - start
    codes = [inspect(i).get("State", {}).get("ExitCode") for i in ids]
    check("asked to stop, both containers exit within three seconds with status 0", took < 3.0 and codes == [0, 0], (round(took, 1), codes))


def ros2():
    image = "edge-sentry-ros2"
    if not args.no_build:
        code, out = compose("--profile", "ros2", "build", "ros2", timeout=2400)
        check("the ROS 2 image builds", code == 0, out)
        if code != 0:
            return
    check("the ROS 2 image does not run as root", non_root(image), inspect(image).get("Config", {}).get("User"))
    code, out = sh("docker", "run", "--rm", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", image,
                   "ros2", "launch", "edge_sentry_bridge", "demo.launch.py", "ticks:=8", "period:=0.3", timeout=240)
    print(f"      the demo launch exited with status {code}")
    check("the ROS 2 demo plays its ticks over the topics and then stops by itself",
          code != 124 and "finished 8 ticks" in out, out)
    check("the demo prints each order with its behavior and reason", "DEFEND: cover" in out, out)
    code, out = sh("docker", "run", "--rm", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", image,
                   "python3", "ros2/check_bridge.py", "--sim", "/usr/local/bin/edge-sim", "--node", "/usr/local/bin/edge-node", timeout=300)
    for line in out.splitlines():
        if line.startswith(("PASS", "FAIL")):
            print("      " + line)
    check("the bridge check passes inside the ROS 2 image", code == 0 and "all checks passed" in out, out)


try:
    ros2() if args.ros2 else stack()
finally:
    compose("--profile", "sil", "--profile", "ros2", "down", "-v", "--remove-orphans")

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
