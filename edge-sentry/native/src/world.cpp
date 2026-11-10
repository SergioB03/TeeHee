#include "world.hpp"
#include "json_mini.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>

namespace {
int clampi(int v, int lo, int hi) { return std::max(lo, std::min(hi, v)); }

// Hidden state is written with enough digits that reloading it between CLI
// calls does not drift from an in-process run.
std::string joinNumbers(const std::vector<double>& values) {
  std::ostringstream o;
  o.precision(12);
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) o << ",";
    o << values[i];
  }
  return o.str();
}

std::vector<double> parseNumbers(const std::string& body) {
  std::vector<double> out;
  std::stringstream in(body);
  std::string item;
  while (std::getline(in, item, ',')) {
    try {
      out.push_back(std::stod(item));
    } catch (...) {
      // skip anything that is not a number
    }
  }
  return out;
}

std::string nextId(const std::string& prefix, const std::vector<Unit>& units) {
  int n = static_cast<int>(units.size()) + 1;
  return prefix + std::to_string(n);
}
}  // namespace

std::string unitTypeToString(UnitType t) {
  switch (t) {
    case UnitType::Sensor: return "sensor";
    case UnitType::Relay: return "relay";
    case UnitType::Jammer: return "jammer";
    case UnitType::Jet: return "jet";
    case UnitType::Missile: return "missile";
    case UnitType::HQ: return "hq";
  }
  return "sensor";
}

bool unitTypeFromString(const std::string& s, UnitType& out) {
  if (s == "sensor") out = UnitType::Sensor;
  else if (s == "relay") out = UnitType::Relay;
  else if (s == "jammer") out = UnitType::Jammer;
  else if (s == "jet") out = UnitType::Jet;
  else if (s == "missile") out = UnitType::Missile;
  else if (s == "hq") out = UnitType::HQ;
  else return false;
  return true;
}

std::string teamToString(Team t) {
  switch (t) {
    case Team::Blue: return "blue";
    case Team::Red: return "red";
    case Team::Unknown: return "unknown";
  }
  return "unknown";
}

bool teamFromString(const std::string& s, Team& out) {
  if (s == "blue") out = Team::Blue;
  else if (s == "red") out = Team::Red;
  else if (s == "unknown") out = Team::Unknown;
  else return false;
  return true;
}

std::string orderToString(OrderAction a) {
  switch (a) {
    case OrderAction::None: return "none";
    case OrderAction::Move: return "move";
    case OrderAction::Jam: return "jam";
    case OrderAction::Launch: return "launch";
    case OrderAction::Cease: return "cease";
    case OrderAction::Defend: return "defend";
  }
  return "none";
}

bool orderFromString(const std::string& s, OrderAction& out) {
  if (s == "none") out = OrderAction::None;
  else if (s == "move") out = OrderAction::Move;
  else if (s == "jam") out = OrderAction::Jam;
  else if (s == "launch") out = OrderAction::Launch;
  else if (s == "cease") out = OrderAction::Cease;
  else if (s == "defend") out = OrderAction::Defend;
  else return false;
  return true;
}

World World::defaultScenario() {
  World w;
  w.width = 12;
  w.height = 12;
  w.cover.assign(static_cast<size_t>(w.width * w.height), 0);
  // Sparse cover ridges
  for (int x = 0; x < w.width; ++x) {
    w.cover[static_cast<size_t>(4 * w.width + x)] = (x % 3 == 0) ? 1 : 0;
  }

  auto add = [&](const std::string& id, UnitType type, Team team, int x, int y, int jam = 0) {
    Unit u;
    u.id = id;
    u.type = type;
    u.team = team;
    u.x = x;
    u.y = y;
    u.px = x;
    u.py = y;
    if (type == UnitType::Jammer) u.jamRadius = jam > 0 ? jam : 3;
    w.units.push_back(u);
  };

  add("B-HQ", UnitType::HQ, Team::Blue, 1, 1);
  add("B-S1", UnitType::Sensor, Team::Blue, 2, 3);
  add("B-S2", UnitType::Sensor, Team::Blue, 3, 8);
  add("B-R1", UnitType::Relay, Team::Blue, 4, 5);
  // Close enough that B-R1 starts under the jam cone (REVIEW_FINDINGS #7).
  add("R-J1", UnitType::Jammer, Team::Red, 5, 6, 3);
  add("R-JET1", UnitType::Jet, Team::Red, 10, 2);
  add("R-MSL1", UnitType::Missile, Team::Red, 8, 3);

  w.units.back().targetId = "B-HQ";
  // Apply one integrity pass so the first status shows EW effects.
  for (auto& u : w.units) {
    if (!u.alive || u.team != Team::Blue) continue;
    if (!(u.type == UnitType::Sensor || u.type == UnitType::Relay || u.type == UnitType::HQ)) continue;
    const double jam = w.jamFieldAt(u.x, u.y, Team::Blue);
    u.integrity = std::max(0.2, 1.0 - 0.7 * jam);
  }
  w.events.push_back({0, "scenario", "default perimeter / EW sandbox loaded"});
  w.scoreAllTracks();
  return w;
}

