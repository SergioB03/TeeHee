#!/usr/bin/env python3
"""Checks for the autonomy planner, through the edge-node program (stdlib only).

Each case hands the planner a hand-written observation and checks the orders
that come back, so the behavior is pinned without running the simulator.

    npm run build:native
    python3 native/tests/test_planner.py            # uses native/build/edge-node
"""
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "build", "edge-node")
if not os.path.exists(NODE):
    print("SKIP  edge-node is not built here (the console's quick build only makes edge-sim)")
    sys.exit(0)

failures = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        failures.append(name)


def own(uid, kind, x, y, z=0, hp=100, act="none", tgt=""):
    return {"id": uid, "type": kind, "x": x, "y": y, "z": z, "hp": hp, "act": act, "tgt": tgt, "tx": -1, "ty": -1, "tz": -1}


def contact(uid, cls, x, y, z, score, vx=0, vy=0, vz=0, locked=1, jam=0, hit=""):
    return {"id": uid, "cls": cls, "x": x, "y": y, "z": z, "vx": vx, "vy": vy, "vz": vz, "snr": 0.6, "locked": locked, "score": score, "jam": jam, "hit": hit}


def plan(own_units, contacts, variant="full", raw=None):
    obs = {"v": 1, "tick": 5, "width": 12, "height": 12, "ceiling": 5, "watchAt": 0.24, "own": own_units, "contacts": contacts}
    line = raw if raw is not None else json.dumps(obs, separators=(",", ":"))
    p = subprocess.run([NODE, "--stdin", "--variant", variant], input=line + "\n", capture_output=True, text=True)
    reply = json.loads(p.stdout)
    return reply, {o["id"]: o for o in reply.get("orders", [])}


BASE = [own("B-HQ", "hq", 2, 2), own("B-S1", "sensor", 1, 4)]

# --- point defense ---------------------------------------------------------------
jet_overhead = contact("R-JET1", "jet", 3, 2, 2, 0.60)
far_missile = contact("R-M1", "missile", 11, 2, 2, 0.95, vx=-1)
_, o = plan(BASE, [jet_overhead, far_missile])
check("point defense covers the jet attacking now, not a missile still 8 ticks out", o["B-HQ"]["target"] == "R-JET1" and o["B-HQ"]["mode"] == "DEFEND")
_, o = plan(BASE, [jet_overhead, far_missile], variant="notimed")
check("the untimed comparison rule picks the missile on score x damage alone", o["B-HQ"]["target"] == "R-M1")
_, o = plan(BASE, [jet_overhead, contact("R-M1", "missile", 4, 2, 2, 0.95, vx=-1)])
check("point defense switches to the missile once it can hit next tick", o["B-HQ"]["target"] == "R-M1")
_, o = plan(BASE, [contact("R-JET1", "jet", 3, 2, 2, 0.60, locked=0), far_missile])
check("a contact with no lock is passed over for one that can be shot at", o["B-HQ"]["target"] == "R-M1")
_, o = plan(BASE, [contact("R-JET1", "jet", 3, 2, 5, 0.60)])
check("a jet still above its release altitude is not yet 'next tick'", "3 ticks" in o["B-HQ"]["reason"])
_, o = plan([own("B-HQ", "hq", 2, 2, act="defend", tgt="R-JET1"), BASE[1]], [jet_overhead, far_missile])
check("no order is repeated when point defense is already on the right contact", "B-HQ" not in o)
_, o = plan([own("B-HQ", "hq", 2, 2, act="defend", tgt="R-GONE"), BASE[1]], [])
check("point defense stands down when nothing is left to cover", o["B-HQ"]["action"] == "cease")
jammer = contact("R-J1", "jammer", 3, 4, 0, 0.9, jam=3)
_, o = plan(BASE, [jammer, contact("R-DRN1", "drone", 9, 9, 1, 0.3)])
check("a jammer holding assets in its field is covered ahead of a distant drone", o["B-HQ"]["target"] == "R-J1")

# --- the fighter -----------------------------------------------------------------
fighter = own("B-F1", "stealth", 5, 5, 4)
bomber = contact("R-BMR1", "bomber", 7, 6, 4, 0.80, vx=-1)
_, o = plan(BASE + [fighter], [contact("R-M1", "missile", 4, 2, 2, 0.95, vx=-1), bomber])
check("the fighter takes the bomber and leaves the missile about to hit to point defense",
      o["B-F1"]["action"] == "launch" and o["B-F1"]["target"] == "R-BMR1" and o["B-F1"]["mode"] == "ENGAGE" and o["B-HQ"]["target"] == "R-M1")
