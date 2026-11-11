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
      len(names) == 28 and "closing" in names and "stopTicks" in names and "heading2" in names
      and "altitude" in names and "descent" in names and "altDrop3" in names)
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
check("skirmish scenario starts with 5 blue and 7 red units and no orders",
      sum(u["team"] == "blue" for u in w["units"]) == 5 and sum(u["team"] == "red" for u in w["units"]) == 7
      and all(u["order"]["action"] == "none" for u in w["units"]))
check("skirmish has a stealth fighter on each side, a bomber and a drone",
      unit(w, "B-F1")["type"] == "stealth" and unit(w, "R-F1")["type"] == "stealth"
      and unit(w, "R-BMR1")["type"] == "bomber" and unit(w, "R-DRN1")["type"] == "drone")
rejected("unknown scenario is rejected", "reset", "--scenario", "nope", contains="scenario")
rejected("opponent needs a side", "opponent", contains="--side")
rejected("opponent rejects a bad side", "opponent", "--side", "green", contains="--side")
before = len(decisions(500))
code, out = sim("opponent", "--side", "red", "--doctrine", "basic", "--dry-run")
orders = {o["id"]: o for o in out["orders"]}
check("the original red script sends missiles and the bomber at HQ and the other aircraft at ground units, all at once",
      orders["R-MSL1"]["target"] == "B-HQ" and orders["R-MSL2"]["target"] == "B-HQ" and orders["R-BMR1"]["target"] == "B-HQ"
      and all(orders[i]["action"] == "launch" and orders[i]["target"] in ("B-S1", "B-S2", "B-R1") for i in ("R-JET1", "R-F1", "R-DRN1")))
rejected("opponent rejects an unknown doctrine", "opponent", "--side", "red", "--doctrine", "reckless", contains="--doctrine")
code, out = sim("opponent", "--side", "red")
orders = {o["id"]: o for o in out["orders"]}
check("red's coordinated doctrine sends the stealth fighter after blue's fighter and the jammer toward headquarters",
      orders["R-F1"]["action"] == "launch" and orders["R-F1"]["target"] == "B-F1"
      and orders["R-J1"]["action"] == "move" and all(o["ok"] for o in out["orders"]))
check("faster attackers hold low instead of arriving alone",
      any(o["action"] == "orbit" and "arrives together" in o["reason"] for o in out["orders"])
      and sum(o["action"] == "launch" and o["target"].startswith("B-") and o["id"] != "R-F1" for o in out["orders"]) >= 1)
check("red opponent never orders blue units", all(i.startswith("R-") for i in orders))
log = decisions(500)
check("each opponent order is logged with actor 'opponent' and a reason",
      len(log) == before + len(out["orders"]) and all(d["actor"] == "opponent" and d["reason"] for d in log[before:]))
# A player who hands their own side to the computer is not their own opponent: the console passes --actor autopilot.
sim("reset", "--scenario", "skirmish")
before = len(decisions(500))
code, out = sim("opponent", "--side", "red", "--actor", "autopilot")
log = decisions(500)
check("red's doctrine flown as the player's own autopilot is logged as actor 'autopilot'",
      code == 0 and len(out["orders"]) >= 3 and len(log) == before + len(out["orders"])
      and all(d["actor"] == "autopilot" and d["args"]["id"].startswith("R-") and d["reason"] for d in log[before:]))
# Blue's computer opponent, against the original red script so that red commits at once.
sim("reset", "--scenario", "skirmish")
sim("opponent", "--side", "red", "--doctrine", "basic", "--no-log")
blocked = kills = 0
blue_orders = []
for _ in range(20):
    code, out = sim("opponent", "--side", "blue", "--no-log")
    blue_orders += out["orders"]
    code, out = sim("tick")
    for e in out["world"]["events"]:
        if e["tick"] == out["world"]["tick"]:
            blocked += e["kind"] == "blocked"
            kills += e["kind"] == "destroyed" and e["detail"].startswith("R-")
check("blue opponent only orders its own HQ and fighter", {o["id"] for o in blue_orders} == {"B-HQ", "B-F1"})
check("blue's fighter only attacks aircraft, and otherwise moves or orbits",
      all(o["target"] in ("R-JET1", "R-F1", "R-BMR1", "R-DRN1", "R-MSL1", "R-MSL2") if o["action"] == "launch"
          else o["action"] in ("move", "orbit", "cease") for o in blue_orders if o["id"] == "B-F1"))