ScorerKind World::activeScorer = ScorerKind::Learned;

World World::skirmishScenario() {
  World w = defaultScenario();
  w.units.clear();
  w.events.clear();
  auto add = [&](const std::string& id, UnitType type, Team team, int x, int y) {
    Unit u;
    u.id = id;
    u.type = type;
    u.team = team;
    u.x = x;
    u.y = y;
    u.px = x;
    u.py = y;
    if (type == UnitType::Jammer) u.jamRadius = 3;
    w.units.push_back(u);
  };
  add("B-HQ", UnitType::HQ, Team::Blue, 1, 1);
  add("B-S1", UnitType::Sensor, Team::Blue, 2, 3);
  add("B-S2", UnitType::Sensor, Team::Blue, 3, 8);
  add("B-R1", UnitType::Relay, Team::Blue, 4, 5);
  add("R-J1", UnitType::Jammer, Team::Red, 9, 8);
  add("R-JET1", UnitType::Jet, Team::Red, 10, 2);
  add("R-JET2", UnitType::Jet, Team::Red, 11, 10);
  add("R-MSL1", UnitType::Missile, Team::Red, 8, 3);
  add("R-MSL2", UnitType::Missile, Team::Red, 11, 6);
  w.events.push_back({0, "scenario", "skirmish loaded: nobody has orders yet"});
  w.scoreAllTracks();
  return w;
}

// The computer opponent. Blue is run by the learned threat model: its one
// defensive slot goes to the contact with the highest score x severity.
// Red follows a short fixed doctrine. Both only issue orders a player could.
std::vector<OpponentMove> World::planOpponent(Team side) const {
  std::vector<OpponentMove> moves;
  auto nearestBlue = [&](const Unit& from, bool sensorsFirst) -> const Unit* {
    const Unit* best = nullptr;
    double bestD = 1e9;
    for (int pass = 0; pass < 2 && !best; ++pass) {
      for (const auto& b : units) {
        if (!b.alive || b.team != Team::Blue) continue;
        const bool isHq = b.type == UnitType::HQ;
        if (sensorsFirst && pass == 0 && isHq) continue;
        const double d = dist(b.x, b.y, from.x, from.y);
        if (d < bestD) {
          bestD = d;
          best = &b;
        }
      }
    }
    return best;
  };

  if (side == Team::Blue) {
    const Unit* hq = nullptr;
    for (const auto& u : units) {
      if (u.alive && u.team == Team::Blue && u.type == UnitType::HQ) {
        hq = &u;
        break;
      }
    }
    if (!hq) return moves;
    const Track* best = nullptr;
    double bestValue = 0;
    for (const auto& t : tracks) {
      const Unit* u = findUnit(t.sourceUnitId);
      if (!u || !u->alive || t.threat.learnedScore < learnedWatchAt()) continue;
      const double severity = u->type == UnitType::Missile ? 40 : u->type == UnitType::Jet ? 15 : u->type == UnitType::Jammer ? 3 : 0;
      const double value = t.threat.learnedScore * severity;
      if (value > bestValue) {
        bestValue = value;
        best = &t;
      }
    }
    const bool defending = hq->order.action == OrderAction::Defend;
    if (best && !(defending && hq->order.targetId == best->sourceUnitId)) {
      std::ostringstream why;
      why.precision(2);
      why << best->sourceUnitId << " reads " << labelForScore(best->threat.learnedScore, ScorerKind::Learned) << " at "
          << std::fixed << best->threat.learnedScore << ", the highest expected loss";
      OpponentMove m;
      m.id = hq->id;
      m.action = OrderAction::Defend;
      m.targetId = best->sourceUnitId;
      m.reason = why.str();
      moves.push_back(m);
    } else if (!best && defending) {
      OpponentMove m;
      m.id = hq->id;
      m.action = OrderAction::Cease;
      m.reason = "no contact is above the watch line";
      moves.push_back(m);
    }
    return moves;
  }

  for (const auto& u : units) {
    if (!u.alive || u.team != Team::Red) continue;
    if (u.type == UnitType::Missile || u.type == UnitType::Jet) {
      const Unit* current = u.targetId.empty() ? nullptr : findUnit(u.targetId);
      if (current && current->alive) continue;  // already committed
      const Unit* tgt = nearestBlue(u, u.type == UnitType::Jet);
      if (u.type == UnitType::Missile) {
        // Missiles go for the command post while it stands.
        for (const auto& b : units) {
          if (b.alive && b.team == Team::Blue && b.type == UnitType::HQ) tgt = &b;
        }
      }
      if (!tgt) continue;
      OpponentMove m;
      m.id = u.id;
      m.action = OrderAction::Launch;
      m.targetId = tgt->id;
      m.reason = u.type == UnitType::Missile ? "strike the command post" : "suppress the nearest sensor";
      moves.push_back(m);
    } else if (u.type == UnitType::Jammer) {
      bool covering = false;
      for (const auto& b : units) {
        if (!b.alive || b.team != Team::Blue) continue;
        if (dist(b.x, b.y, u.x, u.y) < u.jamRadius) covering = true;
      }
      if (covering) continue;
      const Unit* tgt = nearestBlue(u, true);
      if (!tgt) continue;
      // Stop one cell short of the asset, on the jammer's side of it.
      const int tx = clampi(tgt->x + (u.x > tgt->x ? 1 : u.x < tgt->x ? -1 : 0), 0, width - 1);
      const int ty = clampi(tgt->y + (u.y > tgt->y ? 1 : u.y < tgt->y ? -1 : 0), 0, height - 1);
      if (u.order.action == OrderAction::Move && u.order.tx == tx && u.order.ty == ty) continue;
      OpponentMove m;
      m.id = u.id;
      m.action = OrderAction::Move;
      m.tx = tx;
      m.ty = ty;
      m.reason = "close in to jam " + tgt->id;
      moves.push_back(m);
    }
  }
  return moves;
}

