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


def sim(*args, scorer=None):
    """Run one CLI command. Returns (exit_code, parsed_json_or_None)."""
    extra = ["--scorer", scorer] if scorer else []
    p = subprocess.run([SIM, "--world", WORLD, *extra, *args], capture_output=True, text=True)
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
# The heuristic reads the missile's real target, so it can call it at tick 0.
code, heur = sim("status", scorer="heuristic")
tracks = {t["sourceUnitId"]: t for t in heur["world"]["tracks"]}
check("heuristic: homing missile track is hostile", tracks["R-MSL1"]["threatLabel"] == "hostile")
check("heuristic: idle jet track is not hostile", tracks["R-JET1"]["threatLabel"] != "hostile")
check("heuristic: jammer track is at least watch", tracks["R-J1"]["threatLabel"] in ("watch", "hostile"))
check("heuristic feature vector includes closingOnBlue",
      len(tracks["R-MSL1"].get("features") or []) == 6 and tracks["R-MSL1"]["features"][-1] == 1.0)
check("heuristic keeps its fixed 0.33 / 0.66 cut-offs",
      tracks["R-MSL1"]["scorer"] == "heuristic" and tracks["R-MSL1"]["hostileAt"] == 0.66)

# --- learned scorer (default): observable inputs only -------------------------
tracks = {t["sourceUnitId"]: t for t in out["world"]["tracks"]}
check("learned scorer is the default", all(t["scorer"] == "learned" for t in tracks.values()))
names = json.loads(subprocess.run([SIM, "inputs"], capture_output=True, text=True).stdout)["inputs"]
check("`inputs` lists the model's input names, history included",
      len(names) == 24 and "closing" in names and "stopTicks" in names and "heading2" in names)
check("learned: observed vector matches the input list, all values in 0..1",
      all(len(t["observed"]) == len(names) and all(0 <= v <= 1 for v in t["observed"]) for t in tracks.values()))
check("learned: both scores are reported for comparison",
      all("heuristicScore" in t and "learnedScore" in t for t in tracks.values()))
check("learned: jammer covering the relay is at least watch", tracks["R-J1"]["threatLabel"] in ("watch", "hostile"))
check("learned: idle jet is not hostile", tracks["R-JET1"]["threatLabel"] != "hostile")
check("learned: nothing has moved yet, so the missile is not yet hostile",
      tracks["R-MSL1"]["threatLabel"] != "hostile" and tracks["R-MSL1"]["vx"] == 0)

code, out = sim("tick", "--n", "3")
code, out = sim("status")  # a separate process: velocity must survive the reload
msl = next(t for t in out["world"]["tracks"] if t["sourceUnitId"] == "R-MSL1")
check("observed velocity persists across CLI calls", (msl["vx"], msl["vy"]) != (0, 0))
check("learned: homing missile is hostile by tick 3, three ticks before impact", msl["threatLabel"] == "hostile")
jet = next(t for t in out["world"]["tracks"] if t["sourceUnitId"] == "R-JET1")
check("learned: idle jet is still not hostile at tick 3", jet["threatLabel"] != "hostile")
rejected("unknown --scorer is rejected", "--scorer", "magic", "status", contains="--scorer")

code, out = sim("tick", "--n", "3")
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

# --- track history ------------------------------------------------------------
sim("reset")
sim("order", "--id", "R-JET1", "--action", "move", "--tx", "8", "--ty", "2")
sim("tick", "--n", "5")
code, out = sim("status")
jet = unit(out["world"], "R-JET1")
check("history: a contact that moved then stopped remembers both",
      jet["moved"] == 1 and jet["stopTicks"] == 3)
jet_track = next(t for t in out["world"]["tracks"] if t["sourceUnitId"] == "R-JET1")
check("history: stopTicks reaches the model inputs",
      abs(jet_track["observed"][names.index("stopTicks")] - 3 / 6) < 1e-6)

# --- HQ point defense ---------------------------------------------------------
sim("reset")
rejected("only an HQ can defend", "order", "--id", "B-S1", "--action", "defend", "--target", "R-MSL1", contains="hq")
rejected("defend needs an opposing target", "order", "--id", "B-HQ", "--action", "defend", "--target", "B-S1", contains="opposing")
code, out = sim("order", "--id", "B-HQ", "--action", "defend", "--target", "R-MSL1")
check("HQ accepts a defend order", code == 0 and unit(out["world"], "B-HQ")["order"]["action"] == "defend")
code, out = sim("tick", "--n", "6")
kinds = [e["kind"] for e in out["world"]["events"]]
check("a defended missile is blocked and HQ takes no damage",
      "blocked" in kinds and "engage" not in kinds and unit(out["world"], "B-HQ")["hp"] == 100)
sim("reset")
code, out = sim("order", "--id", "B-HQ", "--action", "defend", "--target", "R-J1")
code, out = sim("tick")
check("a defended jammer's field is suppressed", unit(out["world"], "B-R1")["integrity"] == 1)

