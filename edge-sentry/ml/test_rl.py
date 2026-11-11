#!/usr/bin/env python3
"""Checks on the training environment and the policy's rules. Standard library only.

    python3 ml/test_rl.py
"""
import random
import sys

import rl_env
import rl_policy
import simlab

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


def pictures(roe, games=6, seed=7):
    """Every picture from a few games played with random allowed choices."""
    rng = random.Random(seed)
    env = rl_env.VecEnv(games, seed=seed, roe=roe, fixed=[(1_800_000 + i, i % 2 == 1) for i in range(games)])
    seen, totals = [], [0.0] * games
    feats = env.reset()
    for _ in range(rl_env.TICKS):
        seen += feats
        pd = [rng.choice([i for i, ok in enumerate(f["pd_mask"]) if ok]) for f in feats]
        ft = [rng.choice([i for i, ok in enumerate(f["ft_mask"]) if ok]) for f in feats]
        feats, rewards, done = env.step(pd, ft)
        totals = [a + r for a, r in zip(totals, rewards)]
    env.close()
    return seen, totals, done


held, totals, done = pictures(roe=True)
again, totals_again, _ = pictures(roe=True)
check("the same seeds and the same choices give the same games, tick for tick",
      [f["contacts"] for f in held] == [f["contacts"] for f in again] and totals == totals_again)
check("every game lasts exactly 22 ticks", done is True and len(held) == 6 * rl_env.TICKS)
check("every picture has the declared number of inputs",
      all(len(f["globals"]) == rl_env.N_GLOBAL and all(len(r) == rl_env.N_CONTACT for r in f["contacts"]) for f in held))


# Where three of a contact's inputs sit in its row (see rl_env.featurize).
LOCKED, AT_LINE, HIT_AIRCRAFT = 14, 16, 19

# Rebuild a few observations by hand to test the masks against the rule as written in docs/INTERFACE.md.
def contact(cid, cls, score, locked=1, hit=""):
    return {"id": cid, "cls": cls, "x": 6, "y": 6, "z": 3, "vx": -1, "vy": 0, "vz": 0, "snr": 0.6, "locked": locked,
            "score": score, "jam": 0, "hit": hit}


OWN = [{"id": "B-HQ", "type": "hq", "x": 1, "y": 1, "z": 0, "hp": 100, "act": "none", "tgt": "", "tx": -1, "ty": -1, "tz": -1},
       {"id": "B-S1", "type": "sensor", "x": 2, "y": 3, "z": 0, "hp": 100, "act": "none", "tgt": "", "tx": -1, "ty": -1, "tz": -1},
       {"id": "B-F1", "type": "stealth", "x": 3, "y": 3, "z": 4, "hp": 100, "act": "none", "tgt": "", "tx": -1, "ty": -1, "tz": -1}]


def picture(contacts, own=OWN, roe=True):
    obs = {"v": 1, "tick": 3, "width": 12, "height": 12, "ceiling": 5, "watchAt": 0.24, "own": own, "contacts": contacts}
    return rl_env.featurize(obs, roe=roe)


f = picture([contact("R-A", "jet", 0.10), contact("R-B", "jet", 0.60), contact("R-C", "stealth", 0.05, hit="B-F1"),
             contact("R-D", "jammer", 0.90), contact("R-E", "bomber", 0.70, locked=0), contact("R-F", "jet", 0.05, hit="B-S1")])