void World::reset() { *this = defaultScenario(); }

Unit* World::findUnit(const std::string& id) {
  for (auto& u : units) if (u.id == id) return &u;
  return nullptr;
}

const Unit* World::findUnit(const std::string& id) const {
  for (const auto& u : units) if (u.id == id) return &u;
  return nullptr;
}

double World::dist(int x1, int y1, int x2, int y2) const {
  const double dx = static_cast<double>(x1 - x2);
  const double dy = static_cast<double>(y1 - y2);
  return std::sqrt(dx * dx + dy * dy);
}

double World::jamFieldAt(int x, int y, Team against) const {
  // Only opposing jammers contribute (team-aware EW).
  double pressure = 0;
  for (const auto& u : units) {
    if (!u.alive || u.type != UnitType::Jammer) continue;
    if (u.jamRadius <= 0) continue;
    if (against != Team::Unknown && u.team == against) continue;
    if (isDefended(u.id)) continue;  // point defense suppresses this emitter
    const double d = dist(u.x, u.y, x, y);
    if (d <= u.jamRadius) {
      pressure = std::max(pressure, 1.0 - (d / static_cast<double>(u.jamRadius)));
    }
  }
  return pressure;
}

bool World::spawn(UnitType type, Team team, int x, int y, std::string* err) {
  if (x < 0 || y < 0 || x >= width || y >= height) {
    if (err) *err = "spawn out of bounds";
    return false;
  }
  Unit u;
  u.id = nextId(team == Team::Blue ? "B" : "R", units);
  u.type = type;
  u.team = team;
  u.x = x;
  u.y = y;
  u.px = x;
  u.py = y;
  if (type == UnitType::Jammer) u.jamRadius = 3;
  units.push_back(u);
  events.push_back({tick, "spawn", u.id + " " + unitTypeToString(type)});
  return true;
}

