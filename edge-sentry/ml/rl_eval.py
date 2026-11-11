#!/usr/bin/env python3
"""How does the learned policy compare with the planner?

    python3 ml/rl_eval.py                    # print the table, rewrite ml/RL.md
    python3 ml/rl_eval.py --check            # also exit non-zero if a floor is missed
    python3 ml/rl_eval.py --rollouts 30 --battles 16 --check --no-write   # the quick version CI runs

Standard library only: the policy runs through ml/rl_policy.py, with no
PyTorch. It plays the same games as the other evaluations, with the same
seeds: the planner evaluation's random rollouts (red follows a script; some
aircraft attack and some only pass through) and the opponent evaluation's
battles (red is flown by its coordinated doctrine). Neither set is used in
training.
"""
import argparse
import os
import sys

import rl_env
import rl_policy
import simlab

p = argparse.ArgumentParser()
p.add_argument("--rollouts", type=int, default=150, help="scripted rollouts (seeds from 300000, as in autonomy_eval.py)")
p.add_argument("--battles", type=int, default=80, help="battles against red's doctrine (seeds from 800000, as in opponent_eval.py)")
p.add_argument("--weights", default=rl_policy.DEFAULT_PATH)
p.add_argument("--check", action="store_true", help="fail if results fall below the floors")
p.add_argument("--no-write", action="store_true", help="do not rewrite ml/RL.md")
args = p.parse_args()

TICKS = rl_env.TICKS
AIRCRAFT = {"jet", "stealth", "bomber", "drone", "missile"}
weights = rl_policy.load(args.weights)
audit = {"orders": 0, "shots": 0, "below_line": 0}


def learned(sim, world):
    """The policy, with every order it gives checked against the hold-fire rule from outside."""
    obs = sim.run("observe", world=False)["observation"]
    feat, pd_choice, ft_choice = rl_policy.choose(weights, obs)
    blue_air = {u["id"] for u in obs["own"] if u["type"] in rl_env.AIRCRAFT_TYPES}
    by_id = {c["id"]: c for c in obs["contacts"]}
    for line in rl_env.order_lines(feat, pd_choice, ft_choice):
        audit["orders"] += 1
        if "launch" in line:
            audit["shots"] += 1
            target = by_id[line[line.index("--target") + 1]]
            if target["score"] < obs["watchAt"] and target["hit"] not in blue_air:
                audit["below_line"] += 1
        sim.run(*line, world=False)


def planner(sim, world):
    sim.run("autopilot", world=False)


def nobody(sim, world):
    pass


POLICIES = [("none", "Nobody", nobody), ("planner", "The planner", planner), ("learned", "The learned policy", learned)]


def play(sim, units, red, attackers, act):
    """One game. `red(t)` gives red's commands before tick t; `attackers` is None when red's doctrine flies
    (everything it sends attacks) or the ids of the aircraft a script ever orders to attack."""
    state = sim.load(units)
    blue = [u["id"] for u in units if u["team"] == "blue"]
    red_air = {u["id"] for u in units if u["team"] == "red" and u["type"] in AIRCRAFT}
    values, hits, down = [], 0, set()
    for t in range(TICKS):
        for line in red(t):
            sim.run(*line, world=False)
        act(sim, None)
        world = sim.run("tick")["world"]
        values.append(sum((u["hp"] / 100.0) * u["integrity"] for u in world["units"] if u["id"] in blue and u["alive"]) / len(blue))
        for e in world["events"]:
            if e["tick"] != world["tick"]:
                continue
            if e["kind"] == "engage" and e["detail"].split(" hit ")[-1] in blue:
                hits += 1
            if e["kind"] == "destroyed" and e["detail"] in red_air:
                down.add(e["detail"])
    fighter = next((u for u in world["units"] if u["id"] == "B-F1"), None)
    # In a battle, count as ml/opponent_eval.py does: red aircraft lost by the end, missiles aside.
    lost = sum(1 for u in world["units"] if u["team"] == "red" and not u["alive"] and u["type"] in AIRCRAFT and u["type"] != "missile")
    return {"value": sum(values) / len(values), "hits": hits,
            "down": lost if attackers is None else len(down & attackers),
            "bystanders": 0 if attackers is None else len(down - attackers),
            "fighter": 1 if fighter and fighter["alive"] else 0}


def run(games):
    sim = simlab.Sim()
    totals = {key: {"value": 0.0, "hits": 0, "down": 0, "bystanders": 0, "fighter": 0} for key, _, _ in POLICIES}
    wins = 0
    for units, red, attackers in games:
        row = {}
        for key, _, act in POLICIES:
            row[key] = play(sim, units, red, attackers, act)
            for k, v in row[key].items():
                totals[key][k] += v
        wins += row["learned"]["value"] > row["planner"]["value"] + 1e-9
    n = len(games)
    return {key: {k: v / n for k, v in t.items()} for key, t in totals.items()}, wins


