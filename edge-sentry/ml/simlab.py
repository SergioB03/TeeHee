"""Shared helpers for training and evaluating the edge-sim threat model.

Standard library only. Everything goes through the real edge-sim binary, so
the features and outcomes are exactly what the simulator produces.
"""
import json
import math
import os
import random
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SIM = os.path.join(ROOT, "native", "build", "edge-sim")



def _input_names():
    """Ask the simulator for the model's input names so the two cannot drift."""
    binary = os.environ.get("EDGE_SIM") or DEFAULT_SIM
    if not os.path.exists(binary):
        raise SystemExit(f"edge-sim not found at {binary}. Build it with: npm run build:native")
    info = json.loads(subprocess.run([binary, "inputs"], capture_output=True, text=True).stdout)
    return info["inputs"], info["sequenceInputs"]


# Every track's `observed` vector follows this order (modelInputNames() in
# native/src/aiml_scorer.hpp). The simulator computes all of it, including the
# track-history inputs, so Python never re-derives a feature.
MODEL_INPUTS, SEQUENCE_INPUTS = _input_names()
IX = {name: i for i, name in enumerate(MODEL_INPUTS)}

HORIZON = 5        # label: does the contact harm a blue asset within this many ticks
TICKS = 22         # length of each generated rollout
HOSTILE_AT = 0.66  # thresholds used by the simulator's labels
WATCH_AT = 0.33

# The hand-set weights the project started with (speed, snr, jam, prox, alt),
# kept as a baseline that uses no privileged information.
V1_WEIGHTS = (0.20, 0.10, 0.30, 0.25, 0.15)


def predict(model, observed):
    """Reference forward pass: one tanh hidden layer, sigmoid output."""
    z = model["out_bias"]
    for weights, bias, out_w in zip(model["hidden_weights"], model["hidden_bias"], model["out_weights"]):
        z += out_w * math.tanh(bias + sum(w * x for w, x in zip(weights, observed)))
    return 1.0 / (1.0 + math.exp(-z))


def v1_score(features):
    """Original heuristic: no closing term, so no access to the target."""
    speed, snr, jam, prox, alt = features[:5]
    w = V1_WEIGHTS
    return max(0.0, min(1.0, w[0] * speed + w[1] * (1 - snr) + w[2] * jam + w[3] * prox + w[4] * alt))


class Sim:
    """Thin wrapper over the edge-sim CLI with its own world file."""

    def __init__(self, binary=None, scorer=None, log=False):
        self.binary = binary or os.environ.get("EDGE_SIM") or DEFAULT_SIM
        if not os.path.exists(self.binary):
            raise SystemExit(f"edge-sim not found at {self.binary}. Build it with: npm run build:native")
        self.dir = tempfile.mkdtemp(prefix="edge-ml-")
        self.world = os.path.join(self.dir, "world.json")
        self.scorer = scorer
        self.log = log
        # Sensing for worlds this wrapper loads: 0 = perfect picture. See `edge-sim sensing`.
        self.noise = 0.0
        self.seed = 0
        self.fusion = True

    def run(self, *args):
        # --no-log: bulk rollouts would otherwise fill the decision log.
        cmd = [self.binary, "--world", self.world] + ([] if self.log else ["--no-log"])
        if self.scorer:
            cmd += ["--scorer", self.scorer]
        out = subprocess.run(cmd + [str(a) for a in args], capture_output=True, text=True).stdout
        try:
            return json.loads(out)
        except ValueError:
            return {"ok": False, "error": "unreadable simulator output"}

    def load(self, units):
        """Start from a hand-built unit list instead of the default scenario."""
        world = {"tick": 0, "width": 12, "height": 12, "noise": self.noise, "seed": self.seed,
                 "fusion": 1 if self.fusion else 0, "units": units, "tracks": [], "events": [], "cover": []}
        with open(self.world, "w") as f:
            json.dump(world, f, separators=(",", ":"))
        return self.run("status")


# Highest altitude level each aircraft type can reach (profileOf() in native/src/world.cpp).
CEILING = {"jet": 5, "stealth": 5, "bomber": 5, "drone": 2, "missile": 5}
CRUISE = {"jet": 3, "stealth": 4, "bomber": 4, "drone": 1, "missile": 2}


def unit(uid, kind, team, x, y, target="", z=None):
    # Key order matters: edge-sim's reader expects the unit-level targetId
    # before the nested order object. z defaults to the type's cruise level.
    z = CRUISE.get(kind, 0) if z is None else z
    return {
        "id": uid, "type": kind, "team": team, "x": x, "y": y, "px": x, "py": y, "z": z, "pz": z,
        "hp": 100, "integrity": 1, "jamRadius": 3 if kind == "jammer" else 0,
        "targetId": target, "lastHitTick": -1000,
        "c0": 0, "c1": 0, "c2": 0, "h0": 0, "h1": 0, "h2": 0, "r0": -1, "r1": -1, "r2": -1, "r3": -1,
        "a0": -1, "a1": -1, "a2": -1, "a3": -1,
        "stopTicks": 0, "moved": 0, "alive": True,
        "order": {"action": "none", "tx": -1, "ty": -1, "tz": -1, "targetId": ""},
    }


