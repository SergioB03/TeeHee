#!/usr/bin/env python3
"""What happens when blue works from noisy sensors instead of a perfect picture?

    python3 ml/tracking_eval.py                 # print the tables, rewrite ml/TRACKING.md
    python3 ml/tracking_eval.py --check         # also exit non-zero if a floor is missed
    python3 ml/tracking_eval.py --rollouts 20 --check --no-write   # the quick version CI runs

Standard library only. With sensing noise on, each blue sensor detects a
contact only some of the time and reports a noisy position (worse at long
range, at low signature, behind cover and under jamming). Two ways of turning
detections into a picture are compared:

    tracker        a constant-velocity Kalman filter per contact that fuses
                   every sensor's detection, coasts through short gaps and
                   drops the track after three missed ticks
    no tracker     the single best detection of the tick, and nothing at all
                   on a tick with no detection

Everything downstream (the threat model, point defense locks, the autonomy
planner) reads the resulting picture. The threat model is the one trained on
perfect sensing; it is not retrained here.
"""
import argparse
import math
import os
import sys

import simlab

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=100, help="random rollouts per setting")
p.add_argument("--check", action="store_true", help="fail if results fall below the floors")
p.add_argument("--no-write", action="store_true", help="do not rewrite ml/TRACKING.md")
args = p.parse_args()

TICKS = 22
SETTINGS = [
    ("Perfect picture", 0.0, True),
    ("Light noise, tracker", 0.35, True),
    ("Light noise, no tracker", 0.35, False),
    ("Heavy noise, tracker", 0.7, True),
    ("Heavy noise, no tracker", 0.7, False),
    ("Worst case, tracker", 1.0, True),
    ("Worst case, no tracker", 1.0, False),
]


def configure(sim, noise, fusion, seed):
    sim.noise, sim.fusion, sim.seed = noise, fusion, seed


def picture_quality(noise, fusion):
    """Track coverage, position error, track losses, and threat-model AUC on held-out rollouts."""
    sim = simlab.Sim()
    live = tracked = losses = 0
    error = 0.0
    rows = []
    for i in range(args.rollouts):
        seed = 600000 + i
        configure(sim, noise, fusion, seed)
        units, orders = simlab.random_scenario(seed)
        blue_ids = {u["id"] for u in units if u["team"] == "blue"}
        state = sim.load(units)
        snaps = []
        for t in range(TICKS + 1):
            for when, a in orders:
                if when == t:
                    res = sim.run(*a)
                    if res.get("ok"):
                        state = res
            if t > 0:
                state = sim.run("tick")
            world = state["world"]
            tracks = {k["sourceUnitId"]: k for k in world["tracks"]}
            for u in world["units"]:
                if u["team"] != "red" or not u["alive"]:
                    continue
                live += 1
                k = tracks.get(u["id"])
                if k:
                    tracked += 1
                    error += math.hypot(k["x"] - u["x"], k["y"] - u["y"])
            hits = set()
            for e in world["events"]:
                if e["tick"] == world["tick"] and e["kind"] == "engage" and " hit " in e["detail"]:
                    attacker, victim = e["detail"].split(" hit ", 1)
                    if victim in blue_ids:
                        hits.add(attacker)
            snaps.append({"tick": world["tick"], "tracks": world["tracks"], "hits": hits})
        losses += sum(u.get("drops", 0) for u in state["world"]["units"])
        rows.extend(label_rows_with_truth(snaps, seed))
    scores = [r["learned"] for r in rows]
    labels = [r["harm"] for r in rows]
    _, recall, _ = simlab.prf(scores, labels, HOSTILE_AT)
    out = {"coverage": tracked / live, "error": error / max(1, tracked), "losses": losses / args.rollouts,
           "auc": simlab.auc(scores, labels), "recall": recall, "auc_mix": float("nan"), "recall_mix": float("nan")}
    if MIX:
        mixed = [simlab.predict(MIX, r["observed"]) for r in rows]
        out["auc_mix"] = simlab.auc(mixed, labels)
        out["recall_mix"] = simlab.prf(mixed, labels, MIX["hostile_at"])[1]
    return out


def label_rows_with_truth(snaps, seed):
    """simlab.label_rows, but a jammer's harm is judged from later ticks even if its track is missing then."""
    return simlab.label_rows(snaps, seed)