bool World::issueOrder(const std::string& id, OrderAction action, int tx, int ty,
                       const std::string& targetId, std::string* err) {
  Unit* u = findUnit(id);
  if (!u || !u->alive) {
    if (err) *err = "unit not found or destroyed";
    return false;
  }
  if (action == OrderAction::Cease) {
    u->order = {};
    // Also drop the standing target: tickOnce() engages whenever targetId is
    // set, so leaving it meant a "ceased" jet/missile kept attacking.
    u->targetId.clear();
    events.push_back({tick, "order", id + " cease"});
    return true;
  }
  if (action == OrderAction::Jam && u->type != UnitType::Jammer) {
    if (err) *err = "only jammers can jam";
    return false;
  }
  if (action == OrderAction::Launch && !(u->type == UnitType::Jet || u->type == UnitType::Missile)) {
    if (err) *err = "only jet/missile can launch";
    return false;
  }
  if (action == OrderAction::Defend) {
    // Abstract point defense: an HQ covers against one contact at a time.
    if (u->type != UnitType::HQ) {
      if (err) *err = "only an hq can defend";
      return false;
    }
    const Unit* tgt = targetId.empty() ? nullptr : findUnit(targetId);
    if (!tgt || !tgt->alive || tgt->team == u->team) {
      if (err) *err = "defend target must be a live opposing unit";
      return false;
    }
    u->order = {};
    u->order.action = action;
    u->order.targetId = targetId;
    events.push_back({tick, "order", id + " defend " + targetId});
    return true;
  }
  if (action == OrderAction::Move && (tx < 0 || ty < 0 || tx >= width || ty >= height)) {
    if (err) *err = "move needs --tx/--ty inside the map";
    return false;
  }
  if (action == OrderAction::Launch) {
    const std::string& want = targetId.empty() ? u->targetId : targetId;
    const Unit* tgt = want.empty() ? nullptr : findUnit(want);
    if (!tgt || !tgt->alive) {
      if (err) *err = "launch target not found or destroyed";
      return false;
    }
    if (tgt == u) {
      if (err) *err = "unit cannot target itself";
      return false;
    }
  }
  u->order.action = action;
  u->order.tx = tx;
  u->order.ty = ty;
  u->order.targetId = targetId;
  if (!targetId.empty()) u->targetId = targetId;
  events.push_back({tick, "order", id + " " + orderToString(action)});
  return true;
}

bool World::isDefended(const std::string& id, std::string* byHq) const {
  for (const auto& u : units) {
    if (!u.alive || u.type != UnitType::HQ) continue;
    if (u.order.action == OrderAction::Defend && u.order.targetId == id) {
      if (byHq) *byHq = u.id;
      return true;
    }
  }
  return false;
}

// Range rate and bearing are taken against every blue asset and the strongest
// is kept: the asset a contact is flying at is often not the nearest one.
Kinematics World::kinematics(const Unit& u) const {
  Kinematics k;
  const double vx = u.x - u.px;
  const double vy = u.y - u.py;
  const double speed = std::sqrt(vx * vx + vy * vy);
  for (const auto& b : units) {
    if (!b.alive || b.team != Team::Blue) continue;
    const double now = dist(b.x, b.y, u.x, u.y);
    const double before = dist(b.x, b.y, u.px, u.py);
    k.nearest = std::min(k.nearest, now);
    k.closing = std::max(k.closing, (before - now) / 2.0);
    // Time to intercept: at this range rate, how soon does it arrive?
    if (before - now > 0) {
      const double ticksOut = now / (before - now);
      k.intercept = std::max(k.intercept, 1.0 - ticksOut / 10.0);
    }
    if (speed <= 0) continue;
    // Bearing uses the position before the step so arriving on top of an
    // asset still counts as heading straight at it.
    double bearing = 0;
    if (before > 0) {
      const double toward = vx * (b.x - u.px) + vy * (b.y - u.py);
      bearing = toward / (speed * before);
    }
    if (bearing > k.heading) {
      k.heading = bearing;
      k.aimRange = now > 12 ? 0 : 1.0 - now / 12.0;
      // Perpendicular distance from the asset to the contact's line of motion.
      const double off = before * std::sqrt(std::max(0.0, 1.0 - bearing * bearing));
      k.miss = std::max(0.0, 1.0 - off / 3.0);
    }
  }
  k.closing = std::max(0.0, std::min(1.0, k.closing));
  k.heading = std::max(0.0, std::min(1.0, k.heading));
  k.intercept = std::max(0.0, std::min(1.0, k.intercept));
  return k;
}

// Called once at the end of every tick: shift each contact's short history.
void World::recordHistory() {
  for (auto& u : units) {
    if (!u.alive) continue;
    const Kinematics k = kinematics(u);
    u.c2 = u.c1; u.c1 = u.c0; u.c0 = k.closing;
    u.h2 = u.h1; u.h1 = u.h0; u.h0 = k.heading;
    u.r3 = u.r2; u.r2 = u.r1; u.r1 = u.r0; u.r0 = (k.nearest < 1e8) ? k.nearest : -1;
    if (u.x == u.px && u.y == u.py) {
      ++u.stopTicks;
    } else {
      u.stopTicks = 0;
      u.moved = true;
    }
  }
}