def random_scenario(seed):
    """Random blue layout, random red contacts, and a script of orders.

    Returns (units, orders) where orders is a list of (tick, args) to issue
    before that tick is simulated. Red contacts are a mix of attackers,
    contacts in transit (some passing close to blue), loiterers, jammers and
    harmless ground units, with some changing behavior mid-run. Aircraft
    also start at varied altitudes and some climb, descend or orbit without
    ever attacking, so a descent on its own does not give an attack away.
    About a quarter of the rollouts give blue a fighter of its own.
    """
    rng = random.Random(seed)
    taken = set()

    def cell():
        while True:
            c = (rng.randrange(12), rng.randrange(12))
            if c not in taken:
                taken.add(c)
                return c

    def near(c, reach):
        return (max(0, min(11, c[0] + rng.randint(-reach, reach))), max(0, min(11, c[1] + rng.randint(-reach, reach))))

    units, orders, blue = [], [], []
    x, y = cell()
    units.append(unit("B-HQ", "hq", "blue", x, y))
    blue.append(("B-HQ", (x, y)))
    for i in range(rng.randint(2, 4)):
        kind = rng.choice(["sensor", "sensor", "relay"])
        uid = f"B-{'S' if kind == 'sensor' else 'R'}{i + 1}"
        x, y = cell()
        units.append(unit(uid, kind, "blue", x, y))
        blue.append((uid, (x, y)))

    kinds = (["jet"] * 5 + ["stealth"] * 3 + ["bomber"] * 2 + ["drone"] * 3 + ["missile"] * 5
             + ["jammer"] * 4 + ["sensor"] * 2 + ["relay"] * 2)
    prefix = {"jet": "JET", "stealth": "F", "bomber": "BMR", "drone": "DRN", "missile": "M",
              "jammer": "JAM", "sensor": "GS", "relay": "GR"}
    red_aircraft = []
    for i in range(rng.randint(2, 6)):
        kind = rng.choice(kinds)
        # Vary id length: the simulator's hit roll depends on it.
        uid = "R-" + prefix[kind] + str(rng.choice([i + 1, (i + 1) * 11]))
        x, y = cell()
        z = None
        if kind in CEILING and rng.random() < 0.3:
            z = rng.randint(0 if kind != "missile" else 1, CEILING[kind])
        units.append(unit(uid, kind, "red", x, y, z=z))
        roll = rng.random()
        change_at = rng.randint(3, 14) if rng.random() < 0.3 else None
        if kind in CEILING:
            if kind != "missile":
                red_aircraft.append(uid)
            level = ["--tz", rng.randint(0, CEILING[kind])] if rng.random() < 0.4 else []
            if roll < 0.42:
                orders.append((0, ["order", "--id", uid, "--action", "launch", "--target", rng.choice(blue)[0]]))
                if change_at:
                    orders.append((change_at, ["order", "--id", uid, "--action", "cease"]))
            elif roll < 0.66:
                # In transit: half pass close to a blue asset without attacking,
                # some changing altitude on the way.
                dest = near(rng.choice(blue)[1], 2) if rng.random() < 0.5 else (rng.randrange(12), rng.randrange(12))
                orders.append((0, ["order", "--id", uid, "--action", "move", "--tx", dest[0], "--ty", dest[1]] + level))
                if change_at:
                    orders.append((change_at, ["order", "--id", uid, "--action", "launch", "--target", rng.choice(blue)[0]]))
            elif roll < 0.76 and kind != "missile":
                # Loiter: fly somewhere (sometimes near blue), then orbit there.
                dest = near(rng.choice(blue)[1], 3) if rng.random() < 0.5 else (rng.randrange(12), rng.randrange(12))
                orders.append((0, ["order", "--id", uid, "--action", "move", "--tx", dest[0], "--ty", dest[1]]))
                orders.append((rng.randint(2, 8), ["order", "--id", uid, "--action", "orbit"] + level))
            elif roll < 0.86:
                # Change altitude and nothing else.
                orders.append((rng.randint(0, 6), ["order", "--id", uid, "--action", "altitude", "--tz", rng.randint(0, CEILING[kind])]))
                if change_at and change_at > 6:
                    orders.append((change_at, ["order", "--id", uid, "--action", "launch", "--target", rng.choice(blue)[0]]))
            elif change_at:
                orders.append((change_at, ["order", "--id", uid, "--action", "launch", "--target", rng.choice(blue)[0]]))
        elif kind == "jammer":
            if roll < 0.4:
                dest = near(rng.choice(blue)[1], 2)
                orders.append((0, ["order", "--id", uid, "--action", "move", "--tx", dest[0], "--ty", dest[1]]))
            elif roll < 0.65:
                orders.append((0, ["order", "--id", uid, "--action", "move", "--tx", rng.randrange(12), "--ty", rng.randrange(12)]))
            if change_at:
                orders.append((change_at, ["order", "--id", uid, "--action", "move", "--tx", rng.randrange(12), "--ty", rng.randrange(12)]))
        elif roll < 0.4:
            orders.append((0, ["order", "--id", uid, "--action", "move", "--tx", rng.randrange(12), "--ty", rng.randrange(12)]))

    # Sometimes blue has a fighter, which may patrol or intercept a red aircraft.
    if rng.random() < 0.25:
        x, y = cell()
        units.append(unit("B-F1", rng.choice(["jet", "stealth"]), "blue", x, y))
        roll = rng.random()
        if roll < 0.5 and red_aircraft:
            orders.append((rng.randint(0, 8), ["order", "--id", "B-F1", "--action", "launch", "--target", rng.choice(red_aircraft)]))
        elif roll < 0.8:
            orders.append((0, ["order", "--id", "B-F1", "--action", "orbit"]))
    return units, orders


