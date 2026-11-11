#!/usr/bin/env python3
"""Does the autonomy planner defend better than simpler rules?

    python3 ml/autonomy_eval.py                 # print the table, rewrite ml/AUTONOMY.md
    python3 ml/autonomy_eval.py --check         # also exit non-zero if a floor is missed
    python3 ml/autonomy_eval.py --rollouts 30 --check --no-write   # the quick version CI runs

Standard library only. Blue has two defensive resources in every rollout: the
headquarters' point defense (covers one contact per tick) and one stealth
fighter (can shoot aircraft down). Each policy below decides how to use them,
on the same rollouts, and is scored on mission value: the average over every
tick and blue unit of health x integrity.

The planner (`edge-sim autopilot`, native/src/autonomy.cpp) sees only the
observation message: blue's own units and its tracks. The comparison policies
see the same.
"""
import argparse
import os
import random
import sys

import simlab

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=150, help="random rollouts (seeds from 300000)")
p.add_argument("--check", action="store_true", help="fail if results fall below the floors")
p.add_argument("--no-write", action="store_true", help="do not rewrite ml/AUTONOMY.md")
args = p.parse_args()

TICKS = 22
SEVERITY = {"missile": 40, "bomber": 35, "stealth": 20, "jet": 15, "drone": 8, "jammer": 3}
AIRCRAFT = {"jet", "stealth", "bomber", "drone", "missile"}


def blue_value(world, blue):
    total = 0.0
    for u in world["units"]:
        if u["id"] in blue and u["alive"]:
            total += (u["hp"] / 100.0) * u["integrity"]
    return total / len(blue)


def unit_of(world, uid):
    return next((u for u in world["units"] if u["id"] == uid), None)


def triage_pick(world, skip=()):
    """Highest threat score x damage, contacts with a lock first (the rule from ml/outcome.py)."""
    kind = {u["id"]: u["type"] for u in world["units"]}
    for locked_only in (True, False):
        best, best_v = None, 0.0
        for t in world["tracks"]:
            if t["sourceUnitId"] in skip or (locked_only and not t.get("locked", True)):
                continue
            v = t["learnedScore"] * SEVERITY.get(kind.get(t["sourceUnitId"]), 0)
            if v > best_v:
                best, best_v = t["sourceUnitId"], v
        if best:
            return best
    return None


def hq_triage(sim, world):
    hq = unit_of(world, "B-HQ")
    if not hq or not hq["alive"]:
        return None
    want = triage_pick(world)
    have = hq["order"]["targetId"] if hq["order"]["action"] == "defend" else None
    if want and want != have:
        sim.run("order", "--id", "B-HQ", "--action", "defend", "--target", want)
    elif not want and have:
        sim.run("order", "--id", "B-HQ", "--action", "cease")
    return want


def fighter_target(world):
    f = unit_of(world, "B-F1")
    if not f or not f["alive"]:
        return None, None
    current = unit_of(world, f["targetId"]) if f["targetId"] else None
    return f, (current if current and current["alive"] else None)


def no_defense(sim, world):
    pass


def hq_only(sim, world):
    hq_triage(sim, world)


def chase_nearest(sim, world):
    """Point defense by triage; the fighter attacks the nearest red aircraft until it is down."""
    hq_triage(sim, world)
    f, current = fighter_target(world)
    if not f or current:
        return
    reds = [u for u in world["units"] if u["team"] == "red" and u["alive"] and u["type"] in AIRCRAFT]
    if reds:
        near = min(reds, key=lambda u: (u["x"] - f["x"]) ** 2 + (u["y"] - f["y"]) ** 2)
        sim.run("order", "--id", "B-F1", "--action", "launch", "--target", near["id"])


def scripted(sim, world):
    """Point defense by triage; the fighter takes the next-highest score x damage aircraft and commits."""
    covered = hq_triage(sim, world)
    f, current = fighter_target(world)
    if not f or current:
        return
    kind = {u["id"]: u["type"] for u in world["units"]}
    best, best_v = None, 0.0
    for t in world["tracks"]:
        uid = t["sourceUnitId"]
        if uid == covered or kind.get(uid) not in AIRCRAFT:
            continue
        v = t["learnedScore"] * SEVERITY.get(kind[uid], 0)
        if t["learnedScore"] >= t.get("watchAt", 0.33) and v > best_v:
            best, best_v = uid, v
    if best:
        sim.run("order", "--id", "B-F1", "--action", "launch", "--target", best)


def planner(variant):
    def act(sim, world):
        sim.run("autopilot", "--variant", variant)
    return act


POLICIES = [
    ("none", "No defense, fighter idle", no_defense),
    ("hq_only", "Point defense by triage, fighter idle", hq_only),
    ("chase", "Triage, fighter chases the nearest aircraft", chase_nearest),
    ("scripted", "Triage, fighter takes the next most dangerous aircraft", scripted),
    ("planner", "Autonomy planner", planner("full")),
    ("nolead", "Planner without lead intercept", planner("nolead")),
    ("notimed", "Planner with triage instead of timed point defense", planner("notimed")),
    ("nopatrol", "Planner without a patrol station", planner("nopatrol")),
    ("freefire", "Planner allowed to shoot contacts not yet labelled a threat", planner("freefire")),
    ("closein", "Planner that treats a close approach to its fighter as hostile", planner("closein")),
]


