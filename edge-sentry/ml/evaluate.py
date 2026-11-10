#!/usr/bin/env python3
"""Evaluate the threat scorers on held-out rollouts and scripted scenarios.

    python3 ml/evaluate.py            # print results, rewrite ml/RESULTS.md
    python3 ml/evaluate.py --check    # also exit non-zero if a floor is missed (CI)

Standard library only. The scorers are compared on identical simulator output:
    learned     small neural network on observable features and track history (the default scorer)
    sequence    GRU that reads per-tick inputs and keeps its own memory (once ml/train_seq.py has run)
    heuristic   hand-set weights that also read the contact's real target
    v1          the original hand-set weights, observable features only
"""
import argparse
import json
import os
import sys

import seqlab
import simlab
from scenarios import LEVELS, SCENARIOS

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=200, help="held-out rollouts (seeds from 100000)")
p.add_argument("--check", action="store_true", help="fail if results fall below the floors")
args = p.parse_args()

model = json.load(open(os.path.join(simlab.HERE, "model.json")))
seq_model = seqlab.load()  # None until ml/train_seq.py has been run
SCORERS = [
    ("learned", "Learned model (observable inputs and track history)", "Learned"),
    ("sequence", "Sequence model (GRU, per-tick inputs only, learns its own history)", "Sequence model"),
    ("heuristic", "Heuristic that reads the contact's target", "Heuristic with target"),
    ("v1", "Original heuristic (observable inputs only)", "Original heuristic"),
]
if seq_model is None:
    SCORERS = [s for s in SCORERS if s[0] != "sequence"]
SHORT = {key: short for key, _, short in SCORERS}
SCORERS = [(key, name) for key, name, _ in SCORERS]


def scores_for(row_or_track, key):
    if key == "learned":
        return row_or_track["learned"] if "learned" in row_or_track else row_or_track["learnedScore"]
    if key == "sequence":
        return row_or_track["sequence"] if "sequence" in row_or_track else row_or_track["sequenceScore"]
    if key == "heuristic":
        return row_or_track["heuristic"] if "heuristic" in row_or_track else row_or_track["heuristicScore"]
    return simlab.v1_score(row_or_track["features"])


def cuts(key):
    """(watch, hostile) cut-offs: each trained model carries its own."""
    if key == "learned":
        return model["watch_at"], model["hostile_at"]
    if key == "sequence":
        return seq_model["watch_at"], seq_model["hostile_at"]
    return simlab.WATCH_AT, simlab.HOSTILE_AT


def level(score, key):
    watch_at, hostile_at = cuts(key)
    return 2 if score >= hostile_at else 1 if score >= watch_at else 0


# --- held-out rollouts ---------------------------------------------------------
rows = simlab.dataset(range(100000, 100000 + args.rollouts))
labels = [r["harm"] for r in rows]
air = [r for r in rows if r["observed"][simlab.IX["airborne"]] > 0.5]
air_labels = [r["harm"] for r in air]
parity = max(abs(simlab.predict(model, r["observed"]) - r["learned"]) for r in rows)
seq_parity = 0.0
if seq_model:
    for s in seqlab.sequences(rows):
        ref = seqlab.scores(seq_model, s["x"])
        seq_parity = max(seq_parity, max(abs(a - r["sequence"]) for a, r in zip(ref, s["rows"])))

metrics = {}
for key, _ in SCORERS:
    s = [scores_for(r, key) for r in rows]
    sa = [scores_for(r, key) for r in air]
    pr, rc, f1 = simlab.prf(s, labels, cuts(key)[1])
    metrics[key] = {"auc": simlab.auc(s, labels), "auc_air": simlab.auc(sa, air_labels),
                    "precision": pr, "recall": rc, "f1": f1}

# --- scripted scenarios --------------------------------------------------------
sim = simlab.Sim()


def run_check(check, snaps, key):
    kind, uid = check[0], check[1]

    def lvl(tick):
        tr = next((t for t in snaps[tick]["tracks"] if t["sourceUnitId"] == uid), None)
        return None if tr is None else level(scores_for(tr, key), key)

    if kind == "warned":
        hit = next((s["tick"] for s in snaps if uid in s["hits"]), None)
        if hit is None:
            return False, "never hit, so the scenario itself is broken"
        first = next((s["tick"] for s in snaps if lvl(s["tick"]) == 2), None)
        if first is None or first > hit - check[2]:
            return False, f"hit at tick {hit}, hostile " + ("never" if first is None or first >= hit else f"only from tick {first}")
        return True, f"hostile from tick {first}, hit at tick {hit}"
    if kind == "never":
        want, t0, t1 = LEVELS[check[2]], check[3], check[4]
        bad = [t for t in range(t0, min(t1, len(snaps) - 1) + 1) if (lvl(t) or 0) >= want]
        return (not bad), (f"reached {check[2]} at tick {bad[0]}" if bad else f"stayed below {check[2]}")
    want, tick = LEVELS[check[2]], check[3]
    got = lvl(tick)
    if got is None:
        return False, f"not tracked at tick {tick}"
    ok = got >= want if kind == "at_least" else got < want
    return ok, f"{['benign', 'watch', 'hostile'][got]} at tick {tick}"