offered = {cid for cid, ok in zip(f["ids"], f["ft_mask"][1:]) if ok}
check("hold fire: the fighter is not offered a contact scored below the watch line", "R-A" not in offered)
check("a contact at or above the watch line is offered", "R-B" in offered and "R-E" in offered)
check("self-defense: a contact that has hit one of blue's aircraft is offered whatever its score", "R-C" in offered)
check("a contact that hit a ground unit but scores below the line is still not offered", "R-F" not in offered)
check("the fighter is never offered a ground unit", "R-D" not in offered)
covered = {cid for cid, ok in zip(f["ids"], f["pd_mask"][1:]) if ok}
check("point defense is not offered a contact it has no lock on", "R-E" not in covered and "R-B" in covered and "R-D" in covered)
check("doing nothing is always among the choices", f["pd_mask"][0] and f["ft_mask"][0])
free = picture([contact("R-A", "jet", 0.10)], roe=False)
check("with hold-fire switched off, the low-scored contact is offered", free["ft_mask"][1] is True)
alone = picture([contact("R-B", "jet", 0.60)], own=OWN[:2])
check("with no fighter and no headquarters, nothing but 'do nothing' is offered",
      alone["ft_mask"] == [True, False] and picture([contact("R-B", "jet", 0.60)], own=OWN[1:2])["pd_mask"] == [True, False])
check("in every picture from real games, the masks follow the same rules",
      all(all((not ok) or c[LOCKED] == 1.0 for ok, c in zip(g["pd_mask"][1:], g["contacts"]))
          and all((not ok) or c[AT_LINE] == 1.0 or c[HIT_AIRCRAFT] == 1.0 for ok, c in zip(g["ft_mask"][1:], g["contacts"]))
          for g in held))

# Orders: only what changes.
busy = [dict(OWN[0], act="defend", tgt="R-B"), OWN[1], dict(OWN[2], act="launch", tgt="R-B")]
g = picture([contact("R-B", "jet", 0.60)], own=busy)
check("a choice already in force sends no order", rl_env.order_lines(g, 1, 1) == [])
lines = rl_env.order_lines(g, 0, 0)
check("dropping both sends a cease and a move back to station",
      [l[4] for l in lines] == ["cease", "move"] and lines[1][lines[1].index("--tz") + 1] == 3)
lines = rl_env.order_lines(picture([contact("R-B", "jet", 0.60)]), 1, 1)
check("new choices become a defend order and a launch order at that contact",
      [(l[2], l[4], l[6]) for l in lines] == [("B-HQ", "defend", "R-B"), ("B-F1", "launch", "R-B")])

# The reward is the evaluations' own measure.
units, orders = simlab.fighter_scenario(1_800_000)
sim = simlab.Sim()
sim.load(units)
blue = [u["id"] for u in units if u["team"] == "blue"]
values = []
for t in range(rl_env.TICKS):
    for when, a in orders:
        if when == t:
            sim.run(*a)
    world = sim.run("tick")["world"]
    values.append(sum(u["hp"] / 100.0 * u["integrity"] for u in world["units"] if u["id"] in blue and u["alive"]) / len(blue))
env = rl_env.VecEnv(1, fixed=[(1_800_000, False)])
env.reset()
total = 0.0
for _ in range(rl_env.TICKS):
    _, rewards, _ = env.step([0], [0])
    total += rewards[0]
env.close()
check("the reward adds up to the mission value the evaluations report", abs((1 + total / rl_env.TICKS) - sum(values) / len(values)) < 1e-4)

# The shipped policy.
weights = rl_policy.load()
legal = True
for g in held:
    pd, ft = rl_policy.forward(weights, g["contacts"], g["globals"])
    legal = legal and len(pd) == len(g["contacts"]) + 1 and g["pd_mask"][rl_policy.best(pd, g["pd_mask"])] and g["ft_mask"][rl_policy.best(ft, g["ft_mask"])]
check("the shipped policy only ever picks a choice the rules offer", legal)
g = held[5]
order = list(range(len(g["contacts"])))
random.Random(3).shuffle(order)
pd, _ = rl_policy.forward(weights, g["contacts"], g["globals"])
pd2, _ = rl_policy.forward(weights, [g["contacts"][i] for i in order], g["globals"])
check("the policy scores a contact the same wherever it sits in the list",
      len(order) < 2 or all(abs(pd[1 + i] - pd2[1 + j]) < 1e-9 for j, i in enumerate(order)))

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