def play(sim, units, orders, act):
    state = sim.load(units)
    blue = [u["id"] for u in units if u["team"] == "blue"]
    red_air = {u["id"] for u in units if u["team"] == "red" and u["type"] in AIRCRAFT}
    # Ground truth the policies never see: which red aircraft are ever ordered to attack.
    attackers = {a[a.index("--id") + 1] for _, a in orders if "launch" in a}
    values, hits, blocked, down = [], 0, 0, set()
    for t in range(TICKS):
        for when, a in orders:
            if when == t:
                res = sim.run(*a)
                if res.get("ok"):
                    state = res
        act(sim, state["world"])
        state = sim.run("tick")
        world = state["world"]
        values.append(blue_value(world, blue))
        for e in world["events"]:
            if e["tick"] != world["tick"]:
                continue
            if e["kind"] == "engage" and e["detail"].split(" hit ")[-1] in blue:
                hits += 1
            if e["kind"] == "blocked":
                blocked += 1
            if e["kind"] == "destroyed" and e["detail"] in red_air:
                down.add(e["detail"])
    fighter = unit_of(state["world"], "B-F1")
    return {"value": sum(values) / len(values), "hits": hits, "blocked": blocked, "down": len(down & attackers),
            "bystanders": len(down - attackers), "fighter": 1 if fighter and fighter["alive"] else 0}


sim = simlab.Sim()
totals = {key: {"value": 0.0, "hits": 0, "blocked": 0, "down": 0, "bystanders": 0, "fighter": 0} for key, _, _ in POLICIES}
wins = {key: 0 for key, _, _ in POLICIES}
for i in range(args.rollouts):
    units, orders = simlab.fighter_scenario(300000 + i)
    row = {}
    for key, _, act in POLICIES:
        row[key] = play(sim, units, orders, act)
        for k, v in row[key].items():
            totals[key][k] += v
    for key in wins:
        if key != "scripted" and row[key]["value"] > row["scripted"]["value"] + 1e-9:
            wins[key] += 1
        if key == "scripted" and row["scripted"]["value"] > row["planner"]["value"] + 1e-9:
            wins[key] += 1
avg = {key: {k: v / args.rollouts for k, v in t.items()} for key, t in totals.items()}


def gap(key):
    lo, hi = avg["none"]["value"], avg["planner"]["value"]
    return (avg[key]["value"] - lo) / (hi - lo) if hi > lo else 0.0


out = ["# Autonomy planner results", "", "Generated by `python3 ml/autonomy_eval.py`. Do not edit by hand.", ""]
out.append("Blue has the headquarters' point defense (covers one contact per tick, if it has a lock) and one stealth "
           f"fighter (can shoot aircraft down). Each policy plays the same {args.rollouts} random rollouts for {TICKS} "
           "ticks using only blue's own observations. Mission value is the average over every tick and blue unit of "
           "health x integrity, so 1.000 means nothing was ever damaged or jammed.")
out.append("")
out.append("| Policy | Mission value | Share of the planner's gain | Hits taken | Hits blocked | Attackers shot down | Non-attackers shot down |")
out.append("| --- | --- | --- | --- | --- | --- | --- |")
for key, name, _ in POLICIES:
    a = avg[key]
    out.append(f"| {name} | {a['value']:.3f} | {gap(key):.0%} | {a['hits']:.2f} | {a['blocked']:.2f} | {a['down']:.2f} | {a['bystanders']:.2f} |")
out.append("")
out.append("The first four rows are the baselines, the fifth is the planner, and the last five are the planner with one "
           "behavior switched off or one stricter rule switched on, to show what each one contributes. \"Share of the planner's gain\" is how much of the "
           "distance from no defense to the full planner a policy covers. Hits, blocks and kills are averages per rollout. "
           "\"Non-attackers shot down\" counts red aircraft that were never ordered to attack anything in that rollout: "
           "the simulator does not punish shooting them, so mission value alone would reward a fighter that shoots at "
           "everything. No policy can see which aircraft those are.")
out.append("")
out.append(f"Head to head on mission value, the planner beat the scripted fighter policy in {wins['planner']} of "
           f"{args.rollouts} rollouts and lost in {wins['scripted']}; the rest were ties.")
out.append("")
report = "\n".join(out)
print(report)
if not args.no_write:
    with open(os.path.join(simlab.HERE, "AUTONOMY.md"), "w") as f:
        f.write(report)

if args.check:
    floors = [
        ("planner beats no defense", avg["planner"]["value"] > avg["none"]["value"]),
        ("planner beats point defense alone", avg["planner"]["value"] > avg["hq_only"]["value"]),
        ("planner beats the chase-nearest fighter", avg["planner"]["value"] > avg["chase"]["value"]),
        ("planner beats the scripted fighter", avg["planner"]["value"] > avg["scripted"]["value"]),
    ]
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(ok for _, ok in floors) else 1)
