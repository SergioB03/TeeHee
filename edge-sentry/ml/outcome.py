#!/usr/bin/env python3
"""Outcome evaluation: does acting on the threat score actually protect blue?

    python3 ml/outcome.py                  # print the table, rewrite ml/OUTCOMES.md
    python3 ml/outcome.py --check          # also exit non-zero if a floor is missed
    python3 ml/outcome.py --rollouts 40 --check --no-write   # the quick version CI runs
    python3 ml/outcome.py --write-fixtures # regenerate evals/fixtures/outcome/*.json

Blue has one defensive resource: an HQ's point defense covers against one
contact per tick (`order --id B-HQ --action defend --target <contact>`). A
covered contact's hit is blocked and a covered jammer's field is suppressed.
With several contacts and one slot, the outcome depends on triage, which is
what the threat score is for.

Every policy plays the same rollouts through the real edge-sim binary and is
scored on mission value: the average, over every tick and every blue asset,
of health x integrity (0 for a destroyed asset). Standard library only.
"""
import argparse
import json
import os
import random
import shutil
import sys

import simlab
from scenarios import SCENARIOS

TICKS = 20
AGENT_TICKS = 12  # the agent eval plays a shorter game to keep tool calls reasonable
# Expected loss if the contact gets through, by class. Triage is by
# score x severity, the same rule for every scorer. Contact class is taken
# as known (classification is not what is being tested here).
SEVERITY = {"missile": 40, "jet": 15, "jammer": 3}

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=150, help="random rollouts (seeds from 200000)")
p.add_argument("--check", action="store_true")
p.add_argument("--write-fixtures", action="store_true")
p.add_argument("--no-write", action="store_true", help="do not rewrite ml/OUTCOMES.md (quick checks with fewer rollouts)")
args = p.parse_args()


def blue_value(world, blue_ids):
    units = {u["id"]: u for u in world["units"]}
    total = 0.0
    for uid in blue_ids:
        u = units.get(uid)
        if u and u["alive"]:
            total += (u["hp"] / 100.0) * u["integrity"]
    return total / len(blue_ids)


def hq_of(world):
    return next((u["id"] for u in world["units"] if u["type"] == "hq" and u["team"] == "blue" and u["alive"]), None)


def kinds(world):
    return {u["id"]: u["type"] for u in world["units"]}


def by_score(key):
    """Defend the contact with the highest score x severity under one scorer."""
    def choose(world, _ctx):
        kind = kinds(world)
        best, best_v = None, 0.0
        for t in world["tracks"]:
            score = t["learnedScore"] if key == "learned" else t["heuristicScore"] if key == "heuristic" else simlab.v1_score(t["features"])
            v = score * SEVERITY.get(kind.get(t["sourceUnitId"]), 0)
            if v > best_v:
                best, best_v = t["sourceUnitId"], v
        return best
    return choose


def no_defense(_world, _ctx):
    return None


def random_contact(world, ctx):
    ids = [t["sourceUnitId"] for t in world["tracks"]]
    return ctx["rng"].choice(ids) if ids else None


def lookahead(world, ctx):
    """Try every assignment on a copy of the world, keep the best next tick.

    Uses the simulator's own future, so it is a reference point, not a policy
    a real operator could run. Ties go to the higher-severity contact.
    """
    sim, probe, hq = ctx["sim"], ctx["probe"], hq_of(world)
    if not hq:
        return None
    kind = kinds(world)
    candidates = [None] + sorted((t["sourceUnitId"] for t in world["tracks"]), key=lambda i: -SEVERITY.get(kind.get(i), 0))
    best, best_v = None, -1.0
    for c in candidates:
        shutil.copyfile(sim.world, probe.world)
        if c:
            probe.run("order", "--id", hq, "--action", "defend", "--target", c)
        else:
            probe.run("order", "--id", hq, "--action", "cease")
        nxt = probe.run("tick")
        v = blue_value(nxt["world"], ctx["blue"])
        if v > best_v + 1e-9:
            best, best_v = c, v
    return best


POLICIES = [
    ("none", "No defense", no_defense),
    ("random", "Defend a random contact", random_contact),
    ("v1", "Triage by original heuristic", by_score("v1")),
    ("heuristic", "Triage by heuristic that reads the target", by_score("heuristic")),
    ("learned", "Triage by learned model", by_score("learned")),
    ("lookahead", "One-step lookahead (uses the simulator's future)", lookahead),
]


def play(sim, probe, units, orders, choose, seed, ticks=TICKS):
    """Run one rollout under one policy. Returns (mission value, stats, start world text)."""
    state = sim.load(units)
    blue = [u["id"] for u in units if u["team"] == "blue"]
    ctx = {"rng": random.Random(seed), "sim": sim, "probe": probe, "blue": blue}
    current, values = None, []
    hits = blocked = 0
    start_text = None
    for t in range(ticks):
        for when, a in orders:
            if when == t:
                res = sim.run(*a)
                if res.get("ok"):
                    state = res
        if t == 0:
            start_text = open(sim.world).read()
        hq = hq_of(state["world"])
        want = choose(state["world"], ctx) if hq else None
        if hq and want != current:
            res = sim.run("order", "--id", hq, "--action", "defend", "--target", want) if want else sim.run("order", "--id", hq, "--action", "cease")
            if res.get("ok"):
                current = want
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
    final = state["world"]
    health = sum(u["hp"] for u in final["units"] if u["id"] in blue) / len(blue)
    lost = sum(1 for u in final["units"] if u["id"] in blue and not u["alive"])
    return sum(values) / len(values), {"hits": hits, "blocked": blocked, "health": health, "lost": lost}, start_text