# --- decision log -------------------------------------------------------------
def decisions(n=50):
    return sim("decisions", "--n", str(n))[1]["decisions"]

before = len(decisions(500))
sim("reset")
sim("tick", "--n", "2")
sim("order", "--id", "B-HQ", "--action", "defend", "--target", "R-MSL1", "--actor", "agent", "--reason", "Missile is closing on HQ")
sim("order", "--id", "B-S1", "--action", "jam")
log = decisions(500)
check("reset, accepted and refused orders are all logged; ticks are not", len(log) == before + 3)
entry = log[-2]
check("log entry records actor, reason, order and outcome",
      entry["actor"] == "agent" and entry["reason"] == "Missile is closing on HQ" and entry["ok"] is True
      and entry["args"] == {"id": "B-HQ", "action": "defend", "target": "R-MSL1"} and entry["tick"] == 2)
check("log entry carries the threat picture the decision was made on",
      {p["id"] for p in entry["picture"]} == {"R-J1", "R-JET1", "R-MSL1"} and "closing" in entry["picture"][0]["inputs"])
check("refused orders are logged with the simulator's reason",
      log[-1]["ok"] is False and "jammers" in log[-1]["error"])
sim("order", "--id", "B-HQ", "--action", "cease", "--reason", "--no-log")
check("a reason that looks like a flag is kept as text and cannot switch logging off",
      decisions(1)[0]["reason"] == "--no-log")
sim("order", "--id", "B-HQ", "--action", "cease", "--no-log")
check("--no-log skips the log (used for bulk rollouts)", len(decisions(500)) == before + 4)
sim("order", "--id", "B-HQ", "--action", "cease", "--reason", 'line one\nwith "quotes" and a \\ backslash')
check("the log stays valid JSON with quotes and newlines in a reason", decisions(1)[0]["reason"].startswith("line one"))
rejected("bad --actor is rejected", "--actor", "not a name!", "status", contains="--actor")

# --- play mode: skirmish scenario and the computer opponent -------------------
code, out = sim("reset", "--scenario", "skirmish")
w = out["world"]
check("skirmish scenario starts with 4 blue and 5 red units and no orders",
      sum(u["team"] == "blue" for u in w["units"]) == 4 and sum(u["team"] == "red" for u in w["units"]) == 5
      and all(u["order"]["action"] == "none" for u in w["units"]))
rejected("unknown scenario is rejected", "reset", "--scenario", "nope", contains="scenario")
rejected("opponent needs a side", "opponent", contains="--side")
rejected("opponent rejects a bad side", "opponent", "--side", "green", contains="--side")
before = len(decisions(500))
code, out = sim("opponent", "--side", "red")
orders = {o["id"]: o for o in out["orders"]}
check("red opponent sends both missiles at HQ and each jet at a blue unit",
      orders["R-MSL1"]["target"] == "B-HQ" and orders["R-MSL2"]["target"] == "B-HQ"
      and orders["R-JET1"]["action"] == "launch" and orders["R-JET2"]["action"] == "launch"
      and all(o["ok"] for o in out["orders"]))
check("red opponent never orders blue units", all(i.startswith("R-") for i in orders))
log = decisions(500)
check("each opponent order is logged with actor 'opponent' and a reason",
      len(log) == before + len(out["orders"]) and all(d["actor"] == "opponent" and d["reason"] for d in log[before:]))
blocked = hits = 0
for _ in range(12):
    code, out = sim("opponent", "--side", "blue", "--no-log")
    check_orders = out["orders"]
    code, out = sim("tick")
    for e in out["world"]["events"]:
        if e["tick"] == out["world"]["tick"]:
            blocked += e["kind"] == "blocked"
            hits += e["kind"] == "engage" and "B-HQ" in e["detail"]
check("blue opponent only ever orders its own HQ", all(o["id"] == "B-HQ" for o in check_orders))
check("blue's model blocks at least one strike on HQ in the skirmish", blocked >= 1)
sim("reset")

# --- sequence model: hidden state carried from tick to tick --------------------
def seq_scores(world):
    return [round(t["sequenceScore"], 9) for t in world["tracks"]]

sim("reset")
code, out = sim("tick", "--n", "4")
in_one_call = seq_scores(out["world"])
sim("reset")
for _ in range(3):
    sim("tick")
    sim("status")                                             # extra reads and orders in between
    sim("order", "--id", "B-HQ", "--action", "cease", "--no-log")   # must not advance the memory
code, out = sim("tick")
check("sequence score is the same for 4 ticks at once and 4 single ticks with reads in between",
      seq_scores(out["world"]) == in_one_call and len(in_one_call) == 3)
code, out = sim("--scorer", "sequence", "status")
check("--scorer sequence labels tracks from the sequence score",
      all(t["scorer"] == "sequence" and abs(t["threatScore"] - t["sequenceScore"]) < 1e-5 for t in out["world"]["tracks"]))
sim("reset")

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