check("blue's model blocks at least one strike in the skirmish", blocked >= 1)
check("blue's fighter shoots down at least one red aircraft in the skirmish", kills >= 1)
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

# --- 3D: altitude, aircraft types, terrain masking, firing solutions ------------
code, out = sim("reset")
w = out["world"]
check("world reports its altitude ceiling", w["ceiling"] == 5)
check("aircraft start at cruise altitude and ground units at 0",
      unit(w, "R-JET1")["z"] == 3 and unit(w, "R-MSL1")["z"] == 2 and unit(w, "B-HQ")["z"] == 0 and unit(w, "R-J1")["z"] == 0)
check("tracks carry altitude and whether point defense has a lock",
      all("z" in t and "vz" in t and isinstance(t["locked"], bool) for t in w["tracks"]))
code, out = sim("spawn", "--type", "stealth", "--team", "red", "--x", "11", "--y", "11", "--z", "5")
check("spawn accepts the new aircraft types and a starting altitude", code == 0 and out["world"]["units"][-1]["z"] == 5)
fighter = out["world"]["units"][-1]["id"]
rejected("drone cannot spawn above its ceiling", "spawn", "--type", "drone", "--team", "red", "--x", "5", "--y", "5", "--z", "4", contains="ceiling")
rejected("ground unit cannot spawn in the air", "spawn", "--type", "sensor", "--team", "blue", "--x", "5", "--y", "9", "--z", "2", contains="ground")
rejected("ground units cannot change altitude", "order", "--id", "B-S1", "--action", "altitude", "--tz", "2", contains="ground")
rejected("altitude order needs a level", "order", "--id", "R-JET1", "--action", "altitude", contains="--tz")
rejected("altitude above the ceiling is rejected", "order", "--id", "R-JET1", "--action", "altitude", "--tz", "9", contains="ceiling")
rejected("missiles cannot orbit", "order", "--id", "R-MSL1", "--action", "orbit", contains="orbit")

sim("order", "--id", "R-MSL1", "--action", "cease", "--no-log")
sim("order", "--id", "R-JET1", "--action", "altitude", "--tz", "0", "--no-log")
sim("order", "--id", fighter, "--action", "move", "--tx", "5", "--ty", "11", "--tz", "1", "--no-log")
code, out = sim("tick")
check("jet descends one level per tick", unit(out["world"], "R-JET1")["z"] == 2)
f = unit(out["world"], fighter)
check("stealth fighter covers two cells and two levels per tick", (f["x"], f["z"]) == (9, 3))
jet_track = next(t for t in out["world"]["tracks"] if t["sourceUnitId"] == "R-JET1")
check("the model sees altitude and descent", jet_track["observed"][names.index("descent")] == 0.5
      and abs(jet_track["observed"][names.index("altitude")] - 0.4) < 1e-6 and jet_track["vz"] == -1)
code, out = sim("tick", "--n", "2")
jet = unit(out["world"], "R-JET1")
f = unit(out["world"], fighter)
check("altitude order finishes at the requested level", jet["z"] == 0 and jet["order"]["action"] == "none")
check("move with --tz finishes when both position and level are reached",
      (f["x"], f["y"], f["z"]) == (5, 11, 1) and f["order"]["action"] == "none")

sim("reset")
code, out = sim("order", "--id", "R-MSL1", "--action", "move", "--tx", "11", "--ty", "11", "--no-log")
check("a move order replaces a standing attack", unit(out["world"], "R-MSL1")["targetId"] == "")
sim("reset")
sim("order", "--id", "R-JET1", "--action", "orbit", "--no-log")
path = []
for _ in range(4):
    code, out = sim("tick")
    j = unit(out["world"], "R-JET1")
    path.append((j["x"], j["y"]))
check("orbit flies a four-cell box and returns to where it started",
      path == [(11, 2), (11, 3), (10, 3), (10, 2)] and j["order"]["action"] == "orbit" and j["z"] == 3)