def defended_value(noise, fusion, defend):
    """Mission value over fighter rollouts with the autonomy planner running blue (or nobody)."""
    sim = simlab.Sim()
    total = 0.0
    for i in range(args.rollouts):
        seed = 700000 + i
        configure(sim, noise, fusion, seed)
        units, orders = simlab.fighter_scenario(seed)
        blue = [u["id"] for u in units if u["team"] == "blue"]
        state = sim.load(units)
        values = []
        for t in range(TICKS):
            for when, a in orders:
                if when == t:
                    sim.run(*a)
            if defend:
                sim.run("autopilot")
            world = sim.run("tick")["world"]
            values.append(sum((u["hp"] / 100.0) * u["integrity"] for u in world["units"] if u["id"] in blue and u["alive"]) / len(blue))
        total += sum(values) / len(values)
    return total / args.rollouts


import json  # noqa: E402

HOSTILE_AT = json.load(open(os.path.join(simlab.HERE, "model.json")))["hostile_at"]
MIX_PATH = os.path.join(simlab.HERE, "model_noise_mix.json")
MIX = json.load(open(MIX_PATH)) if os.path.exists(MIX_PATH) else None  # written by `ml/train.py --noise-mix`
results = []
undefended = defended_value(0.0, True, defend=False)
for name, noise, fusion in SETTINGS:
    q = picture_quality(noise, fusion)
    q["value"] = defended_value(noise, fusion, defend=True)
    results.append((name, q))
    print(f"{name}: {q}", file=sys.stderr)

out = ["# Sensing and tracking results", "", "Generated by `python3 ml/tracking_eval.py`. Do not edit by hand.", ""]
out.append("With sensing noise on, each blue sensor detects a contact only some of the time and reports a noisy position. "
           "The tracker is a constant-velocity Kalman filter per contact that fuses every sensor's detection, coasts "
           "through gaps of up to three ticks, and then drops the track. \"No tracker\" uses the single best detection "
           f"of the tick and sees nothing on a tick without one. {args.rollouts} random rollouts per row.")
out.append("")
out.append("| Sensing | Contact-ticks with a track | Position error, cells | Tracks lost per rollout | Threat AUC | Recall at hostile | Mission value with the planner |")
out.append("| --- | --- | --- | --- | --- | --- | --- |")
for name, q in results:
    out.append(f"| {name} | {q['coverage']:.1%} | {q['error']:.2f} | {q['losses']:.2f} | {q['auc']:.3f} | {q['recall']:.2f} | {q['value']:.3f} |")
out.append(f"| No defense at all | | | | | | {undefended:.3f} |")
out.append("")
out.append("Position error is the distance between where the picture puts a contact and where it is. Threat AUC and "
           "recall are for the default threat model, trained on the perfect picture and not retrained, over the "
           "contact-ticks that have a track. Mission value is from rollouts where blue has point defense and a fighter, "
           "both run by the autonomy planner from the same picture.")
out.append("")
if MIX:
    out.append("")
    out.append("## Does training on noisy tracks help?")
    out.append("")
    out.append("The same network retrained on tracker output at mixed noise levels (`python3 ml/train.py --noise-mix`), "
               "scored on the same rows. It is an experiment: edge-sim still ships the weights trained on the perfect picture.")
    out.append("")
    out.append("| Sensing | Threat AUC, shipped model | Threat AUC, trained on mixed noise | Recall at hostile, shipped | Recall at hostile, mixed noise |")
    out.append("| --- | --- | --- | --- | --- |")
    for name, q in results:
        out.append(f"| {name} | {q['auc']:.3f} | {q['auc_mix']:.3f} | {q['recall']:.2f} | {q['recall_mix']:.2f} |")
out.append("")
report = "\n".join(out)
print(report)
if not args.no_write:
    with open(os.path.join(simlab.HERE, "TRACKING.md"), "w") as f:
        f.write(report)

if args.check:
    by = dict(results)
    floors = [
        ("perfect picture tracks every contact with no error", by["Perfect picture"]["coverage"] == 1.0 and by["Perfect picture"]["error"] == 0.0),
        ("under heavy noise the tracker holds more contact-ticks than no tracker", by["Heavy noise, tracker"]["coverage"] > by["Heavy noise, no tracker"]["coverage"]),
        ("under heavy noise the tracker loses fewer tracks", by["Heavy noise, tracker"]["losses"] < by["Heavy noise, no tracker"]["losses"]),
        ("under heavy noise the planner still beats no defense", by["Heavy noise, tracker"]["value"] > undefended),
    ]
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(ok for _, ok in floors) else 1)