_, o = plan(BASE + [fighter], [contact("R-JET1", "jet", 7, 5, 3, 0.10)])
check("hold fire: a contact below the watch line is shadowed, not shot at", o["B-F1"]["mode"] == "SHADOW" and o["B-F1"]["action"] == "move")
_, o = plan(BASE + [fighter], [contact("R-JET1", "jet", 7, 5, 3, 0.10)], variant="freefire")
check("the free-fire comparison variant does shoot at it", o["B-F1"]["action"] == "launch")
_, o = plan(BASE + [fighter], [contact("R-JET1", "jet", 7, 5, 3, 0.80)])
check("the same contact labelled a threat is engaged", o["B-F1"]["action"] == "launch" and o["B-F1"]["target"] == "R-JET1")
far = contact("R-BMR1", "bomber", 11, 11, 4, 0.80, vx=-1, vy=-1)
_, o = plan(BASE + [own("B-F1", "stealth", 0, 0, 4)], [far])
check("a distant target is intercepted where it will be, not where it is",
      o["B-F1"]["mode"] == "INTERCEPT" and o["B-F1"]["action"] == "move" and (o["B-F1"]["tx"], o["B-F1"]["ty"]) != (11, 11) and o["B-F1"]["tz"] == 4)
_, o = plan(BASE + [own("B-F1", "stealth", 0, 0, 4)], [far], variant="nolead")
check("without lead it simply pursues", o["B-F1"]["action"] == "launch")
_, o = plan(BASE + [fighter], [contact("R-GS1", "sensor", 6, 5, 0, 0.9)])
check("the fighter never goes after a ground unit", o["B-F1"]["mode"] == "PATROL")
hurt = own("B-F1", "stealth", 9, 9, 4, hp=40)
armed = contact("R-JET1", "jet", 7, 7, 3, 0.80, vx=-1, vy=-1)
_, o = plan(BASE + [hurt], [armed])
check("a damaged fighter leaves an armed aircraft that has done nothing alone and returns to headquarters",
      o["B-F1"]["mode"] == "RTB" and (o["B-F1"]["tx"], o["B-F1"]["ty"]) == (2, 2))
_, o = plan(BASE + [hurt], [bomber])
check("a damaged fighter still goes after a bomber, which cannot shoot back", o["B-F1"]["mode"] in ("ENGAGE", "INTERCEPT"))
_, o = plan(BASE + [hurt], [dict(armed, hit="B-S1")])
check("a damaged fighter still goes after an armed aircraft already seen hitting a blue unit",
      o["B-F1"]["mode"] in ("ENGAGE", "INTERCEPT"))
_, o = plan(BASE + [hurt], [bomber], variant="gohome")
check("the go-home comparison variant sends a damaged fighter back whatever is out there",
      o["B-F1"]["mode"] == "RTB" and (o["B-F1"]["tx"], o["B-F1"]["ty"]) == (2, 2))
# --- self-defense ----------------------------------------------------------------
hunter = contact("R-F1", "stealth", 6, 5, 4, 0.10, vx=-1, hit="B-F1")
_, o = plan(BASE + [fighter], [hunter])
check("self-defense: a contact that has hit our fighter is shot at even below the watch line",
      o["B-F1"]["action"] == "launch" and o["B-F1"]["target"] == "R-F1")
_, o = plan(BASE + [fighter], [hunter], variant="nodefense")
check("without the self-defense rule it would not have shot back", o.get("B-F1", {}).get("action") != "launch")
_, o = plan(BASE + [own("B-F1", "stealth", 5, 5, 4, hp=50)], [hunter])
check("a damaged fighter does not turn and run from the contact hunting it: it fights",
      o["B-F1"]["action"] == "launch" and o["B-F1"]["target"] == "R-F1")
_, o = plan(BASE + [own("B-F1", "stealth", 5, 5, 4, hp=50)], [hunter, bomber])
check("damaged and hunted, it fights the hunter before anything else", o["B-F1"]["target"] == "R-F1")
_, o = plan(BASE + [fighter], [hunter, bomber])
check("the contact shooting at this fighter comes before a bomber", o["B-F1"]["target"] == "R-F1")
_, o = plan(BASE + [fighter], [hunter])
check("point defense covers the contact that is hunting our fighter", o["B-HQ"]["target"] == "R-F1")
_, o = plan(BASE + [fighter], [contact("R-F1", "stealth", 6, 5, 4, 0.10, vx=-1)])
check("a fighter that is merely close and has not fired is not shot at", o.get("B-F1", {}).get("action") != "launch")
_, o = plan(BASE + [fighter], [contact("R-F1", "stealth", 6, 5, 4, 0.10, vx=-1)], variant="closein")
check("the close-in comparison variant treats that approach as hostile", o["B-F1"]["action"] == "launch")
_, o = plan(BASE + [fighter], [contact("R-M1", "missile", 6, 5, 4, 0.10, vx=-1, hit="B-F1")])
check("a missile is never treated as a hunter to dogfight", o.get("B-F1", {}).get("action") != "launch")

