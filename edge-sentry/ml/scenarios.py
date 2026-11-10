"""Scripted scenarios with a known right call.

Each scenario fixes the units and orders, then states what a good threat
scorer should say and when. The expectations were written from the intent of
each scenario (who actually attacks, who never does), not tuned to any scorer.

Check kinds:
    ("warned", uid, lead)            hostile at least `lead` ticks before the
                                     contact's first hit on a blue unit
    ("never", uid, level, t0, t1)    never reaches `level` during ticks t0..t1
    ("at_least", uid, level, tick)   at or above `level` at `tick`
    ("below", uid, level, tick)      below `level` at `tick`
"""
from simlab import unit

LEVELS = {"benign": 0, "watch": 1, "hostile": 2}


def default_units():
    """The simulator's built-in scenario, written out so it can be scripted."""
    return [
        unit("B-HQ", "hq", "blue", 1, 1),
        unit("B-S1", "sensor", "blue", 2, 3),
        unit("B-S2", "sensor", "blue", 3, 8),
        unit("B-R1", "relay", "blue", 4, 5),
        unit("R-J1", "jammer", "red", 5, 6),
        unit("R-JET1", "jet", "red", 10, 2),
        unit("R-MSL1", "missile", "red", 8, 3, target="B-HQ"),
    ]


def blue_post():
    return [
        unit("B-HQ", "hq", "blue", 2, 6),
        unit("B-S1", "sensor", "blue", 3, 6),
        unit("B-R1", "relay", "blue", 4, 5),
    ]


def order(tick, uid, action, **kw):
    args = ["order", "--id", uid, "--action", action]
    if "to" in kw:
        args += ["--tx", kw["to"][0], "--ty", kw["to"][1]]
    if "level" in kw:
        args += ["--tz", kw["level"]]
    if "target" in kw:
        args += ["--target", kw["target"]]
    return (tick, args)


SCENARIOS = [
    {
        "name": "Missile homing on headquarters",
        "units": default_units(), "orders": [], "ticks": 10,
        "checks": [("warned", "R-MSL1", 2)],
    },
    {
        "name": "Jet parked at long range",
        "units": default_units(), "orders": [], "ticks": 10,
        "checks": [("never", "R-JET1", "hostile", 0, 10)],
    },
    {
        "name": "Jammer already covering a relay",
        "units": default_units(), "orders": [], "ticks": 4,
        "checks": [("at_least", "R-J1", "watch", 1)],
    },
    {
        "name": "Jammer out of reach of everything",
        "units": blue_post() + [unit("R-JAM1", "jammer", "red", 10, 10)],
        "orders": [], "ticks": 8,
        "checks": [("never", "R-JAM1", "hostile", 0, 8)],
    },
    {
        "name": "Jet attack run on a sensor",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 11, 6)],
        "orders": [order(0, "R-JET1", "launch", target="B-S1")], "ticks": 16,
        "checks": [("warned", "R-JET1", 2)],
    },
    {
        "name": "Jet flies past without attacking",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 11, 1)],
        "orders": [order(0, "R-JET1", "move", to=(0, 1))], "ticks": 14,
        "checks": [("never", "R-JET1", "hostile", 0, 14)],
    },
    {
        "name": "Jet moves in next to headquarters and loiters",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 9, 6)],
        "orders": [order(0, "R-JET1", "move", to=(2, 7))], "ticks": 14,
        "checks": [("at_least", "R-JET1", "watch", 8), ("never", "R-JET1", "hostile", 9, 14)],
    },
    {
        "name": "Attacker stands down while still far away",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 11, 11)],
        "orders": [order(0, "R-JET1", "launch", target="B-HQ"), order(3, "R-JET1", "cease")], "ticks": 10,
        "checks": [("below", "R-JET1", "hostile", 6), ("never", "R-JET1", "hostile", 6, 10)],
    },
    {
        "name": "Idle jet turns attacker mid-run",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 10, 5)],
        "orders": [order(6, "R-JET1", "launch", target="B-R1")], "ticks": 18,
        "checks": [("never", "R-JET1", "hostile", 0, 5), ("warned", "R-JET1", 1)],
    },
    {
        "name": "Harmless red ground unit beside a relay",
        "units": blue_post() + [unit("R-GS1", "sensor", "red", 5, 5)],
        "orders": [], "ticks": 8,
        "checks": [("never", "R-GS1", "watch", 0, 8)],
    },
    {
        "name": "Jammer drives into range of a relay",
        "units": blue_post() + [unit("R-JAM1", "jammer", "red", 10, 5)],
        "orders": [order(0, "R-JAM1", "move", to=(5, 5))], "ticks": 8,
        "checks": [("below", "R-JAM1", "hostile", 0), ("at_least", "R-JAM1", "watch", 4)],
    },
    {
        "name": "Missile fired across the whole map",
        "units": [unit("B-HQ", "hq", "blue", 0, 0), unit("B-S1", "sensor", "blue", 1, 2), unit("R-M1", "missile", "red", 11, 11)],
        "orders": [order(0, "R-M1", "launch", target="B-HQ")], "ticks": 16,
        "checks": [("warned", "R-M1", 2)],
    },
    {
        "name": "Two jets, only one attacks",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 10, 3), unit("R-JET22", "jet", "red", 10, 9)],
        "orders": [order(0, "R-JET1", "launch", target="B-R1")], "ticks": 14,
        "checks": [("warned", "R-JET1", 2), ("never", "R-JET22", "hostile", 0, 14)],
    },
    {
        "name": "Missile that has no target",
        "units": blue_post() + [unit("R-M1", "missile", "red", 9, 6)],
        "orders": [], "ticks": 8,
        "checks": [("never", "R-M1", "hostile", 0, 8)],
    },
    # --- 3D: altitude, aircraft types, terrain masking -------------------------
    {
        "name": "Stealth fighter attack run on headquarters",
        "units": blue_post() + [unit("R-F1", "stealth", "red", 11, 6)],
        "orders": [order(0, "R-F1", "launch", target="B-HQ")], "ticks": 12,
        "checks": [("warned", "R-F1", 1)],
    },
    {
        "name": "Bomber run on headquarters from high level",
        "units": blue_post() + [unit("R-BMR1", "bomber", "red", 11, 2)],
        "orders": [order(0, "R-BMR1", "launch", target="B-HQ")], "ticks": 16,
        "checks": [("warned", "R-BMR1", 2)],
    },
    {
        "name": "Jet drops to low level far away and holds there",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 10, 10)],
        "orders": [order(1, "R-JET1", "altitude", level=1)], "ticks": 8,
        "checks": [("never", "R-JET1", "hostile", 0, 8)],
    },
    {
        "name": "Fighter orbits at long range",
        "units": blue_post() + [unit("R-JET1", "jet", "red", 10, 1)],
        "orders": [order(0, "R-JET1", "orbit")], "ticks": 10,
        "checks": [("never", "R-JET1", "hostile", 0, 10)],
    },
    {
        "name": "Drone comes in low behind cover to hit a sensor",
        "units": default_units()[:4] + [unit("R-DRN1", "drone", "red", 3, 11)],
        "orders": [order(0, "R-DRN1", "launch", target="B-S1")], "ticks": 14,
        "checks": [("warned", "R-DRN1", 1)],
    },
]