def custom_world(units):
    """Rewrite the world file with hand-placed units, reusing the simulator's own unit layout."""
    sim("reset")
    world = json.load(open(WORLD))
    template = world["units"][0]
    built = []
    for uid, kind, team, x, y, z in units:
        u = json.loads(json.dumps(template))
        u.update({"id": uid, "type": kind, "team": team, "x": x, "y": y, "px": x, "py": y, "z": z, "pz": z,
                  "jamRadius": 3 if kind == "jammer" else 0, "targetId": "", "hp": 100, "integrity": 1})
        built.append(u)
    world.update({"units": built, "tracks": [], "events": [], "tick": 0})
    json.dump(world, open(WORLD, "w"))
    return sim("status")[1]["world"]


# Release altitude: a jet beside its target cannot hit until it is down at level 2.
custom_world([("B-HQ", "hq", "blue", 0, 0, 0), ("B-S1", "sensor", "blue", 6, 6, 0), ("R-JET1", "jet", "red", 7, 6, 5)])
sim("order", "--id", "R-JET1", "--action", "launch", "--target", "B-S1", "--no-log")
early_hits, later_hits, levels = 0, 0, []
for t in range(1, 13):
    code, out = sim("tick")
    levels.append(unit(out["world"], "R-JET1")["z"])
    hit = any(e["tick"] == t and e["kind"] == "engage" for e in out["world"]["events"])
    early_hits += hit and t < 3
    later_hits += hit and t >= 3
check("an attacker descends to its release altitude", levels[:4] == [4, 3, 2, 2])
check("no hits land from above release altitude, hits land once it is down", early_hits == 0 and later_hits >= 1)

# Terrain masking: the ridge cell (3,4) sits between the sensor and the drone.
low = custom_world([("B-S1", "sensor", "blue", 3, 2, 0), ("R-DRN1", "drone", "red", 3, 7, 1)])["tracks"][0]
high = custom_world([("B-S1", "sensor", "blue", 3, 2, 0), ("R-DRN1", "drone", "red", 3, 7, 2)])["tracks"][0]
check("a low contact behind cover is masked: weaker track and no lock; one level up it is seen",
      low["snr"] < high["snr"] and low["locked"] is False and high["locked"] is True)
loud = custom_world([("B-S1", "sensor", "blue", 3, 2, 0), ("R-BMR1", "bomber", "red", 9, 2, 4)])["tracks"][0]
quiet = custom_world([("B-S1", "sensor", "blue", 3, 2, 0), ("R-F1", "stealth", "red", 9, 2, 4)])["tracks"][0]
check("a stealth fighter gives a much weaker track than a bomber at the same place", quiet["snr"] < loud["snr"] / 2)

# Firing solution: with the only nearby sensor jammed, point defense cannot stop a stealth fighter.
custom_world([("B-HQ", "hq", "blue", 0, 0, 0), ("B-S1", "sensor", "blue", 9, 9, 0),
              ("R-J1", "jammer", "red", 10, 9, 0), ("R-F1", "stealth", "red", 11, 11, 4)])
sim("order", "--id", "R-F1", "--action", "launch", "--target", "B-S1", "--no-log")
sim("order", "--id", "B-HQ", "--action", "defend", "--target", "R-F1", "--no-log")
seen = []
for _ in range(10):
    code, out = sim("tick")
    seen += [e["kind"] for e in out["world"]["events"] if e["tick"] == out["world"]["tick"]]
check("defending a contact with no lock does not block it", "nolock" in seen and "engage" in seen and "blocked" not in seen)

# Air to air.
custom_world([("B-HQ", "hq", "blue", 0, 0, 0), ("B-F1", "stealth", "blue", 2, 2, 4), ("R-JET1", "jet", "red", 9, 2, 3),
              ("R-BMR1", "bomber", "red", 9, 9, 4)])
rejected("bombers cannot attack aircraft", "order", "--id", "R-BMR1", "--action", "launch", "--target", "B-F1", contains="ground targets")
sim("order", "--id", "B-F1", "--action", "launch", "--target", "R-JET1", "--no-log")
for _ in range(14):
    code, out = sim("tick")
check("a fighter climbs or dives to its target's level and shoots it down in two hits",
      unit(out["world"], "R-JET1")["alive"] is False and unit(out["world"], "B-F1")["z"] == 3)