_, o = plan(BASE + [own("B-F1", "stealth", 10, 10, 4)], [])
check("with no contacts the fighter moves to a patrol station by the assets", o["B-F1"]["mode"] == "PATROL" and o["B-F1"]["tx"] <= 3 and o["B-F1"]["ty"] <= 4)
_, o = plan(BASE + [own("B-F1", "stealth", 2, 3, 3, act="orbit")], [])
check("on station and orbiting, it gets no new order", "B-F1" not in o)
_, o = plan(BASE + [own("B-F1", "stealth", 5, 5, 4, act="launch", tgt="R-BMR1")], [bomber])
check("already engaging the chosen target, it gets no new order", "B-F1" not in o)
two = [own("B-F1", "stealth", 5, 5, 4), own("B-F2", "jet", 5, 6, 3)]
_, o = plan(BASE + two, [bomber, contact("R-JET1", "jet", 7, 4, 3, 0.8)])
check("two fighters take two different targets", {o["B-F1"]["target"], o["B-F2"]["target"]} == {"R-BMR1", "R-JET1"})
_, o = plan([fighter], [bomber])
check("with no assets to protect there is nothing to plan", o == {})

# --- the message format ----------------------------------------------------------
reply, _ = plan([], [], raw='{"ping":1}')
check("ping is answered", reply.get("pong") == 1)
reply, _ = plan([], [], raw="not json at all")
check("a malformed message gets an error reply, not a crash", reply["ok"] is False and "observation" in reply["error"])
reply, _ = plan([], [], raw='{"v":2,"tick":1,"own":[],"contacts":[]}')
check("an unknown message version is refused", reply["ok"] is False)
reply, o = plan(BASE + [fighter], [bomber])
check("the reply reports its tick and how long planning took", reply["tick"] == 5 and reply["micros"] >= 0 and reply["v"] == 1)

# --- over TCP: several messages on one connection, and a restart ------------------
port = 47000 + os.getpid() % 1000
node = subprocess.Popen([NODE, "--listen", str(port)], stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            conn = socket.create_connection(("127.0.0.1", port), timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    stream = conn.makefile("rw")
    obs = {"v": 1, "tick": 1, "width": 12, "height": 12, "ceiling": 5, "watchAt": 0.24, "own": BASE, "contacts": [jet_overhead]}
    answers = []
    for tick in (1, 2, 3):
        obs["tick"] = tick
        stream.write(json.dumps(obs) + "\n")
        stream.flush()
        answers.append(json.loads(stream.readline()))
    check("a TCP connection carries one plan per observation, in order", [a["tick"] for a in answers] == [1, 2, 3] and all(a["ok"] for a in answers))
    conn.close()
    conn = socket.create_connection(("127.0.0.1", port), timeout=1)
    stream = conn.makefile("rw")
    stream.write(json.dumps(obs) + "\n")
    stream.flush()
    check("after a client disconnects the node accepts the next one", json.loads(stream.readline())["ok"] is True)
    conn.close()

    # Several connections at once: a health probe or a second console must not wait for the first to leave.
    first = socket.create_connection(("127.0.0.1", port), timeout=3)
    second = socket.create_connection(("127.0.0.1", port), timeout=3)
    one, two = first.makefile("rw"), second.makefile("rw")

    def ask(stream, text):
        stream.write(text + "\n")
        stream.flush()
        return json.loads(stream.readline())

    pong = ask(two, '{"ping":1}')
    obs["tick"] = 9
    late = ask(one, json.dumps(obs))
    check("a second client is answered while the first stays connected", pong.get("pong") == 1 and late["tick"] == 9 and late["ok"])
    check("--ping reports a live node, even while other clients are connected",
          subprocess.run([NODE, "--ping", "127.0.0.1:%d" % port]).returncode == 0)
    one.write('{"v":1,"tick":10')  # half a message, no newline yet
    one.flush()
    check("a client that has sent half a message does not hold up another", ask(two, '{"ping":1}').get("pong") == 1)
    one.write(',"width":12,"height":12,"ceiling":5,"watchAt":0.24,"own":[],"contacts":[]}\n')
    one.flush()
    check("and its own message is answered once the rest of it arrives", json.loads(one.readline())["tick"] == 10)
    crowd = [socket.create_connection(("127.0.0.1", port), timeout=3) for _ in range(40)]
    time.sleep(0.3)
    check("with more connections than it will hold, the node turns the extra away and keeps serving",
          ask(two, '{"ping":1}').get("pong") == 1 and ask(one, json.dumps(obs))["ok"])
    for c in crowd + [first, second]:
        c.close()
finally:
    node.terminate()
    try:
        stopped = node.wait(timeout=3)
    except subprocess.TimeoutExpired:
        node.kill()
        stopped = None
check("asked to terminate, the node stops at once with status 0", stopped == 0)
check("--ping reports failure when nothing is listening", subprocess.run([NODE, "--ping", "127.0.0.1:%d" % port]).returncode == 1)
check("--ping refuses an address it cannot read",
      subprocess.run([NODE, "--ping", "nonsense"], stderr=subprocess.DEVNULL).returncode == 2)

print()
if failures:
    print("%d check(s) FAILED" % len(failures))
    sys.exit(1)
print("all checks passed")