void World::scoreAllTracks() {
  tracks.clear();
  for (auto& u : units) {
    if (!u.alive) continue;
    if (u.team != Team::Red) continue; // blue sensors track red contacts in v1

    Track t;
    t.id = "T-" + u.id;
    t.sourceUnitId = u.id;
    t.x = u.x;
    t.y = u.y;
    // Observed velocity: displacement over the last tick.
    t.vx = u.x - u.px;
    t.vy = u.y - u.py;
    // The heuristic keeps its original order-based speed input so the
    // baseline scores are unchanged.
    const double orderSpeed = (u.order.action == OrderAction::Move) ? 1.0 : 0.0;

    // SNR degraded by jam fields near blue sensors looking at this contact
    double bestSnr = 0.15;
    for (const auto& s : units) {
      if (!s.alive || s.team != Team::Blue) continue;
      if (!(s.type == UnitType::Sensor || s.type == UnitType::HQ || s.type == UnitType::Relay)) continue;
      const double d = dist(s.x, s.y, u.x, u.y);
      const double base = std::max(0.0, 1.0 - d / 10.0);
      const double jam = jamFieldAt(s.x, s.y, Team::Blue);
      bestSnr = std::max(bestSnr, base * (1.0 - 0.75 * jam) * s.integrity);
    }
    t.snr = bestSnr;

    double nearestBlue = 1e9;
    for (const auto& b : units) {
      if (!b.alive || b.team != Team::Blue) continue;
      nearestBlue = std::min(nearestBlue, dist(b.x, b.y, u.x, u.y));
    }
    const double prox = nearestBlue > 12 ? 0 : 1.0 - (nearestBlue / 12.0);

    TrackFeatures f;
    f.speed = orderSpeed;
    if (u.type == UnitType::Jet) f.speed = std::max(f.speed, 2.0);
    if (u.type == UnitType::Missile) f.speed = std::max(f.speed, 3.0);
    f.snr = t.snr;
    // Emitters score as jam pressure; other contacts use friendly red EW cover.
    f.jamPressure = (u.type == UnitType::Jammer)
                        ? 0.9
                        : jamFieldAt(u.x, u.y, Team::Blue);
    f.proximityBlue = prox;
    f.altitudeHint = (u.type == UnitType::Jet || u.type == UnitType::Missile) ? 0.85 :
                     (u.type == UnitType::Jammer ? 0.35 : 0.2);

    bool closing = false;
    if (!u.targetId.empty()) {
      const Unit* tgt = findUnit(u.targetId);
      closing = tgt && tgt->alive && tgt->team == Team::Blue;
    }
    if (!closing && u.order.action == OrderAction::Launch && !u.order.targetId.empty()) {
      const Unit* tgt = findUnit(u.order.targetId);
      closing = tgt && tgt->alive && tgt->team == Team::Blue;
    }
    f.closingOnBlue = closing ? 1.0 : 0.0;

    // Inputs for the learned model, in modelInputNames() order. Nothing here
    // reads the contact's order or target: it is kinematics, a few ticks of
    // track history, emissions, and effects on blue assets.
    const Kinematics k = kinematics(u);
    const double speedNow = std::sqrt(t.vx * t.vx + t.vy * t.vy);
    double blueInField = 0, ewCover = 0;
    if (u.type == UnitType::Jammer && u.jamRadius > 0) {
      for (const auto& b : units) {
        if (!b.alive || b.team != Team::Blue) continue;
        if (!(b.type == UnitType::Sensor || b.type == UnitType::HQ || b.type == UnitType::Relay)) continue;
        const double d = dist(b.x, b.y, u.x, u.y);
        if (d < u.jamRadius) blueInField = std::max(blueInField, 1.0 - d / static_cast<double>(u.jamRadius));
      }
    } else {
      ewCover = jamFieldAt(u.x, u.y, Team::Blue);
    }
    const std::vector<double> inputs = {
        speedNow / 3.0,
        t.snr,
        (u.type == UnitType::Jammer) ? 1.0 : 0.0,
        prox,
        f.altitudeHint,
        k.closing,
        k.heading,
        k.aimRange,
        blueInField,
        ewCover,
        (tick - u.lastHitTick <= 3) ? 1.0 : 0.0,
        k.intercept,
        (nearestBlue <= 1.5) ? 1.0 : 0.0,
        (nearestBlue < 0.5) ? 1.0 : 0.0,
        (speedNow <= 0) ? 1.0 : 0.0,
        k.miss,
        (k.heading >= 0.98) ? 1.0 : 0.0,
        std::min(u.stopTicks, 6) / 6.0,
        u.moved ? 1.0 : 0.0,
        (u.r3 >= 0 && nearestBlue < 1e8) ? (u.r3 - nearestBlue) / 6.0 : 0.0,
        u.c1,
        u.h1,
        u.c2,
        u.h2,
    };

    // The sequence model sees each tick exactly once: advance its hidden
    // state the first time this contact is scored on a new tick, and reuse it
    // for any later rescoring within the same tick (orders, status).
    if (u.seqTick != tick) {
      sequenceStep(std::vector<double>(inputs.begin(), inputs.begin() + kSequenceInputs), u.seqHidden);
      u.seqTick = tick;
    }

    t.threat = scoreTrack(f, inputs, u.seqHidden, activeScorer);
    tracks.push_back(t);
  }
}