old = json.load(open(WORLD))
for u in old["units"]:
    for key in ("z", "pz", "a0", "a1", "a2", "a3"):
        u.pop(key, None)
json.dump(old, open(WORLD, "w"))
code, out = sim("status")
check("a world saved before altitude existed loads with aircraft at cruise level",
      code == 0 and unit(out["world"], "R-BMR1")["z"] == 4 and unit(out["world"], "B-HQ")["z"] == 0)
sim("reset")

# --- autonomy: observation message and the autopilot command ----------------------
sim("reset", "--scenario", "skirmish")
sim("opponent", "--side", "red", "--no-log")
sim("tick", "--n", "2")
code, out = sim("observe")
obs = out["observation"]
check("observe lists blue's own units and its contacts", code == 0 and obs["v"] == 1 and len(obs["own"]) == 5 and len(obs["contacts"]) == 7)
check("an observed contact carries only what blue can measure",
      all(set(c) == {"id", "cls", "x", "y", "z", "vx", "vy", "vz", "snr", "locked", "score", "jam", "hit"} for c in obs["contacts"]))
check("the observation never names what a contact is attacking (nothing has been hit yet)",
      not any(blue_id in json.dumps(obs["contacts"]) for blue_id in ("B-HQ", "B-S1", "B-S2", "B-R1", "B-F1")))
before = len(decisions(500))
code, out = sim("autopilot", "--dry-run")
plan = out["orders"]
check("autopilot --dry-run plans without giving orders or logging",
      code == 0 and len(plan) >= 1 and unit(out["world"], "B-HQ")["order"]["action"] == "none" and len(decisions(500)) == before)
code, out = sim("autopilot")
check("autopilot gives the same orders it planned",
      [(o["id"], o["action"], o["target"]) for o in out["orders"]] == [(o["id"], o["action"], o["target"]) for o in plan]
      and all(o["ok"] for o in out["orders"]))
log = decisions(500)[before:]
check("autopilot orders are logged as actor 'autopilot' with the behavior and a reason",
      len(log) == len(plan) and all(d["actor"] == "autopilot" and d["reason"].split(":")[0] in
                                    ("DEFEND", "HOLD", "PATROL", "SHADOW", "INTERCEPT", "ENGAGE", "RTB") for d in log))
check("autopilot only orders blue units", all(o["id"].startswith("B-") for o in out["orders"]))
code, again = sim("autopilot", "--dry-run")
code, twice = sim("autopilot", "--dry-run")
check("planning twice from the same picture gives the same orders", again["orders"] == twice["orders"])
rejected("autopilot rejects an unknown variant", "autopilot", "--variant", "reckless", contains="--variant")
sim("reset")

# --- sensing: noisy detections and the tracker -------------------------------------
code, out = sim("reset", "--scenario", "skirmish")
w = out["world"]
check("sensing is perfect by default: every contact tracked exactly where it is",
      w["noise"] == 0 and len(w["tracks"]) == 7
      and all((t["x"], t["y"], t["z"]) == (unit(w, t["sourceUnitId"])["x"], unit(w, t["sourceUnitId"])["y"], unit(w, t["sourceUnitId"])["z"]) for t in w["tracks"]))
rejected("sensing rejects a noise level above 1", "sensing", "--noise", "1.5", contains="--noise")
rejected("sensing needs a noise level", "sensing", contains="--noise")
rejected("sensing rejects a bad fusion setting", "sensing", "--noise", "0.5", "--fusion", "maybe", contains="--fusion")


def noisy_run(seed, fusion="on", ticks=12):
    sim("reset", "--scenario", "skirmish")
    sim("sensing", "--noise", "0", "--no-log")
    sim("reset", "--scenario", "skirmish")
    sim("opponent", "--side", "red", "--no-log")
    sim("sensing", "--noise", "1", "--seed", str(seed), "--fusion", fusion)
    pictures, error, seen, live = [], 0.0, 0, 0
    for _ in range(ticks):
        world = sim("tick")[1]["world"]
        tracks = {t["sourceUnitId"]: t for t in world["tracks"]}
        pictures.append(sorted((k, t["x"], t["y"], t["z"]) for k, t in tracks.items()))
        for u in world["units"]:
            if u["team"] == "red" and u["alive"]:
                live += 1
                if u["id"] in tracks:
                    seen += 1
                    error += ((tracks[u["id"]]["x"] - u["x"]) ** 2 + (tracks[u["id"]]["y"] - u["y"]) ** 2) ** 0.5
    drops = sum(u["drops"] for u in world["units"])
    return pictures, world, error / max(1, seen), seen / live, drops


