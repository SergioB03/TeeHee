#include "world.hpp"
#include "json_mini.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
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
    case UnitType::Stealth: return "stealth";
    case UnitType::Bomber: return "bomber";
    case UnitType::Drone: return "drone";
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
  else if (s == "stealth") out = UnitType::Stealth;
  else if (s == "bomber") out = UnitType::Bomber;
  else if (s == "drone") out = UnitType::Drone;
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
    case OrderAction::Altitude: return "altitude";
    case OrderAction::Orbit: return "orbit";
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
  else if (s == "altitude") out = OrderAction::Altitude;
  else if (s == "orbit") out = OrderAction::Orbit;
  else return false;
  return true;
}

const AirProfile& profileOf(UnitType t) { return profileByName(unitTypeToString(t)); }

bool isAircraft(UnitType t) { return profileOf(t).ceiling > 0; }

namespace {
bool canAttack(UnitType t) { return isAircraft(t); }

void stepHorizontal(Unit& u, int tx, int ty, int cells) {
  for (int i = 0; i < cells; ++i) {
    if (u.x < tx) ++u.x;
    else if (u.x > tx) --u.x;
    if (u.y < ty) ++u.y;
    else if (u.y > ty) --u.y;
  }
}

void stepAltitude(Unit& u, int tz, int levels) {
  if (u.z < tz) u.z = std::min(tz, u.z + levels);
  else if (u.z > tz) u.z = std::max(tz, u.z - levels);
}
}  // namespace

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
    u.z = u.pz = profileOf(type).cruise;
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
    u.z = u.pz = profileOf(type).cruise;
    if (type == UnitType::Jammer) u.jamRadius = 3;
    w.units.push_back(u);
  };
  add("B-HQ", UnitType::HQ, Team::Blue, 1, 1);
  add("B-S1", UnitType::Sensor, Team::Blue, 2, 3);
  add("B-S2", UnitType::Sensor, Team::Blue, 3, 8);
  add("B-R1", UnitType::Relay, Team::Blue, 4, 5);
  add("B-F1", UnitType::Stealth, Team::Blue, 2, 6);
  add("R-J1", UnitType::Jammer, Team::Red, 9, 8);
  add("R-JET1", UnitType::Jet, Team::Red, 10, 2);
  add("R-F1", UnitType::Stealth, Team::Red, 11, 10);
  add("R-BMR1", UnitType::Bomber, Team::Red, 11, 0);
  add("R-DRN1", UnitType::Drone, Team::Red, 8, 11);
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
        if (!b.alive || b.team != Team::Blue || isAircraft(b.type)) continue;
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
    // Blue is flown by the autonomy planner, from the same observation message
    // an edge node would receive.
    for (const auto& p : planDefense(observe())) {
      OpponentMove m;
      m.id = p.id;
      orderFromString(p.action, m.action);
      m.tx = p.tx;
      m.ty = p.ty;
      m.tz = p.tz;
      m.targetId = p.target;
      m.reason = p.reason;
      moves.push_back(m);
    }
    return moves;
  }

  for (const auto& u : units) {
    if (!u.alive || u.team != Team::Red) continue;
    if (canAttack(u.type)) {
      const Unit* current = u.targetId.empty() ? nullptr : findUnit(u.targetId);
      if (current && current->alive) continue;  // already committed
      const bool heavy = u.type == UnitType::Missile || u.type == UnitType::Bomber;
      const Unit* tgt = nearestBlue(u, !heavy);
      if (heavy) {
        // Missiles and bombers go for the command post while it stands.
        for (const auto& b : units) {
          if (b.alive && b.team == Team::Blue && b.type == UnitType::HQ) tgt = &b;
        }
      }
      if (!tgt) continue;
      OpponentMove m;
      m.id = u.id;
      m.action = OrderAction::Launch;
      m.targetId = tgt->id;
      m.reason = heavy ? "strike the command post" : "suppress the nearest sensor";
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

Observation World::observe() const {
  Observation o;
  o.tick = tick;
  o.width = width;
  o.height = height;
  o.ceiling = kCeiling;
  o.watchAt = learnedWatchAt();
  for (const auto& u : units) {
    if (!u.alive || u.team != Team::Blue) continue;
    OwnUnit own;
    own.id = u.id;
    own.type = unitTypeToString(u.type);
    own.x = u.x; own.y = u.y; own.z = u.z;
    own.hp = u.hp;
    own.action = orderToString(u.order.action);
    own.target = u.order.action == OrderAction::Defend ? u.order.targetId : u.targetId;
    own.tx = u.order.tx; own.ty = u.order.ty; own.tz = u.order.tz;
    o.own.push_back(own);
  }
  for (const auto& t : tracks) {
    const Unit* u = findUnit(t.sourceUnitId);
    if (!u || !u->alive) continue;
    Contact c;
    c.id = u->id;
    c.cls = unitTypeToString(u->type);
    c.x = static_cast<int>(t.x); c.y = static_cast<int>(t.y); c.z = t.z;  // where the track is, not where the unit is
    c.vx = static_cast<int>(t.vx); c.vy = static_cast<int>(t.vy); c.vz = t.vz;
    c.snr = t.snr;
    c.locked = t.locked;
    c.score = t.threat.learnedScore;
    c.jam = u->type == UnitType::Jammer ? u->jamRadius : 0;
    o.contacts.push_back(c);
  }
  return o;
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

bool World::spawn(UnitType type, Team team, int x, int y, int z, std::string* err) {
  if (x < 0 || y < 0 || x >= width || y >= height) {
    if (err) *err = "spawn out of bounds";
    return false;
  }
  const AirProfile& profile = profileOf(type);
  if (z < 0) z = profile.cruise;
  if (z > profile.ceiling) {
    if (err) *err = isAircraft(type) ? "spawn altitude is above this type's ceiling of " + std::to_string(profile.ceiling)
                                     : "ground units start at altitude 0";
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
  u.z = u.pz = z;
  if (type == UnitType::Jammer) u.jamRadius = 3;
  units.push_back(u);
  events.push_back({tick, "spawn", u.id + " " + unitTypeToString(type)});
  return true;
}

bool World::issueOrder(const std::string& id, OrderAction action, int tx, int ty, int tz,
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
  if (action == OrderAction::Launch && !canAttack(u->type)) {
    if (err) *err = "only aircraft and missiles can launch";
    return false;
  }
  const AirProfile& profile = profileOf(u->type);
  if (tz >= 0 && !isAircraft(u->type) && tz != 0) {
    if (err) *err = "ground units stay at altitude 0";
    return false;
  }
  if (tz > profile.ceiling) {
    if (err) *err = "--tz is above this unit's ceiling of " + std::to_string(profile.ceiling);
    return false;
  }
  if (action == OrderAction::Altitude) {
    if (!isAircraft(u->type)) {
      if (err) *err = "only aircraft can change altitude";
      return false;
    }
    if (tz < 0) {
      if (err) *err = "altitude needs --tz between 0 and " + std::to_string(profile.ceiling);
      return false;
    }
    u->order = {};
    u->order.action = action;
    u->order.tz = tz;
    u->targetId.clear();
    events.push_back({tick, "order", id + " altitude " + std::to_string(tz)});
    return true;
  }
  if (action == OrderAction::Orbit) {
    if (!isAircraft(u->type) || u->type == UnitType::Missile) {
      if (err) *err = "only aircraft can orbit";
      return false;
    }
    // Circle the cell it is over now, holding altitude unless --tz is given.
    u->order = {};
    u->order.action = action;
    u->order.tx = u->x;
    u->order.ty = u->y;
    u->order.tz = tz;
    u->targetId.clear();
    events.push_back({tick, "order", id + " orbit"});
    return true;
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
    if (isAircraft(tgt->type) && !profile.airToAir) {
      if (err) *err = unitTypeToString(u->type) + "s can only attack ground targets";
      return false;
    }
  }
  u->order.action = action;
  u->order.tx = tx;
  u->order.ty = ty;
  u->order.tz = (action == OrderAction::Move) ? tz : -1;
  // A move replaces an attack: without this the unit kept steering at its
  // old target while also flying to the new point.
  if (action == OrderAction::Move) u->targetId.clear();
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
    // History is what blue saw. With noisy sensing a contact with no track has none.
    if (sensing() && u.team == Team::Red && !u.tracked) continue;
    const Unit o = observedView(u);
    const Kinematics k = kinematics(o);
    u.c2 = u.c1; u.c1 = u.c0; u.c0 = k.closing;
    u.h2 = u.h1; u.h1 = u.h0; u.h0 = k.heading;
    u.r3 = u.r2; u.r2 = u.r1; u.r1 = u.r0; u.r0 = (k.nearest < 1e8) ? k.nearest : -1;
    u.a3 = u.a2; u.a2 = u.a1; u.a1 = u.a0; u.a0 = o.z;
    if (o.x == o.px && o.y == o.py) {
      ++u.stopTicks;
    } else {
      u.stopTicks = 0;
      u.moved = true;
    }
  }
}

bool World::coverBetween(int x1, int y1, int x2, int y2) const {
  const int steps = std::max(std::abs(x2 - x1), std::abs(y2 - y1));
  for (int i = 1; i < steps; ++i) {
    const int cx = static_cast<int>(std::lround(x1 + static_cast<double>(x2 - x1) * i / steps));
    const int cy = static_cast<int>(std::lround(y1 + static_cast<double>(y2 - y1) * i / steps));
    const size_t cell = static_cast<size_t>(cy * width + cx);
    if (cx >= 0 && cy >= 0 && cx < width && cy < height && cell < cover.size() && cover[cell]) return true;
  }
  return false;
}

double World::sensorSnr(const Unit& s, const Unit& u) const {
  const double flat = dist(s.x, s.y, u.x, u.y);
  const double up = kLevelCells * u.z;
  const double slant = std::sqrt(flat * flat + up * up);
  double base = std::min(1.0, std::max(0.0, 1.0 - slant / 10.0) * profileOf(u.type).signature);
  // Terrain masking: anything at level 0 or 1 hides behind cover.
  if (u.z <= 1 && coverBetween(s.x, s.y, u.x, u.y)) base *= 0.35;
  const double jam = jamFieldAt(s.x, s.y, Team::Blue);
  return base * (1.0 - 0.75 * jam) * s.integrity;
}

double World::observedSnr(const Unit& u) const {
  double best = 0.15;
  for (const auto& s : units) {
    if (!s.alive || s.team != Team::Blue) continue;
    if (!(s.type == UnitType::Sensor || s.type == UnitType::HQ || s.type == UnitType::Relay)) continue;
    best = std::max(best, sensorSnr(s, u));
  }
  return best;
}

// --- sensing: detections and the tracker ---------------------------------------
namespace {
// Reproducible noise: a hash of (seed, tick, contact, sensor, draw), not a
// stateful generator, so every CLI call sees the same draws for the same tick.
uint64_t mix64(uint64_t x) {
  x += 0x9E3779B97F4A7C15ULL;
  x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
  x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
  return x ^ (x >> 31);
}
uint64_t nameHash(const std::string& s) {
  uint64_t h = 1469598103934665603ULL;  // FNV-1a
  for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
  return h;
}
double uniform01(int seed, int tick, const std::string& id, int sensor, int draw) {
  uint64_t h = mix64(static_cast<uint64_t>(seed) * 0x9E3779B97F4A7C15ULL + static_cast<uint64_t>(tick));
  h = mix64(h ^ nameHash(id));
  h = mix64(h ^ (static_cast<uint64_t>(sensor) * 1315423911ULL + static_cast<uint64_t>(draw)));
  return (static_cast<double>(h >> 11) + 0.5) / 9007199254740992.0;
}
double gauss(int seed, int tick, const std::string& id, int sensor, int draw) {
  const double a = uniform01(seed, tick, id, sensor, draw), b = uniform01(seed, tick, id, sensor, draw + 50);
  return std::sqrt(-2.0 * std::log(a)) * std::cos(6.283185307179586 * b);
}

// One axis of a constant-velocity Kalman filter: position, velocity, and the
// three distinct terms of their 2x2 covariance.
struct Axis {
  double p = 0, v = 0, a = 1, b = 0, c = 1;
  void start(double z, double r) { p = z; v = 0; a = r + 0.05; b = 0; c = 1.0; }
  void predict(double accel) {
    p += v;
    const double q = accel * accel;
    const double a2 = a + 2 * b + c + q / 4, b2 = b + c + q / 2, c2 = c + q;
    a = a2; b = b2; c = c2;
  }
  void update(double z, double r) {
    const double s = a + r, k1 = a / s, k2 = b / s, innovation = z - p;
    p += k1 * innovation;
    v += k2 * innovation;
    const double a2 = (1 - k1) * a, b2 = (1 - k1) * b, c2 = c - k2 * b;
    a = a2; b = b2; c = c2;
  }
};
constexpr int kCoastTicks = 3;       // a track with no detection for longer than this is dropped
constexpr double kAccel = 0.7;       // how sharply a contact may change speed, cells per tick^2
constexpr double kAccelAlt = 0.5;

void loadAxes(const std::vector<double>& trk, Axis axes[3]) {
  for (int i = 0; i < 3 && trk.size() >= 15; ++i) {
    const size_t k = static_cast<size_t>(i) * 5;
    axes[i] = {trk[k], trk[k + 1], trk[k + 2], trk[k + 3], trk[k + 4]};
  }
}
void saveAxes(const Axis axes[3], std::vector<double>& trk) {
  trk.clear();
  for (int i = 0; i < 3; ++i) {
    trk.push_back(axes[i].p); trk.push_back(axes[i].v); trk.push_back(axes[i].a); trk.push_back(axes[i].b); trk.push_back(axes[i].c);
  }
}
// A new track knows nothing about the contact's past.
void forgetHistory(Unit& u) {
  u.c0 = u.c1 = u.c2 = 0;
  u.h0 = u.h1 = u.h2 = 0;
  u.r0 = u.r1 = u.r2 = u.r3 = -1;
  u.a0 = u.a1 = u.a2 = u.a3 = -1;
  u.stopTicks = 0;
  u.moved = false;
  u.seqHidden.clear();
}
}  // namespace

void World::stepTrack(Unit& u) {
  // Detections this tick: one per blue sensor that sees the contact.
  struct Detection { double x, y, z, var, quality; };
  std::vector<Detection> seen;
  int index = 0;
  for (const auto& s : units) {
    ++index;
    if (!s.alive || s.team != Team::Blue) continue;
    if (!(s.type == UnitType::Sensor || s.type == UnitType::HQ || s.type == UnitType::Relay)) continue;
    const double quality = sensorSnr(s, u);
    const double detect = 1.0 - noise * (1.0 - std::max(0.0, std::min(1.0, 0.2 + 1.2 * quality)));
    if (uniform01(seed, tick, u.id, index, 0) >= detect) continue;
    const double sigma = noise * (0.25 + 1.1 * (1.0 - std::min(1.0, quality)));
    seen.push_back({u.x + sigma * gauss(seed, tick, u.id, index, 1), u.y + sigma * gauss(seed, tick, u.id, index, 2),
                    u.z + 0.8 * sigma * gauss(seed, tick, u.id, index, 3), sigma * sigma + 1e-4, quality});
  }
  std::sort(seen.begin(), seen.end(), [](const Detection& a, const Detection& b) { return a.quality > b.quality; });

  const bool had = u.tracked;
  Axis axes[3];
  if (!fusion) {
    // No tracker: the best single detection, and nothing at all without one.
    if (seen.empty()) {
      if (had) { u.tracked = false; ++u.drops; forgetHistory(u); }
      return;
    }
    axes[0].start(seen[0].x, seen[0].var);
    axes[1].start(seen[0].y, seen[0].var);
    axes[2].start(seen[0].z, seen[0].var);
    u.misses = 0;
  } else if (!had) {
    if (seen.empty()) return;
    axes[0].start(seen[0].x, seen[0].var);
    axes[1].start(seen[0].y, seen[0].var);
    axes[2].start(seen[0].z, seen[0].var);
    for (size_t i = 1; i < seen.size(); ++i) {
      axes[0].update(seen[i].x, seen[i].var);
      axes[1].update(seen[i].y, seen[i].var);
      axes[2].update(seen[i].z, seen[i].var);
    }
    u.misses = 0;
  } else {
    loadAxes(u.trk, axes);
    axes[0].predict(kAccel);
    axes[1].predict(kAccel);
    axes[2].predict(kAccelAlt);
    if (seen.empty()) {
      // Coast on the prediction for a few ticks, then give the track up.
      if (++u.misses > kCoastTicks) {
        u.tracked = false;
        ++u.drops;
        u.trk.clear();
        forgetHistory(u);
        return;
      }
    } else {
      u.misses = 0;
      for (const auto& d : seen) {  // fuse every sensor's detection, best first
        axes[0].update(d.x, d.var);
        axes[1].update(d.y, d.var);
        axes[2].update(d.z, d.var);
      }
    }
  }
  saveAxes(axes, u.trk);
  const int rx = clampi(static_cast<int>(std::lround(axes[0].p)), 0, width - 1);
  const int ry = clampi(static_cast<int>(std::lround(axes[1].p)), 0, height - 1);
  const int rz = clampi(static_cast<int>(std::lround(axes[2].p)), 0, profileOf(u.type).ceiling);
  if (had) { u.opx = u.ox; u.opy = u.oy; u.opz = u.oz; }
  else { u.opx = rx; u.opy = ry; u.opz = rz; forgetHistory(u); }  // a new track starts with no motion
  u.ox = rx; u.oy = ry; u.oz = rz;
  u.tracked = true;
}

Unit World::observedView(const Unit& u) const {
  if (!sensing() || u.team != Team::Red) return u;
  Unit o = u;
  o.x = u.ox; o.y = u.oy; o.z = u.oz;
  o.px = u.opx; o.py = u.opy; o.pz = u.opz;
  return o;
}

void World::scoreAllTracks() {
  tracks.clear();
  for (auto& u : units) {
    if (!u.alive) continue;
    if (u.team != Team::Red) continue; // blue sensors track red contacts in v1
    // With noisy sensing the track is stepped once per tick (also on the first
    // look at a hand-built world), and a contact with no track is not seen.
    if (sensing() && u.trkTick != tick) {
      stepTrack(u);
      u.trkTick = tick;
    }
    if (sensing() && !u.tracked) continue;
    const Unit o = observedView(u);  // everything below reads blue's belief, not the truth

    Track t;
    t.id = "T-" + u.id;
    t.sourceUnitId = u.id;
    t.x = o.x;
    t.y = o.y;
    // Observed velocity: displacement over the last tick.
    t.vx = o.x - o.px;
    t.vy = o.y - o.py;
    // The heuristic keeps its original order-based speed input so the
    // baseline scores are unchanged.
    const double orderSpeed = (u.order.action == OrderAction::Move) ? 1.0 : 0.0;

    t.z = o.z;
    t.vz = o.z - o.pz;
    t.snr = observedSnr(u);
    t.locked = t.snr >= kLockSnr;
    t.ex = o.x; t.ey = o.y; t.ez = o.z;
    if (sensing() && u.trk.size() >= 15) {
      t.ex = u.trk[0]; t.ey = u.trk[5]; t.ez = u.trk[10];
      t.err = std::sqrt(std::max(0.0, u.trk[2]) + std::max(0.0, u.trk[7]));
      t.misses = u.misses;
    }

    double nearestBlue = 1e9;
    for (const auto& b : units) {
      if (!b.alive || b.team != Team::Blue) continue;
      nearestBlue = std::min(nearestBlue, dist(b.x, b.y, o.x, o.y));
    }
    const double prox = nearestBlue > 12 ? 0 : 1.0 - (nearestBlue / 12.0);

    TrackFeatures f;
    f.speed = orderSpeed;
    if (u.type == UnitType::Jet || u.type == UnitType::Bomber) f.speed = std::max(f.speed, 2.0);
    if (u.type == UnitType::Missile || u.type == UnitType::Stealth) f.speed = std::max(f.speed, 3.0);
    f.snr = t.snr;
    // Emitters score as jam pressure; other contacts use friendly red EW cover.
    f.jamPressure = (u.type == UnitType::Jammer)
                        ? 0.9
                        : jamFieldAt(o.x, o.y, Team::Blue);
    f.proximityBlue = prox;
    f.altitudeHint = isAircraft(u.type) ? 0.85 :
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
    const Kinematics k = kinematics(o);
    const double speedNow = std::sqrt(t.vx * t.vx + t.vy * t.vy);
    double blueInField = 0, ewCover = 0;
    if (u.type == UnitType::Jammer && u.jamRadius > 0) {
      for (const auto& b : units) {
        if (!b.alive || b.team != Team::Blue) continue;
        if (!(b.type == UnitType::Sensor || b.type == UnitType::HQ || b.type == UnitType::Relay)) continue;
        const double d = dist(b.x, b.y, o.x, o.y);
        if (d < u.jamRadius) blueInField = std::max(blueInField, 1.0 - d / static_cast<double>(u.jamRadius));
      }
    } else {
      ewCover = jamFieldAt(o.x, o.y, Team::Blue);
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
        static_cast<double>(o.z) / kCeiling,
        std::max(0, o.pz - o.z) / 2.0,
        std::max(0, o.z - o.pz) / 2.0,
        std::min(u.stopTicks, 6) / 6.0,
        u.moved ? 1.0 : 0.0,
        (u.r3 >= 0 && nearestBlue < 1e8) ? (u.r3 - nearestBlue) / 6.0 : 0.0,
        u.c1,
        u.h1,
        u.c2,
        u.h2,
        (u.a3 >= 0) ? std::max(0.0, u.a3 - o.z) / kCeiling : 0.0,
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
    u.pz = u.z;
  }

  // Apply orders / movement
  for (auto& u : units) {
    if (!u.alive) continue;
    const AirProfile& profile = profileOf(u.type);
    if (u.order.action == OrderAction::Move) {
      if (u.order.tx >= 0 && u.order.ty >= 0) {
        stepHorizontal(u, u.order.tx, u.order.ty, profile.speed);
        u.x = clampi(u.x, 0, width - 1);
        u.y = clampi(u.y, 0, height - 1);
        if (u.order.tz >= 0) stepAltitude(u, u.order.tz, profile.climb);
        const bool level = u.order.tz < 0 || u.z == u.order.tz;
        if (u.x == u.order.tx && u.y == u.order.ty && level) u.order.action = OrderAction::None;
      }
    }
    if (u.order.action == OrderAction::Altitude) {
      stepAltitude(u, u.order.tz, profile.climb);
      if (u.z == u.order.tz) u.order.action = OrderAction::None;
    }
    if (u.order.action == OrderAction::Orbit) {
      // A four-cell box beside the anchor, turned inward at the map edge.
      const int sx = (u.order.tx + 1 < width) ? 1 : -1;
      const int sy = (u.order.ty + 1 < height) ? 1 : -1;
      const int ox = u.x - u.order.tx, oy = u.y - u.order.ty;
      int nx = 0, ny = 0;
      if (ox == 0 && oy == 0) { nx = sx; ny = 0; }
      else if (ox == sx && oy == 0) { nx = sx; ny = sy; }
      else if (ox == sx && oy == sy) { nx = 0; ny = sy; }
      u.x = u.order.tx + nx;
      u.y = u.order.ty + ny;
      if (u.order.tz >= 0) stepAltitude(u, u.order.tz, profile.climb);
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
    if (!canAttack(u.type)) continue;
    const bool launching = u.order.action == OrderAction::Launch || !u.targetId.empty();
    if (!launching) continue;
    Unit* tgt = findUnit(u.targetId.empty() ? u.order.targetId : u.targetId);
    if (!tgt || !tgt->alive) continue;
    const AirProfile& profile = profileOf(u.type);
    const bool airTarget = isAircraft(tgt->type) && tgt->z > 0;

    // Steer toward the target, and get to an altitude it can be hit from:
    // level with an aircraft, down to release altitude for a ground target.
    // A missile holds its level and dives in the last two cells.
    stepHorizontal(u, tgt->x, tgt->y, profile.speed);
    const double d = dist(u.x, u.y, tgt->x, tgt->y);
    if (airTarget) stepAltitude(u, tgt->z, profile.climb);
    else if (u.type == UnitType::Missile) { if (d <= 2.0) stepAltitude(u, 0, profile.climb); }
    else if (u.z > profile.release) stepAltitude(u, profile.release, profile.climb);
    const bool inEnvelope = airTarget ? std::abs(u.z - tgt->z) <= 1 : u.z <= profile.release;

    const double jam = jamFieldAt(tgt->x, tgt->y, Team::Blue);
    // Abstract hit chance — game math only
    const double pHit = std::max(0.05, 0.55 - 0.08 * d + 0.15 * jam) * profile.hitFactor;
    // Deterministic-ish from tick+ids for reproducible demos
    const int h = static_cast<int>(u.id.size() * 17 + tgt->id.size() * 13 + tick * 31) % 100;
    const bool wouldHit = d <= 1.5 && inEnvelope && h < static_cast<int>(pHit * 100);
    std::string blocker;
    if (wouldHit && isDefended(u.id, &blocker)) {
      // Point defense needs a usable track on a red contact to shoot at it.
      if (u.team != Team::Red || (observedSnr(u) >= kLockSnr && (!sensing() || u.tracked))) {
        events.push_back({tick, "blocked", u.id + " blocked by " + blocker});
        if (u.type == UnitType::Missile) {
          u.alive = false;
          u.hp = 0;
          events.push_back({tick, "expended", u.id});
        }
        continue;
      }
      events.push_back({tick, "nolock", blocker + " had no firing solution on " + u.id});
    }
    if (wouldHit) {
      // Aircraft are fragile: a fighter's hit does 50, a missile's 100.
      tgt->hp -= airTarget ? (u.type == UnitType::Missile ? 100 : 50) : profile.damage;
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

  // Sensors look, and the tracker updates, before this tick's history is recorded.
  if (sensing()) {
    for (auto& u : units) {
      if (!u.alive || u.team != Team::Red) continue;
      stepTrack(u);
      u.trkTick = tick;
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
      << "\"z\":" << u.z << ","
      << "\"pz\":" << u.pz << ","
      << "\"hp\":" << u.hp << ","
      << "\"integrity\":" << u.integrity << ","
      << "\"jamRadius\":" << u.jamRadius << ","
      << "\"targetId\":" << jsonString(u.targetId) << ","
      << "\"lastHitTick\":" << u.lastHitTick << ","
      << "\"c0\":" << u.c0 << ",\"c1\":" << u.c1 << ",\"c2\":" << u.c2 << ","
      << "\"h0\":" << u.h0 << ",\"h1\":" << u.h1 << ",\"h2\":" << u.h2 << ","
      << "\"r0\":" << u.r0 << ",\"r1\":" << u.r1 << ",\"r2\":" << u.r2 << ",\"r3\":" << u.r3 << ","
      << "\"a0\":" << u.a0 << ",\"a1\":" << u.a1 << ",\"a2\":" << u.a2 << ",\"a3\":" << u.a3 << ","
      << "\"stopTicks\":" << u.stopTicks << ","
      << "\"moved\":" << (u.moved ? 1 : 0) << ","
      << "\"seqTick\":" << u.seqTick << ","
      << "\"seqHidden\":[" << joinNumbers(u.seqHidden) << "],"
      << "\"tracked\":" << (u.tracked ? 1 : 0) << ",\"trkTick\":" << u.trkTick << ",\"misses\":" << u.misses
      << ",\"drops\":" << u.drops << ",\"ox\":" << u.ox << ",\"oy\":" << u.oy << ",\"oz\":" << u.oz
      << ",\"opx\":" << u.opx << ",\"opy\":" << u.opy << ",\"opz\":" << u.opz << ","
      << "\"trk\":[" << joinNumbers(u.trk) << "],"
      << "\"alive\":" << (u.alive ? "true" : "false") << ","
      << "\"order\":{"
      << "\"action\":" << jsonString(orderToString(u.order.action)) << ","
      << "\"tx\":" << u.order.tx << ","
      << "\"ty\":" << u.order.ty << ","
      << "\"tz\":" << u.order.tz << ","
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
      << "\"z\":" << t.z << ","
      << "\"vz\":" << t.vz << ","
      << "\"snr\":" << t.snr << ","
      << "\"locked\":" << (t.locked ? "true" : "false") << ","
      << "\"ex\":" << t.ex << ",\"ey\":" << t.ey << ",\"ez\":" << t.ez << ",\"err\":" << t.err << ",\"misses\":" << t.misses << ","
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
      << sp << "\"ceiling\":" << kCeiling << "," << nl
      << sp << "\"noise\":" << noise << ",\"seed\":" << seed << ",\"fusion\":" << (fusion ? 1 : 0) << "," << nl
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
  extractNumberField(json, "noise", w.noise);
  w.noise = std::max(0.0, std::min(1.0, w.noise));
  extractIntField(json, "seed", w.seed);
  int fusionFlag = 1;
  extractIntField(json, "fusion", fusionFlag);
  w.fusion = fusionFlag != 0;

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
      // Worlds written before altitude existed: aircraft load at cruise level.
      u.z = profileOf(u.type).cruise;
      extractIntField(obj, "z", u.z);
      u.z = clampi(u.z, 0, profileOf(u.type).ceiling);
      u.pz = u.z;
      extractIntField(obj, "pz", u.pz);
      extractNumberField(obj, "a0", u.a0);
      extractNumberField(obj, "a1", u.a1);
      extractNumberField(obj, "a2", u.a2);
      extractNumberField(obj, "a3", u.a3);
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
      int trackedFlag = 0;
      extractIntField(obj, "tracked", trackedFlag);
      u.tracked = trackedFlag != 0;
      extractIntField(obj, "trkTick", u.trkTick);
      extractIntField(obj, "misses", u.misses);
      extractIntField(obj, "drops", u.drops);
      extractIntField(obj, "ox", u.ox);
      extractIntField(obj, "oy", u.oy);
      extractIntField(obj, "oz", u.oz);
      extractIntField(obj, "opx", u.opx);
      extractIntField(obj, "opy", u.opy);
      extractIntField(obj, "opz", u.opz);
      u.trk = parseNumbers(extractArrayBody(obj, "trk"));
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
        extractIntField(orderObj, "tz", u.order.tz);
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