def scripted_game(seed):
    units, orders = simlab.fighter_scenario(seed)
    _, red = rl_env.scenario(seed, False)
    attackers = {a[a.index("--id") + 1] for _, a in orders if "launch" in a}
    return units, red, attackers


rollouts, rollout_wins = run([scripted_game(300000 + i) for i in range(args.rollouts)])
battles, battle_wins = run([(simlab.battle_scenario(800000 + i), rl_env.scenario(800000 + i, True)[1], None) for i in range(args.battles)])


def share(table):
    lo, hi = table["none"]["value"], table["planner"]["value"]
    return (table["learned"]["value"] - lo) / (hi - lo) if hi > lo else 0.0


note = weights.get("note", {})
out = ["# Learned policy results", "", "Generated by `python3 ml/rl_eval.py`. Do not edit by hand.", ""]
out.append("A small network trained by reinforcement learning (PPO, `ml/rl_train.py`) plays blue's point defense and "
           "fighter from the same observation message the planner gets. It was trained for "
           f"{note.get('ticks', 0):,} simulator ticks on {note.get('device', 'an unknown device')}, on games drawn from "
           "seeds that no evaluation uses. "
           + ("The hold-fire rule is enforced as a mask: a shot at a contact scored below the watch line is never among its choices."
              if weights.get("roe", True) else "This version was trained WITHOUT the hold-fire mask, to measure what the rule costs."))
out.append("")
out.append(f"## Scripted rollouts ({args.rollouts}, the planner evaluation's own)")
out.append("")
out.append("| Who runs the defense | Mission value | Hits taken | Attackers shot down | Non-attackers shot down |")
out.append("| --- | --- | --- | --- | --- |")
for key, label, _ in POLICIES:
    r = rollouts[key]
    out.append(f"| {label} | {r['value']:.3f} | {r['hits']:.2f} | {r['down']:.2f} | {r['bystanders']:.2f} |")
out.append("")
out.append(f"## Battles against red's coordinated doctrine ({args.battles}, the opponent evaluation's own)")
out.append("")
out.append("| Who runs the defense | Mission value | Hits taken | Red aircraft shot down | Blue's fighter survives |")
out.append("| --- | --- | --- | --- | --- |")
for key, label, _ in POLICIES:
    r = battles[key]
    out.append(f"| {label} | {r['value']:.3f} | {r['hits']:.2f} | {r['down']:.2f} | {r['fighter']:.0%} |")
out.append("")
out.append(f"The learned policy covers {share(rollouts):.0%} of the distance from no defense to the planner on the rollouts and "
           f"{share(battles):.0%} in the battles. Game by game, it did better than the planner in {rollout_wins} of "
           f"{args.rollouts} rollouts and {battle_wins} of {args.battles} battles.")
out.append("")
out.append(f"Rules of engagement, checked from outside the policy: of {audit['shots']} orders to shoot, "
           f"{audit['below_line']} were at a contact below the watch line that had not hit one of blue's aircraft.")
out.append("")
out.append("Mission value is the average over every tick and blue unit of health x integrity. \"Non-attackers shot down\" "
           "counts red aircraft that were never ordered to attack anything in that rollout; the simulator does not "
           "punish shooting them, so only this column shows it.")
out.append("")
report = "\n".join(out)
print(report)
if not args.no_write:
    with open(os.path.join(simlab.HERE, "RL.md"), "w") as f:
        f.write(report)

if args.check:
    floors = [
        ("the learned policy beats no defense on the scripted rollouts", rollouts["learned"]["value"] > rollouts["none"]["value"] + 0.02),
        ("the learned policy beats no defense against red's doctrine", battles["learned"]["value"] > battles["none"]["value"] + 0.02),
        ("the learned policy covers at least half the distance from no defense to the planner",
         share(rollouts) >= 0.5 and share(battles) >= 0.5),
        ("the learned policy uses both of blue's resources: it takes fewer hits and shoots aircraft down",
         rollouts["learned"]["hits"] < rollouts["none"]["hits"] and battles["learned"]["down"] > 0),
    ]
    if weights.get("roe", True):
        floors.append(("no order to shoot breaks the hold-fire rule", audit["shots"] > 0 and audit["below_line"] == 0))
    for name, ok in floors:
        print(("PASS  " if ok else "FAIL  ") + name)
    sys.exit(0 if all(ok for _, ok in floors) else 1)
