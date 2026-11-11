#!/usr/bin/env python3
"""Several visitors use the web console at the same time, on different sides.

    python3 web/test_sessions.py

Standard library only. Starts its own server on a spare port with a throwaway
world directory, then runs twelve visitors in parallel: three each playing red
on autopilot, red by hand, blue on autopilot, and the free sandbox with both
autopilots on. It checks that autopilot is offered for whichever side a
visitor commands, that it orders only that side, and that no visitor's game,
clock or switches leak into another's. The simulator is deterministic, so
visitors doing the same thing must end with exactly the same result: any
cross-talk between sessions shows up as a difference.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EACH = 3  # visitors per role

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
workdir = tempfile.mkdtemp(prefix="edge-sentry-web-")
server = subprocess.Popen(["node", "web/server.mjs"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                          env={**os.environ, "PORT": str(PORT), "EDGE_WORLD": os.path.join(workdir, "world.json")})
statuses = []


def call(sid, route, body=None):
    """One request as visitor `sid` (None = a client that sends no session id). Returns (status, json)."""
    headers = {"content-type": "application/json"}
    if sid:
        headers["x-session"] = sid
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + route, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = (r.status, json.load(r))
    except urllib.error.HTTPError as e:
        out = (e.code, json.load(e))
    statuses.append(out[0])
    return out


def ordered(world, team):
    """Ids of a team's live units that have an order."""
    return sorted(u["id"] for u in world["units"] if u["team"] == team and u["alive"] and u["order"]["action"] != "none")


def result(game):
    return (round(game["missionValue"], 6), game["hits"], game["blocked"], game["lost"], game["shotDown"], game["tick"], game["over"])


def finish(sid, checks, name):
    """Advance a game to its last tick and check that it then refuses to go on."""
    reply = None
    while reply is None or not reply["game"]["over"]:
        _, reply = call(sid, "/api/tick", {"n": 5})
    code, _ = call(sid, "/api/tick", {"n": 1})
    checks.append((name, reply["game"]["tick"] == 20 and code == 400))
    return reply


def red_on_autopilot(sid):
    checks = []
    _, s = call(sid, "/api/state")
    checks.append(("a new visitor starts in the sandbox with red autopilot on and blue autopilot off",
                   s["game"] is None and s["auto"] == {"red": True, "blue": False}))
    _, s = call(sid, "/api/game/start", {"side": "red"})
    checks.append(("a red game starts with autopilot off", s["game"]["side"] == "red" and s["game"]["autopilot"] is False))
    code, s = call(sid, "/api/game/autopilot", {"on": True})
    checks.append(("autopilot can be switched on when commanding red", code == 200 and s["game"]["autopilot"] is True))
    _, t = call(sid, "/api/tick", {"n": 5})
    mine, theirs = t["autopilotOrders"], t["opponentOrders"]
    checks.append(("red autopilot orders red units, and only red units",
                   len(mine) >= 3 and all(o["id"].startswith("R-") for o in mine) and len(ordered(t["world"], "red")) >= 3))
    checks.append(("with red on autopilot the computer still flies blue against it",
                   len(theirs) >= 1 and all(o["id"].startswith("B-") for o in theirs)))
    _, s = call(sid, "/api/state")
    checks.append(("the autopilot setting and the clock survive a reload", s["game"]["autopilot"] is True and s["game"]["tick"] == 5))
    _, log = call(sid, "/api/decisions")
    who = {(d["actor"], d["args"]["id"][:2]) for d in log["decisions"] if d["command"] == "order"}
    checks.append(("the decision log names red's autopilot and blue's computer opponent separately",
                   who == {("autopilot", "R-"), ("opponent", "B-")}))
    end = finish(sid, checks, "a red game on autopilot runs to tick 20 and then refuses to go on")
    checks.append(("red on autopilot lands hits on blue", end["game"]["hits"] >= 1))
    return checks, result(end["game"])


def red_by_hand(sid):
    checks = []
    call(sid, "/api/game/start", {"side": "red"})
    _, t = call(sid, "/api/tick", {"n": 3})
    checks.append(("with autopilot off, red does nothing until the player orders it",
                   t["autopilotOrders"] == [] and ordered(t["world"], "red") == []))
    code, _ = call(sid, "/api/game/autopilot", {"on": "yes"})
    checks.append(("the autopilot switch refuses anything but true or false", code == 400))
    code, o = call(sid, "/api/order", {"id": "R-MSL1", "action": "launch", "target": "B-HQ", "reason": "by hand"})
    checks.append(("a hand order is accepted with autopilot off", code == 200 and o["ok"] is True))
    code, _ = call(sid, "/api/order", {"id": "B-HQ", "action": "cease"})
    checks.append(("a red player still cannot order blue's units", code == 400))
    _, t = call(sid, "/api/tick", {"n": 2})
    checks.append(("only the unit ordered by hand has an order", ordered(t["world"], "red") == ["R-MSL1"] and t["game"]["tick"] == 5))
    end = finish(sid, checks, "a red game played by hand runs to tick 20 and then refuses to go on")
    return checks, result(end["game"])