scenario_results = []
for sc in SCENARIOS:
    snaps = simlab.rollout(sim, sc["units"], sc["orders"], sc["ticks"])
    per = {}
    for key, _ in SCORERS:
        outcomes = [run_check(c, snaps, key) for c in sc["checks"]]
        per[key] = (all(ok for ok, _ in outcomes), "; ".join(note for ok, note in outcomes if not ok))
    scenario_results.append((sc["name"], per))
passed = {key: sum(1 for _, per in scenario_results if per[key][0]) for key, _ in SCORERS}

# --- report --------------------------------------------------------------------
out = []
out.append("# Threat scorer results")
out.append("")
out.append("Generated by `python3 ml/evaluate.py`. Do not edit by hand.")
out.append("")
out.append(f"Task: predict whether a red contact will harm a blue asset within {simlab.HORIZON} ticks "
           "(hit it, or hold it inside a jam field).")
out.append("")
out.append(f"## Held-out rollouts ({args.rollouts} random scenarios, {len(rows)} contact-ticks, "
           f"{sum(labels) / len(labels):.0%} positive)")
out.append("")
out.append("| Scorer | AUC | AUC, air contacts only | Precision at hostile | Recall at hostile | F1 |")
out.append("| --- | --- | --- | --- | --- | --- |")
for key, name in SCORERS:
    m = metrics[key]
    out.append(f"| {name} | {m['auc']:.3f} | {m['auc_air']:.3f} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} |")
out.append("")
seq_cut = f", {seq_model['hostile_at']:.2f} for the sequence model" if seq_model else ""
out.append(f"\"At hostile\" means at or above each scorer's hostile cut-off: {model['hostile_at']:.2f} for the learned model"
           f"{seq_cut} (chosen on validation rollouts to maximize F1), 0.66 for the heuristics. "
           f"Air contacts (jets and missiles) are the hard cases: {len(air)} rows, {sum(air_labels) / len(air_labels):.0%} positive.")
out.append("")
out.append(f"## Scripted scenarios ({len(SCENARIOS)} cases with a known right call)")
out.append("")
out.append("| Scenario | " + " | ".join(SHORT[k] for k, _ in SCORERS) + " |")
out.append("| --- |" + " --- |" * len(SCORERS))
for name, per in scenario_results:
    cells = [("pass" if per[k][0] else "FAIL: " + per[k][1]) for k, _ in SCORERS]
    out.append(f"| {name} | " + " | ".join(cells) + " |")
out.append("| **Passed** | " + " | ".join(f"**{passed[k]} of {len(SCENARIOS)}**" for k, _ in SCORERS) + " |")
out.append("")
bound = "1e-5" if parity < 1e-5 else f"{parity:.1e}"
out.append(f"C++ and Python implementations of the model agree to within {bound} on every held-out row.")
if seq_model:
    seq_bound = "1e-5" if seq_parity < 1e-5 else f"{seq_parity:.1e}"
    tr = seq_model["training"]
    out.append("")
    out.append(f"The sequence model is a GRU with {seq_model['hidden']} hidden units trained with PyTorch {tr['torch']} "
               f"(`ml/train_seq.py`, {tr['epochs']} epochs). It sees {seqlab.SEQ_INPUTS} per-tick inputs and none of the "
               "hand-built history inputs, and edge-sim carries its hidden state for each contact from tick to tick. "
               f"Its C++ and Python implementations agree to within {seq_bound} on every held-out row.")
out.append("")
report = "\n".join(out)
print(report)
with open(os.path.join(simlab.HERE, "RESULTS.md"), "w") as f:
    f.write(report)

if args.check:
    floors = [
        ("C++/Python parity under 1e-4", parity < 1e-4),
        ("sequence model C++/Python parity under 1e-4", seq_parity < 1e-4),
        ("learned AUC at least 0.95", metrics["learned"]["auc"] >= 0.95),
        ("learned AUC on air contacts at least 0.93", metrics["learned"]["auc_air"] >= 0.93),
        ("learned beats the observable-only heuristic by 0.10 AUC", metrics["learned"]["auc"] >= metrics["v1"]["auc"] + 0.10),
        ("learned passes at least 12 scripted scenarios", passed["learned"] >= 12),
    ]
    failed = [name for name, ok in floors if not ok]
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(1 if failed else 0)