void World::tickOnce() {
  ++tick;

  // Remember where everything was so tracks carry an observed velocity.
  for (auto& u : units) {
    u.px = u.x;
    u.py = u.y;
  }

  // Apply orders / movement
  for (auto& u : units) {
    if (!u.alive) continue;
    if (u.order.action == OrderAction::Move) {
      if (u.order.tx >= 0 && u.order.ty >= 0) {
        if (u.x < u.order.tx) ++u.x;
        else if (u.x > u.order.tx) --u.x;
        if (u.y < u.order.ty) ++u.y;
        else if (u.y > u.order.ty) --u.y;
        u.x = clampi(u.x, 0, width - 1);
        u.y = clampi(u.y, 0, height - 1);
        if (u.x == u.order.tx && u.y == u.order.ty) u.order.action = OrderAction::None;
      }
    }
    if (u.order.action == OrderAction::Jam && u.type == UnitType::Jammer) {
      // jam radius already active while order held
      if (u.jamRadius <= 0) u.jamRadius = 3;
    }
  }

  // Sensor integrity under jam
  for (auto& u : units) {
    if (!u.alive || u.team != Team::Blue) continue;
    if (!(u.type == UnitType::Sensor || u.type == UnitType::Relay || u.type == UnitType::HQ)) continue;
    const double jam = jamFieldAt(u.x, u.y, Team::Blue);
    u.integrity = std::max(0.2, 1.0 - 0.7 * jam);
  }

  // Abstract engage: missiles/jets with launch or active target
  for (auto& u : units) {
    if (!u.alive) continue;
    if (!(u.type == UnitType::Missile || u.type == UnitType::Jet)) continue;
    const bool launching = u.order.action == OrderAction::Launch || !u.targetId.empty();
    if (!launching) continue;
    Unit* tgt = findUnit(u.targetId.empty() ? u.order.targetId : u.targetId);
    if (!tgt || !tgt->alive) continue;

    // Steer toward target
    if (u.x < tgt->x) ++u.x;
    else if (u.x > tgt->x) --u.x;
    if (u.y < tgt->y) ++u.y;
    else if (u.y > tgt->y) --u.y;

    const double d = dist(u.x, u.y, tgt->x, tgt->y);
    const double jam = jamFieldAt(tgt->x, tgt->y, Team::Blue);
    // Abstract hit chance — game math only
    double pHit = std::max(0.05, 0.55 - 0.08 * d + 0.15 * jam);
    if (u.type == UnitType::Jet) pHit *= 0.6;
    // Deterministic-ish from tick+ids for reproducible demos
    const int h = static_cast<int>(u.id.size() * 17 + tgt->id.size() * 13 + tick * 31) % 100;
    std::string blocker;
    if (d <= 1.5 && h < static_cast<int>(pHit * 100) && isDefended(u.id, &blocker)) {
      // The hit would have landed, but an HQ's point defense was on this contact.
      events.push_back({tick, "blocked", u.id + " blocked by " + blocker});
      if (u.type == UnitType::Missile) {
        u.alive = false;
        u.hp = 0;
        events.push_back({tick, "expended", u.id});
      }
      continue;
    }
    if (d <= 1.5 && h < static_cast<int>(pHit * 100)) {
      tgt->hp -= (u.type == UnitType::Missile) ? 40 : 15;
      u.lastHitTick = tick;
      events.push_back({tick, "engage", u.id + " hit " + tgt->id});
      if (tgt->hp <= 0) {
        tgt->alive = false;
        tgt->hp = 0;
        events.push_back({tick, "destroyed", tgt->id});
      }
      if (u.type == UnitType::Missile) {
        u.alive = false;
        u.hp = 0;
        events.push_back({tick, "expended", u.id});
      }
    }
  }

  recordHistory();

  // Keep event log bounded
  if (events.size() > 40) {
    events.erase(events.begin(), events.begin() + static_cast<long>(events.size() - 40));
  }

  scoreAllTracks();
}

