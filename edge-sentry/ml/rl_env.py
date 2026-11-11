"""The training environment for a learned defense policy.

Standard library only. The policy plays blue's autonomy: each tick it gets the
same observation message the planner gets (blue's own units and its tracks,
never red's orders) and makes two choices:

    point defense   which contact headquarters covers, or none
    fighter         which aircraft the fighter goes after, or back to its station

Rules of engagement are a mask, not a hope: with roe=True the fighter cannot be
sent to shoot at a contact scored below the watch line unless that contact has
hit one of blue's aircraft. Those choices are simply not offered, so no amount
of training can learn to break the rule. roe=False removes the mask, to measure
what the rule costs.

The reward each tick is blue's mission value after that tick, minus one: zero
while nothing is damaged or jammed, negative from then on. Its sum over a
rollout is the same quantity every evaluation in this project reports.
"""
import json
import random

import simlab

TICKS = 22
MAX_CONTACTS = 12
CLASSES = ["jet", "stealth", "bomber", "drone", "missile", "jammer"]  # anything else is "other ground unit"
FLYING = {"jet", "stealth", "bomber", "drone", "missile"}
AIRCRAFT_TYPES = {"jet", "stealth", "bomber", "drone"}                # blue units that fly
N_CONTACT = 27   # numbers describing one contact
N_GLOBAL = 13    # numbers describing blue's own side


