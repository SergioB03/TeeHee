#!/usr/bin/env python3
"""Regression checks for the edge-sim CLI (stdlib only).

Usage:
    npm run build:native
    python3 native/tests/test_cli.py            # uses native/build/edge-sim
    python3 native/tests/test_cli.py <path-to-edge-sim>
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "build", "edge-sim")
WORLD = os.path.join(tempfile.mkdtemp(prefix="edge-sim-test-"), "world.json")

failures = []


def sim(*args):
    """Run one CLI command. Returns (exit_code, parsed_json_or_None)."""
    p = subprocess.run([SIM, "--world", WORLD, *args], capture_output=True, text=True)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, None


def unit(world, uid):
    return next(u for u in world["units"] if u["id"] == uid)


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        failures.append(name)


def rejected(name, *args, contains=""):
    code, out = sim(*args)
    ok = code == 1 and out is not None and out.get("ok") is False and contains in out.get("error", "")
    check(name, ok)


# --- basics -----------------------------------------------------------------
code, out = sim("reset")
check("reset returns ok JSON", code == 0 and out and out["ok"] and out["world"]["tick"] == 0)
check("default scenario has 7 units and 3 red tracks",
      len(out["world"]["units"]) == 7 and len(out["world"]["tracks"]) == 3)

# --- default EW + AIML labels (REVIEW_FINDINGS #6 / #7) -----------------------
relay = unit(out["world"], "B-R1")
check("default jammer degrades B-R1 integrity", relay["integrity"] < 0.95)
tracks = {t["sourceUnitId"]: t for t in out["world"]["tracks"]}
check("homing missile track is hostile", tracks["R-MSL1"]["threatLabel"] == "hostile")
check("idle jet track is not hostile", tracks["R-JET1"]["threatLabel"] != "hostile")
check("jammer track is at least watch", tracks["R-J1"]["threatLabel"] in ("watch", "hostile"))
check("AIML feature vector includes closingOnBlue",
      len(tracks["R-MSL1"].get("features") or []) == 6 and tracks["R-MSL1"]["features"][-1] == 1.0)

code, out = sim("tick", "--n", "6")
check("missile reaches and hits B-HQ by tick 6", unit(out["world"], "B-HQ")["hp"] == 60)

# --- event log survives across separate CLI calls -----------------------------
code, out = sim("status")
kinds = [e["kind"] for e in out["world"]["events"]]
check("events persist between calls (engage still listed after a later status)",
      "engage" in kinds and "expended" in kinds)

# --- cease really stops an attack ---------------------------------------------
sim("reset")
sim("order", "--id", "R-JET1", "--action", "launch", "--target", "B-R1")
sim("tick", "--n", "6")
code, out = sim("order", "--id", "R-JET1", "--action", "cease")
hp_at_cease = unit(out["world"], "B-R1")["hp"]
check("cease clears the jet's target", unit(out["world"], "R-JET1")["targetId"] == "")
code, out = sim("tick", "--n", "20")
check("target takes no further damage after cease", unit(out["world"], "B-R1")["hp"] == hp_at_cease)

# --- launch order keeps its target across reloads -----------------------------
sim("reset")
sim("order", "--id", "R-JET1", "--action", "launch", "--target", "B-S1")
code, out = sim("status")
check("order.targetId is restored from world.json",
      unit(out["world"], "R-JET1")["order"]["targetId"] == "B-S1")

# --- bad input returns a JSON error instead of crashing -----------------------
sim("reset")
rejected("spawn without --x/--y is rejected", "spawn", "--type", "jet", "--team", "red", contains="--x/--y")
rejected("spawn with non-numeric --x is rejected", "spawn", "--type", "jet", "--team", "red", "--x", "abc", "--y", "1")
rejected("tick --n abc is rejected", "tick", "--n", "abc", contains="--n")
rejected("tick --n overflow is rejected", "tick", "--n", "99999999999", contains="--n")
rejected("tick --n 0 is rejected", "tick", "--n", "0", contains="--n")
rejected("order with non-numeric --tx is rejected", "order", "--id", "R-JET1", "--action", "move", "--tx", "x", "--ty", "1")

# --- invalid orders are rejected ----------------------------------------------
rejected("launch at unknown target is rejected", "order", "--id", "R-JET1", "--action", "launch", "--target", "GHOST", contains="target")
rejected("launch with no target is rejected", "order", "--id", "R-JET1", "--action", "launch", contains="target")
rejected("launch at self is rejected", "order", "--id", "R-JET1", "--action", "launch", "--target", "R-JET1", contains="itself")
rejected("move without coordinates is rejected", "order", "--id", "R-JET1", "--action", "move", contains="inside the map")
rejected("move off the map is rejected", "order", "--id", "R-JET1", "--action", "move", "--tx", "50", "--ty", "50", contains="inside the map")

code, out = sim("status")
check("rejected commands leave the world unchanged",
      unit(out["world"], "R-JET1")["order"]["action"] == "none" and out["world"]["tick"] == 0)

# --- valid orders still work --------------------------------------------------
sim("order", "--id", "R-JET1", "--action", "move", "--tx", "5", "--ty", "5")
code, out = sim("tick", "--n", "5")
jet = unit(out["world"], "R-JET1")
check("move order arrives and clears", (jet["x"], jet["y"]) == (5, 5) and jet["order"]["action"] == "none")
code, out = sim("order", "--id", "R-MSL1", "--action", "launch")
check("launch with an existing standing target is accepted", code == 0 and out is not None and out["ok"])

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