std::string World::toJson(bool pretty) const {
  const std::string nl = pretty ? "\n" : "";
  const std::string sp = pretty ? "  " : "";

  std::vector<std::string> unitJson;
  for (const auto& u : units) {
    std::ostringstream o;
    o << "{"
      << "\"id\":" << jsonString(u.id) << ","
      << "\"type\":" << jsonString(unitTypeToString(u.type)) << ","
      << "\"team\":" << jsonString(teamToString(u.team)) << ","
      << "\"x\":" << u.x << ","
      << "\"y\":" << u.y << ","
      << "\"px\":" << u.px << ","
      << "\"py\":" << u.py << ","
      << "\"hp\":" << u.hp << ","
      << "\"integrity\":" << u.integrity << ","
      << "\"jamRadius\":" << u.jamRadius << ","
      << "\"targetId\":" << jsonString(u.targetId) << ","
      << "\"lastHitTick\":" << u.lastHitTick << ","
      << "\"c0\":" << u.c0 << ",\"c1\":" << u.c1 << ",\"c2\":" << u.c2 << ","
      << "\"h0\":" << u.h0 << ",\"h1\":" << u.h1 << ",\"h2\":" << u.h2 << ","
      << "\"r0\":" << u.r0 << ",\"r1\":" << u.r1 << ",\"r2\":" << u.r2 << ",\"r3\":" << u.r3 << ","
      << "\"stopTicks\":" << u.stopTicks << ","
      << "\"moved\":" << (u.moved ? 1 : 0) << ","
      << "\"seqTick\":" << u.seqTick << ","
      << "\"seqHidden\":[" << joinNumbers(u.seqHidden) << "],"
      << "\"alive\":" << (u.alive ? "true" : "false") << ","
      << "\"order\":{"
      << "\"action\":" << jsonString(orderToString(u.order.action)) << ","
      << "\"tx\":" << u.order.tx << ","
      << "\"ty\":" << u.order.ty << ","
      << "\"targetId\":" << jsonString(u.order.targetId)
      << "}}";
    unitJson.push_back(o.str());
  }

  std::vector<std::string> trackJson;
  for (const auto& t : tracks) {
    auto probs = classProbsFromScore(t.threat.score);
    std::vector<std::string> feats;
    for (double f : t.threat.features) feats.push_back(std::to_string(f));
    std::vector<std::string> observed;
    for (double f : t.threat.observed) observed.push_back(std::to_string(f));
    std::ostringstream o;
    o << "{"
      << "\"id\":" << jsonString(t.id) << ","
      << "\"sourceUnitId\":" << jsonString(t.sourceUnitId) << ","
      << "\"x\":" << t.x << ","
      << "\"y\":" << t.y << ","
      << "\"vx\":" << t.vx << ","
      << "\"vy\":" << t.vy << ","
      << "\"snr\":" << t.snr << ","
      << "\"threatScore\":" << t.threat.score << ","
      << "\"threatLabel\":" << jsonString(t.threat.label) << ","
      << "\"scorer\":" << jsonString(t.threat.scorer) << ","
      << "\"watchAt\":" << t.threat.watchAt << ","
      << "\"hostileAt\":" << t.threat.hostileAt << ","
      << "\"heuristicScore\":" << std::to_string(t.threat.heuristicScore) << ","
      << "\"learnedScore\":" << std::to_string(t.threat.learnedScore) << ","
      << "\"sequenceScore\":" << std::to_string(t.threat.sequenceScore) << ","
      << "\"classProbs\":{"
      << "\"benign\":" << probs.benign << ","
      << "\"watch\":" << probs.watch << ","
      << "\"hostile\":" << probs.hostile
      << "},"
      << "\"features\":[" << joinComma(feats) << "],"
      << "\"observed\":[" << joinComma(observed) << "]"
      << "}";
    trackJson.push_back(o.str());
  }

  std::vector<std::string> eventJson;
  for (const auto& e : events) {
    std::ostringstream o;
    o << "{\"tick\":" << e.tick
      << ",\"kind\":" << jsonString(e.kind)
      << ",\"detail\":" << jsonString(e.detail) << "}";
    eventJson.push_back(o.str());
  }

  std::vector<std::string> coverJson;
  coverJson.reserve(cover.size());
  for (int c : cover) coverJson.push_back(std::to_string(c));

  std::ostringstream out;
  out << "{" << nl
      << sp << "\"tick\":" << tick << "," << nl
      << sp << "\"width\":" << width << "," << nl
      << sp << "\"height\":" << height << "," << nl
      << sp << "\"units\":[" << joinComma(unitJson) << "]," << nl
      << sp << "\"tracks\":[" << joinComma(trackJson) << "]," << nl
      << sp << "\"events\":[" << joinComma(eventJson) << "]," << nl
      << sp << "\"cover\":[" << joinComma(coverJson) << "]" << nl
      << "}";
  return out.str();
}