first, world, err_fused, cover_fused, drops_fused = noisy_run(11)
again, _, _, _, _ = noisy_run(11)
other, _, _, _, _ = noisy_run(12)
check("noisy sensing is reproducible: the same seed gives the same picture every tick", first == again)
check("a different seed gives a different picture", first != other)
check("with noise on, tracks report an estimate, an uncertainty and a position inside the map",
      world["noise"] == 1 and len(world["tracks"]) >= 1
      and all(t["err"] > 0 and 0 <= t["x"] < 12 and 0 <= t["y"] < 12 and 0 <= t["z"] <= 5 for t in world["tracks"]))
check("the picture is wrong sometimes but stays within a couple of cells on average", 0 < err_fused < 2)
_, _, err_raw, cover_raw, drops_raw = noisy_run(11, fusion="off")
check("the tracker holds more contact-ticks and loses fewer tracks than the best single detection",
      cover_fused > cover_raw and drops_fused < drops_raw)
check("the tracker's positions are closer to the truth than raw detections", err_fused < err_raw)
obs = sim("observe")[1]["observation"]
check("the planner's observation lists only contacts that have a track", len(obs["contacts"]) == len(sim("status")[1]["world"]["tracks"]))
code, out = sim("reset", "--scenario", "skirmish")
check("a reset keeps the sensing setting", out["world"]["noise"] == 1 and out["world"]["fusion"] == 0)
code, out = sim("sensing", "--noise", "0", "--fusion", "on")
check("noise 0 restores the perfect picture", out["world"]["noise"] == 0 and len(out["world"]["tracks"]) == 7
      and all(t["err"] == 0 for t in out["world"]["tracks"]))
sim("reset")

# --- red's coordinated doctrine: the strike arrives together ------------------------
sim("reset", "--scenario", "skirmish")
first_hit = {}
levels = {}
for t in range(1, 15):
    sim("opponent", "--side", "red", "--no-log")
    world = sim("tick")[1]["world"]
    for e in world["events"]:
        if e["tick"] == t and e["kind"] == "engage" and e["detail"].split(" hit ")[1] in ("B-HQ", "B-S1", "B-S2", "B-R1"):
            first_hit.setdefault(e["detail"].split(" hit ")[0], t)
    for u in world["units"]:
        if u["id"] in ("R-JET1", "R-DRN1") and u["alive"]:
            levels[u["id"]] = min(levels.get(u["id"], 9), u["z"])
arrivals = sorted(first_hit.values())
check("under the coordinated doctrine at least three attackers land their first hit within three ticks of each other",
      len(arrivals) >= 3 and any(arrivals[i + 2] - arrivals[i] <= 3 for i in range(len(arrivals) - 2)))
check("jets and drones come in at level 1", levels.get("R-JET1") == 1 and levels.get("R-DRN1") == 1)
check("every red attacker ends up attacking: nobody is left holding",
      all(u["targetId"] for u in world["units"] if u["team"] == "red" and u["alive"] and u["type"] not in ("jammer",)))
sim("reset")

# --- self-defense: a hit on blue's fighter is reported in the observation ------------
custom_world([("B-HQ", "hq", "blue", 0, 0, 0), ("B-F1", "stealth", "blue", 6, 6, 4), ("R-F1", "stealth", "red", 7, 6, 4)])
sim("order", "--id", "R-F1", "--action", "launch", "--target", "B-F1", "--no-log")
named = []
for _ in range(8):
    sim("tick")
    obs = sim("observe")[1]["observation"]
    named += [c["hit"] for c in obs["contacts"] if c["id"] == "R-F1"]
check("once a contact hits a blue unit, the observation says which unit it hit", "B-F1" in named and named[0] == "")
sim("reset")

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
