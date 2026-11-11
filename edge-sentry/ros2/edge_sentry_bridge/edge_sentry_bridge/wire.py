"""Conversions between the ROS 2 messages and the JSON interface in docs/INTERFACE.md.

The planner speaks one-line JSON; ROS 2 speaks typed messages. Nothing is
added or dropped on the way through, so the planner decides the same either way.
"""
from edge_sentry_msgs.msg import Contact, Observation, Order, OwnUnit, Plan


def observation_from_wire(data, stamp):
    msg = Observation()
    msg.stamp = stamp
    msg.version = int(data["v"])
    msg.tick = int(data["tick"])
    msg.width = int(data["width"])
    msg.height = int(data["height"])
    msg.ceiling = int(data["ceiling"])
    msg.watch_at = float(data["watchAt"])
    for u in data["own"]:
        msg.own.append(OwnUnit(id=u["id"], kind=u["type"], x=int(u["x"]), y=int(u["y"]), z=int(u["z"]), hp=float(u["hp"]),
                               action=u["act"], target=u["tgt"], tx=int(u["tx"]), ty=int(u["ty"]), tz=int(u["tz"])))
    for c in data["contacts"]:
        msg.contacts.append(Contact(id=c["id"], cls=c["cls"], x=int(c["x"]), y=int(c["y"]), z=int(c["z"]),
                                    vx=int(c["vx"]), vy=int(c["vy"]), vz=int(c["vz"]), snr=float(c["snr"]),
                                    locked=bool(c["locked"]), score=float(c["score"]), jam=int(c["jam"]), hit=c.get("hit", "")))
    return msg


def observation_to_wire(msg):
    return {
        "v": msg.version, "tick": msg.tick, "width": msg.width, "height": msg.height, "ceiling": msg.ceiling,
        "watchAt": msg.watch_at,
        "own": [{"id": u.id, "type": u.kind, "x": u.x, "y": u.y, "z": u.z, "hp": u.hp, "act": u.action, "tgt": u.target,
                 "tx": u.tx, "ty": u.ty, "tz": u.tz} for u in msg.own],
        "contacts": [{"id": c.id, "cls": c.cls, "x": c.x, "y": c.y, "z": c.z, "vx": c.vx, "vy": c.vy, "vz": c.vz,
                      "snr": c.snr, "locked": 1 if c.locked else 0, "score": c.score, "jam": c.jam, "hit": c.hit}
                     for c in msg.contacts],
    }


def plan_from_wire(data, stamp, tick):
    msg = Plan()
    msg.stamp = stamp
    msg.ok = bool(data.get("ok"))
    msg.tick = int(data.get("tick", tick))
    msg.planning_micros = float(data.get("micros", 0.0))
    for o in data.get("orders", []):
        msg.orders.append(Order(id=o["id"], action=o["action"], target=o["target"], tx=int(o["tx"]), ty=int(o["ty"]),
                                tz=int(o["tz"]), mode=o["mode"], reason=o["reason"]))
    return msg


def order_key(order):
    """What an order does, without the explanation: works for an Order message or a wire dict."""
    get = (lambda k: order[k]) if isinstance(order, dict) else (lambda k: getattr(order, k))
    return (get("id"), get("action"), get("target"), get("tx"), get("ty"), get("tz"))