World World::fromJson(const std::string& json) {
  World w = defaultScenario();
  extractIntField(json, "tick", w.tick);
  extractIntField(json, "width", w.width);
  extractIntField(json, "height", w.height);

  auto unitBody = extractArrayBody(json, "units");
  if (!unitBody.empty()) {
    w.units.clear();
    for (const auto& obj : splitObjects(unitBody)) {
      Unit u;
      extractStringField(obj, "id", u.id);
      std::string type, team, action, targetId, orderTarget;
      extractStringField(obj, "type", type);
      extractStringField(obj, "team", team);
      unitTypeFromString(type, u.type);
      teamFromString(team, u.team);
      extractIntField(obj, "x", u.x);
      extractIntField(obj, "y", u.y);
      // Worlds written before px/py existed load as "not moving".
      u.px = u.x;
      u.py = u.y;
      extractIntField(obj, "px", u.px);
      extractIntField(obj, "py", u.py);
      extractIntField(obj, "lastHitTick", u.lastHitTick);
      extractNumberField(obj, "c0", u.c0);
      extractNumberField(obj, "c1", u.c1);
      extractNumberField(obj, "c2", u.c2);
      extractNumberField(obj, "h0", u.h0);
      extractNumberField(obj, "h1", u.h1);
      extractNumberField(obj, "h2", u.h2);
      extractNumberField(obj, "r0", u.r0);
      extractNumberField(obj, "r1", u.r1);
      extractNumberField(obj, "r2", u.r2);
      extractNumberField(obj, "r3", u.r3);
      extractIntField(obj, "stopTicks", u.stopTicks);
      int movedFlag = 0;
      extractIntField(obj, "moved", movedFlag);
      u.moved = movedFlag != 0;
      extractIntField(obj, "seqTick", u.seqTick);
      u.seqHidden = parseNumbers(extractArrayBody(obj, "seqHidden"));
      extractNumberField(obj, "hp", u.hp);
      extractNumberField(obj, "integrity", u.integrity);
      extractIntField(obj, "jamRadius", u.jamRadius);
      extractStringField(obj, "targetId", u.targetId);
      // alive
      u.alive = obj.find("\"alive\":false") == std::string::npos;
      // nested order — read from the "order" sub-object so its targetId is
      // not confused with the unit-level targetId above
      const auto orderPos = obj.find("\"order\"");
      if (orderPos != std::string::npos) {
        const std::string orderObj = obj.substr(orderPos);
        extractStringField(orderObj, "action", action);
        orderFromString(action, u.order.action);
        extractIntField(orderObj, "tx", u.order.tx);
        extractIntField(orderObj, "ty", u.order.ty);
        extractStringField(orderObj, "targetId", u.order.targetId);
      }
      w.units.push_back(u);
    }
  }

  // Restore the event log. Each CLI call is a fresh process, so without this
  // every command started again from the single "scenario" line and earlier
  // engage/destroyed events were lost.
  if (json.find("\"events\"") != std::string::npos) {
    w.events.clear();
    for (const auto& obj : splitObjects(extractArrayBody(json, "events"))) {
      SimEvent e;
      extractIntField(obj, "tick", e.tick);
      extractStringField(obj, "kind", e.kind);
      extractStringField(obj, "detail", e.detail);
      w.events.push_back(e);
    }
  }

  // Recompute tracks from units for consistency
  w.scoreAllTracks();
  return w;
}