def blue_on_autopilot(sid):
    checks = []
    _, s = call(sid, "/api/game/start", {"side": "blue"})
    checks.append(("a blue game starts with autopilot off", s["game"]["side"] == "blue" and s["game"]["autopilot"] is False))
    code, s = call(sid, "/api/game/autopilot", {"on": True})
    checks.append(("autopilot can be switched on when commanding blue", code == 200 and s["game"]["autopilot"] is True))
    _, t = call(sid, "/api/tick", {"n": 5})
    mine, theirs = t["autopilotOrders"], t["opponentOrders"]
    checks.append(("blue autopilot orders blue units, and only blue units", len(mine) >= 1 and all(o["id"].startswith("B-") for o in mine)))
    checks.append(("with blue on autopilot the computer still flies red against it",
                   len(theirs) >= 3 and all(o["id"].startswith("R-") for o in theirs)))
    end = finish(sid, checks, "a blue game on autopilot runs to tick 20 and then refuses to go on")
    checks.append(("blue on autopilot blocks a hit or shoots something down", end["game"]["blocked"] + end["game"]["shotDown"] >= 1))
    _, s = call(sid, "/api/game/start", {"side": "red"})
    checks.append(("switching sides starts the new game with autopilot off", s["game"]["side"] == "red" and s["game"]["autopilot"] is False))
    call(sid, "/api/game/end", {})
    return checks, result(end["game"])


def sandbox(sid):
    checks = []
    code, _ = call(sid, "/api/game/autopilot", {"on": True})
    checks.append(("the game autopilot switch is refused when no game is running", code == 400))
    _, s = call(sid, "/api/auto", {"blue": True})
    checks.append(("sandbox: blue autopilot switches on without touching red's", s["auto"] == {"red": True, "blue": True}))
    _, t = call(sid, "/api/tick", {"n": 6})
    checks.append(("sandbox: with both on, red's computer orders red and blue's orders blue",
                   len(t["opponentOrders"]) >= 1 and all(o["id"].startswith("R-") for o in t["opponentOrders"])
                   and len(t["autopilotOrders"]) >= 1 and all(o["id"].startswith("B-") for o in t["autopilotOrders"])))
    _, log = call(sid, "/api/decisions")
    checks.append(("sandbox: both computers are logged as autopilots, with reasons",
                   {(d["actor"], d["args"]["id"][:2]) for d in log["decisions"] if d["command"] == "order"} == {("autopilot", "R-"), ("autopilot", "B-")}
                   and all(d["reason"] for d in log["decisions"] if d["command"] == "order")))
    _, s = call(sid, "/api/auto", {"red": False})
    checks.append(("sandbox: red autopilot switches off without touching blue's", s["auto"] == {"red": False, "blue": True}))
    code, _ = call(sid, "/api/auto", {"red": "off"})
    checks.append(("sandbox: the switches refuse anything but true or false", code == 400))
    _, s = call(sid, "/api/state")
    units = tuple((u["id"], u["x"], u["y"], u["z"], round(u["hp"], 3), u["alive"]) for u in s["world"]["units"])
    return checks, (s["world"]["tick"], s["game"], s["auto"]["red"], s["auto"]["blue"], units)


failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


try:
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/api/state", timeout=2).read()
            break
        except OSError:
            time.sleep(0.1)
    else:
        print("the server did not start")
        sys.exit(1)

    _, shared_before = call(None, "/api/state")
    roles = [("red on autopilot", red_on_autopilot), ("red by hand", red_by_hand), ("blue on autopilot", blue_on_autopilot), ("sandbox", sandbox)]
    visitors = [(label, fn, f"visitor{r}{i}{'x' * 8}") for r, (label, fn) in enumerate(roles) for i in range(EACH)]
    with ThreadPoolExecutor(max_workers=len(visitors)) as pool:
        done = list(pool.map(lambda v: v[1](v[2]), visitors))

    # Every per-visitor check must hold for all the visitors in that role.
    merged = {}
    for checks, _ in done:
        for name, ok in checks:
            merged[name] = merged.get(name, True) and ok
    for name, ok in merged.items():
        check(name, ok)

    by_role = {}
    for (label, _, _), (_, outcome) in zip(visitors, done):
        by_role.setdefault(label, []).append(outcome)
    check("visitors doing the same thing at the same time end with exactly the same result",
          all(len(set(map(repr, outcomes))) == 1 for outcomes in by_role.values()))
    check("red on autopilot and red by hand are different games with different results",
          by_role["red on autopilot"][0] != by_role["red by hand"][0])
    check("each sandbox visitor's clock moved only by their own ticks", all(o[0] == 6 and o[1] is None for o in by_role["sandbox"]))

    # Game visitors still have their own game afterwards; one visitor's switches never reached another.
    after = {sid: call(sid, "/api/state")[1] for _, _, sid in visitors}
    check("after everything, each visitor still has their own game or sandbox",
          all((after[sid]["game"] is not None and after[sid]["game"]["side"] == "red" and after[sid]["game"]["over"]) if label.startswith("red")
              else after[sid]["game"] is None for label, _, sid in visitors))
    check("sandbox switches changed by some visitors did not change anyone else's",
          all(after[sid]["auto"] == ({"red": False, "blue": True} if label == "sandbox" else {"red": True, "blue": False}) for label, _, sid in visitors))
    _, shared_after = call(None, "/api/state")
    check("a client that sends no session id is untouched by all of them",
          shared_after["world"]["tick"] == shared_before["world"]["tick"] and shared_after["game"] is None
          and shared_after["auto"] == shared_before["auto"])

    # Two tabs, or a double tap: parallel requests in one session are applied one after another, none lost.
    sid = "doubletap" + "x" * 8
    call(sid, "/api/reset", {})
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: call(sid, "/api/tick", {"n": 1}), range(8)))
    check("eight ticks sent at once in one session all count, exactly once each",
          all(code == 200 for code, _ in replies) and call(sid, "/api/state")[1]["world"]["tick"] == 8)
    check("no request got a server error", all(code < 500 for code in statuses))
    print(f"\n{len(visitors)} visitors in parallel, {len(statuses)} requests")
finally:
    server.terminate()

if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