def fighter_scenario(seed):
    """A random rollout in which blue always has exactly one stealth fighter, with no orders."""
    units, orders = random_scenario(seed)
    units = [u for u in units if u["id"] != "B-F1"]
    orders = [(t, a) for t, a in orders if "B-F1" not in a]
    rng = random.Random(seed * 7919 + 1)
    taken = {(u["x"], u["y"]) for u in units}
    while True:
        cell = (rng.randrange(12), rng.randrange(12))
        if cell not in taken:
            break
    units.append(unit("B-F1", "stealth", "blue", cell[0], cell[1]))
    return units, orders


def rollout(sim, units, orders, ticks=TICKS):
    """Run a scenario. Returns one snapshot per tick: {tick, tracks, hits}.

    hits is the set of red unit ids that damaged a blue unit on that tick.
    """
    state = sim.load(units)
    blue_ids = {u["id"] for u in units if u["team"] == "blue"}
    snaps = []
    for t in range(ticks + 1):
        for when, args in orders:
            if when == t:
                res = sim.run(*args)
                if res.get("ok"):
                    state = res
        if t > 0:
            state = sim.run("tick")
        world = state["world"]
        hits = set()
        for e in world["events"]:
            if e["tick"] == world["tick"] and e["kind"] == "engage" and " hit " in e["detail"]:
                attacker, victim = e["detail"].split(" hit ", 1)
                if victim in blue_ids:
                    hits.add(attacker)
        snaps.append({"tick": world["tick"], "tracks": world["tracks"], "hits": hits})
    return snaps


def label_rows(snaps, seed, horizon=HORIZON):
    """One row per tracked red contact per tick, labelled from the future.

    harm = 1 if, within `horizon` ticks, the contact damages a blue unit, or
    it is a jammer with a blue sensor, relay or HQ inside its field (now or
    within the horizon). Ticks without a full horizon ahead are dropped.
    """
    rows = []
    jam_ix = IX["blueInField"]
    for i, snap in enumerate(snaps):
        if i + horizon >= len(snaps):
            break
        ahead = snaps[i + 1 : i + 1 + horizon]
        for tr in snap["tracks"]:
            uid = tr["sourceUnitId"]
            harm = any(uid in s["hits"] for s in ahead)
            if not harm:
                for s in [snap] + ahead:
                    later = next((k for k in s["tracks"] if k["sourceUnitId"] == uid), None)
                    if later and later["observed"][jam_ix] > 0:
                        harm = True
                        break
            rows.append({
                "seed": seed, "tick": snap["tick"], "id": uid,
                "observed": tr["observed"], "features": tr["features"],
                "heuristic": tr["heuristicScore"], "learned": tr["learnedScore"],
                "sequence": tr.get("sequenceScore", 0.5),
                "harm": int(harm),
            })
    return rows


def dataset(seeds, binary=None, noise_for=None):
    """Labelled rows from random rollouts. noise_for(seed) gives a sensing noise level per rollout (default perfect)."""
    sim = Sim(binary)
    rows = []
    for seed in seeds:
        if noise_for:
            sim.noise, sim.seed = noise_for(seed), seed
        units, orders = random_scenario(seed)
        rows.extend(label_rows(rollout(sim, units, orders), seed))
    return rows


def auc(scores, labels):
    """Area under the ROC curve via the rank statistic, with tie handling."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    return (sum(r for r, y in zip(ranks, labels) if y) - pos * (pos + 1) / 2) / (pos * neg)


def prf(scores, labels, threshold):
    tp = sum(1 for s, y in zip(scores, labels) if s >= threshold and y)
    fp = sum(1 for s, y in zip(scores, labels) if s >= threshold and not y)
    fn = sum(1 for s, y in zip(scores, labels) if s < threshold and y)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1