def run_all(cases, ticks=TICKS):
    """cases: list of (name, units, orders, seed). Returns per-policy averages."""
    sim, probe = simlab.Sim(), simlab.Sim()
    totals = {key: {"value": 0.0, "hits": 0, "blocked": 0, "health": 0.0, "lost": 0} for key, _, _ in POLICIES}
    per_case = []
    for name, units, orders, seed in cases:
        row = {"name": name}
        for key, _, choose in POLICIES:
            value, stats, start = play(sim, probe, units, orders, choose, seed, ticks)
            totals[key]["value"] += value
            for k in ("hits", "blocked", "health", "lost"):
                totals[key][k] += stats[k]
            row[key] = value
            row["start"] = start
        per_case.append(row)
    n = len(cases)
    return {key: {k: v / n for k, v in t.items()} for key, t in totals.items()}, per_case


random_cases = []
for seed in range(200000, 200000 + args.rollouts):
    units, orders = simlab.random_scenario(seed)
    random_cases.append((f"seed {seed}", units, orders, seed))
avg, _ = run_all(random_cases)

# Scripted cases double as fixtures for the agent eval. Only cases whose red
# orders all happen at tick 0 are used: the agent advances time itself, so
# nothing can be injected mid-run.
scripted, seen = [], set()
for i, sc in enumerate(SCENARIOS):
    setup = json.dumps([sc["units"], sc["orders"]], sort_keys=True)
    if setup in seen or any(when != 0 for when, _ in sc["orders"]):
        continue
    seen.add(setup)
    scripted.append((sc["name"], sc["units"], sc["orders"], i))
_, scripted_rows = run_all(scripted, AGENT_TICKS)
# Keep the ones where the defensive slot matters at all.
keep = [i for i, row in enumerate(scripted_rows) if max(row["learned"], row["lookahead"]) > row["none"] + 1e-9]
scripted = [scripted[i] for i in keep]
scripted_rows = [scripted_rows[i] for i in keep]


def gap_closed(table, key):
    # Lookahead is greedy, so it is a strong reference rather than a true ceiling.
    lo, hi = table["none"]["value"], table["lookahead"]["value"]
    return (table[key]["value"] - lo) / (hi - lo) if hi > lo else 0.0


out = ["# Outcome results", "", "Generated by `python3 ml/outcome.py`. Do not edit by hand.", ""]
out.append("Blue's HQ can cover against one contact per tick. Each policy below decides which, on the same "
           f"rollouts, for {TICKS} ticks. Mission value is the average over every tick and blue asset of "
           "health x integrity, so 1.000 means nothing was ever damaged or jammed.")
out.append("")
out.append(f"## {args.rollouts} random rollouts")
out.append("")
out.append("| Policy | Mission value | Gain relative to lookahead | Hits taken | Hits blocked | Health at end | Assets lost |")
out.append("| --- | --- | --- | --- | --- | --- | --- |")
for key, name, _ in POLICIES:
    a = avg[key]
    out.append(f"| {name} | {a['value']:.3f} | {gap_closed(avg, key):.0%} | {a['hits']:.2f} | {a['blocked']:.2f} | {a['health']:.1f} | {a['lost']:.2f} |")
out.append("")
out.append("\"Gain relative to lookahead\" is how much of the distance from no defense to one-step lookahead a policy recovers. "
           "Lookahead is greedy (it only sees one tick ahead), so it is a strong reference, not a true ceiling. "
           "Hits, blocks and losses are averages per rollout.")
out.append("")
out.append(f"## {len(scripted)} scripted scenarios, {AGENT_TICKS} ticks (the agent eval plays these)")
out.append("")
out.append("| Scenario | No defense | Learned model | Lookahead |")
out.append("| --- | --- | --- | --- |")
for row in scripted_rows:
    out.append(f"| {row['name']} | {row['none']:.3f} | {row['learned']:.3f} | {row['lookahead']:.3f} |")
out.append("")
report = "\n".join(out)
print(report)
if not args.no_write:
    with open(os.path.join(simlab.HERE, "OUTCOMES.md"), "w") as f:
        f.write(report)

if args.write_fixtures:
    fixture_dir = os.path.join(simlab.ROOT, "evals", "fixtures", "outcome")
    os.makedirs(fixture_dir, exist_ok=True)
    index = []
    for i, row in enumerate(scripted_rows):
        slug = "".join(c if c.isalnum() else "-" for c in row["name"].lower()).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        blue = [u["id"] for u in scripted[i][1] if u["team"] == "blue"]
        index.append({"id": slug, "name": row["name"], "ticks": AGENT_TICKS, "blue": blue, "world": row["start"],
                      "noDefense": round(row["none"], 4), "learnedPolicy": round(row["learned"], 4),
                      "lookahead": round(row["lookahead"], 4)})
    with open(os.path.join(fixture_dir, "cases.json"), "w") as f:
        json.dump(index, f, indent=1)
        f.write("\n")
    print(f"wrote {len(index)} agent-eval fixtures to evals/fixtures/outcome/cases.json")

if args.check:
    floors = [
        ("learned triage beats no defense", avg["learned"]["value"] > avg["none"]["value"]),
        ("learned triage beats random assignment", avg["learned"]["value"] > avg["random"]["value"]),
        ("learned triage beats the original heuristic", avg["learned"]["value"] > avg["v1"]["value"]),
        ("learned triage beats the heuristic that reads the target", avg["learned"]["value"] > avg["heuristic"]["value"]),
        ("learned triage recovers at least half of the lookahead gain", gap_closed(avg, "learned") >= 0.5),
    ]
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(ok for _, ok in floors) else 1)