def cheb(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def featurize(obs, roe=True):
    """Turn one observation into what the policy reads and what it may choose.

    Returns a dict:
      contacts  up to MAX_CONTACTS rows of N_CONTACT numbers
      ids       the contact id of each row
      globals   N_GLOBAL numbers
      pd_mask   [none, contact 0, contact 1, ...]: True where point defense may choose it
      ft_mask   [station, contact 0, ...]: True where the fighter may choose it
      hq, fighter   the two blue units the choices are carried out by (or None)
    """
    own = obs["own"]
    hq = next((u for u in own if u["type"] == "hq"), None)
    fighter = next((u for u in own if u["type"] in AIRCRAFT_TYPES), None)
    assets = [u for u in own if u["type"] not in AIRCRAFT_TYPES]
    blue_air = {u["id"] for u in own if u["type"] in AIRCRAFT_TYPES}
    watch = obs["watchAt"]
    contacts = sorted(obs["contacts"], key=lambda c: -c["score"])[:MAX_CONTACTS]
    contacts.sort(key=lambda c: c["id"])  # a stable order, so the same picture always gives the same rows

    rows, ids = [], []
    pd_mask, ft_mask = [True], [True]
    for c in contacts:
        cls = c["cls"]
        row = [1.0 if cls == k else 0.0 for k in CLASSES] + [0.0 if cls in CLASSES else 1.0]
        row += [c["x"] / 11.0, c["y"] / 11.0, c["z"] / 5.0]
        row += [max(-1.0, min(1.0, c[k] / 2.0)) for k in ("vx", "vy", "vz")]
        row += [c["snr"], 1.0 if c["locked"] else 0.0, c["score"], 1.0 if c["score"] >= watch else 0.0, min(1.0, c["jam"] / 4.0)]
        hit_air = c["hit"] in blue_air
        row += [1.0 if c["hit"] else 0.0, 1.0 if hit_air else 0.0]
        row.append(cheb(c["x"], c["y"], hq["x"], hq["y"]) / 11.0 if hq else 1.0)
        if assets:
            near = min(assets, key=lambda a: cheb(c["x"], c["y"], a["x"], a["y"]))
            dist = cheb(c["x"], c["y"], near["x"], near["y"])
            dx, dy = near["x"] - c["x"], near["y"] - c["y"]
            norm = max(1.0, (dx * dx + dy * dy) ** 0.5)
            closing = max(-1.0, min(1.0, (c["vx"] * dx + c["vy"] * dy) / (2.0 * norm)))
            row += [dist / 11.0, closing]
        else:
            row += [1.0, 0.0]
        if fighter:
            row += [cheb(c["x"], c["y"], fighter["x"], fighter["y"]) / 11.0, abs(c["z"] - fighter["z"]) / 5.0]
        else:
            row += [1.0, 1.0]
        row.append(1.0 if hq and hq["act"] == "defend" and hq["tgt"] == c["id"] else 0.0)
        row.append(1.0 if fighter and fighter["tgt"] == c["id"] else 0.0)
        rows.append(row)
        ids.append(c["id"])
        pd_mask.append(bool(hq) and bool(c["locked"]))
        may_shoot = cls in FLYING and (not roe or c["score"] >= watch or hit_air)
        ft_mask.append(bool(fighter) and may_shoot)

    g = [obs["tick"] / float(TICKS)]
    g += [1.0, hq["hp"] / 100.0] if hq else [0.0, 0.0]
    if fighter:
        g += [1.0, fighter["hp"] / 100.0, fighter["x"] / 11.0, fighter["y"] / 11.0, fighter["z"] / 5.0]
    else:
        g += [0.0, 0.0, 0.0, 0.0, 0.0]
    g += [len(assets) / 5.0, (sum(a["hp"] for a in assets) / (100.0 * len(assets))) if assets else 0.0]
    g += [len(rows) / float(MAX_CONTACTS)]
    g += [1.0 if fighter and fighter["act"] == "launch" else 0.0, 1.0 if hq and hq["act"] == "defend" else 0.0]
    return {"contacts": rows, "ids": ids, "globals": g, "pd_mask": pd_mask, "ft_mask": ft_mask,
            "hq": hq, "fighter": fighter, "assets": assets}


def order_lines(feat, pd_choice, ft_choice):
    """The simulator commands that carry out one pair of choices. Nothing is sent for a choice already in force."""
    lines = []
    hq, fighter = feat["hq"], feat["fighter"]
    if hq:
        if pd_choice > 0:
            target = feat["ids"][pd_choice - 1]
            if not (hq["act"] == "defend" and hq["tgt"] == target):
                lines.append(["order", "--id", hq["id"], "--action", "defend", "--target", target])
        elif hq["act"] == "defend":
            lines.append(["order", "--id", hq["id"], "--action", "cease"])
    if fighter:
        if ft_choice > 0:
            target = feat["ids"][ft_choice - 1]
            if not (fighter["act"] == "launch" and fighter["tgt"] == target):
                lines.append(["order", "--id", fighter["id"], "--action", "launch", "--target", target])
        elif fighter["act"] == "launch" or fighter["tgt"]:
            # Break off and go back to a station among the things it protects.
            assets = feat["assets"] or [fighter]
            sx = round(sum(a["x"] for a in assets) / len(assets))
            sy = round(sum(a["y"] for a in assets) / len(assets))
            lines.append(["order", "--id", fighter["id"], "--action", "move", "--tx", sx, "--ty", sy, "--tz", 3])
    return lines


def scenario(seed, battle):
    """(units, red_lines) for one rollout. red_lines(t) are the commands that give red its orders before tick t.

    battle=False: a random rollout with a script of red orders, some attacking and some only passing through
    (the autonomy evaluation's kind). battle=True: red is flown each tick by its coordinated doctrine.
    """
    if battle:
        return simlab.battle_scenario(seed), (lambda t: [["opponent", "--side", "red"]])
    units, orders = simlab.fighter_scenario(seed)
    by_tick = {}
    for when, args in orders:
        by_tick.setdefault(when, []).append(list(args))
    return units, (lambda t: by_tick.get(t, []))


class VecEnv:
    """Many rollouts at once, each in its own `edge-sim serve` process.

    Commands for every simulator are written first and the replies read afterwards, so the simulators work in
    parallel while Python waits. Every rollout lasts exactly TICKS ticks and they all start together, which keeps
    the bookkeeping simple: step() returns done=True for every environment on the same call.
    """

    def __init__(self, n, seed=0, battle_share=0.3, roe=True, first_seed=2_000_000, fixed=None):
        self.n = n
        self.roe = roe
        self.rng = random.Random(seed)
        self.battle_share = battle_share
        self.next_seed = first_seed
        self.fixed = fixed  # a list of (seed, battle) to play in order instead of drawing new rollouts
        self.cursor = 0
        self.sims = [simlab.Sim(fast=True) for _ in range(n)]
        self.red = [None] * n
        self.feats = [None] * n
        self.t = 0

    def close(self):
        for s in self.sims:
            s.close()

    def _exchange(self, batches):
        """batches[i] is a list of command lines for simulator i. Returns the parsed replies in the same shape."""
        for sim, lines in zip(self.sims, batches):
            if lines:
                sim.proc.stdin.write("".join(
                    "\t".join(str(w).replace("\t", " ").replace("\n", " ") for w in line) + "\n" for line in lines))
                sim.proc.stdin.flush()
        return [[json.loads(sim.proc.stdout.readline()) for _ in lines] for sim, lines in zip(self.sims, batches)]

    def _draw(self):
        if self.fixed is not None:
            seed, battle = self.fixed[self.cursor % len(self.fixed)]
            self.cursor += 1
            return seed, battle
        seed = self.next_seed
        self.next_seed += 1
        return seed, self.rng.random() < self.battle_share

    def reset(self):
        """Start a fresh rollout in every simulator. Returns the first picture of each."""
        batches = []
        for i in range(self.n):
            seed, battle = self._draw()
            units, self.red[i] = scenario(seed, battle)
            world = {"tick": 0, "width": 12, "height": 12, "noise": 0.0, "seed": 0, "fusion": 1, "units": units,
                     "tracks": [], "events": [], "cover": []}
            lines = [["load", json.dumps(world, separators=(",", ":"))]]
            lines += [line + ["--no-world"] for line in self.red[i](0)]
            lines.append(["observe", "--no-world"])
            batches.append(lines)
        replies = self._exchange(batches)
        self.t = 0
        self.feats = [featurize(r[-1]["observation"], self.roe) for r in replies]
        return self.feats

    def step(self, pd_choices, ft_choices):
        """Carry out one pair of choices per simulator and advance a tick.

        Returns (pictures, rewards, done). When done is True the pictures are the first of the next rollouts.
        """
        last = self.t + 1 >= TICKS
        batches = []
        for i in range(self.n):
            lines = [line + ["--no-world"] for line in order_lines(self.feats[i], pd_choices[i], ft_choices[i])]
            lines.append(["tick", "--no-world"])
            if not last:
                lines += [line + ["--no-world"] for line in self.red[i](self.t + 1)]
            lines.append(["observe", "--no-world"])
            batches.append(lines)
        replies = self._exchange(batches)
        rewards = [r[-1]["blueValue"] - 1.0 for r in replies]
        if last:
            return self.reset(), rewards, True
        self.t += 1
        self.feats = [featurize(r[-1]["observation"], self.roe) for r in replies]
        return self.feats, rewards, False
