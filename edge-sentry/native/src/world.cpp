#include "world.hpp"
#include "json_mini.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>

namespace {
int clampi(int v, int lo, int hi) { return std::max(lo, std::min(hi, v)); }

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
  }
  return "none";
}

bool orderFromString(const std::string& s, OrderAction& out) {
  if (s == "none") out = OrderAction::None;
  else if (s == "move") out = OrderAction::Move;
  else if (s == "jam") out = OrderAction::Jam;
  else if (s == "launch") out = OrderAction::Launch;
  else if (s == "cease") out = OrderAction::Cease;
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
    if (type == UnitType::Jammer) u.jamRadius = jam > 0 ? jam : 3;
    w.units.push_back(u);
  };

  add("B-HQ", UnitType::HQ, Team::Blue, 1, 1);
  add("B-S1", UnitType::Sensor, Team::Blue, 2, 3);
  add("B-S2", UnitType::Sensor, Team::Blue, 3, 8);
  add("B-R1", UnitType::Relay, Team::Blue, 4, 5);
  add("R-J1", UnitType::Jammer, Team::Red, 9, 8, 3);
  add("R-JET1", UnitType::Jet, Team::Red, 10, 2);
  add("R-MSL1", UnitType::Missile, Team::Red, 8, 3);

  w.units.back().targetId = "B-HQ";
  w.events.push_back({0, "scenario", "default perimeter / EW sandbox loaded"});
  w.scoreAllTracks();
  return w;
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

double World::jamFieldAt(int x, int y, Team /*against*/) const {
  double pressure = 0;
  for (const auto& u : units) {
    if (!u.alive || u.type != UnitType::Jammer) continue;
    if (u.jamRadius <= 0) continue;
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

void World::scoreAllTracks() {
  tracks.clear();
  for (const auto& u : units) {
    if (!u.alive) continue;
    if (u.team != Team::Red) continue; // blue sensors track red contacts in v1

    Track t;
    t.id = "T-" + u.id;
    t.sourceUnitId = u.id;
    t.x = u.x;
    t.y = u.y;
    t.vx = (u.order.action == OrderAction::Move) ? 1 : 0;
    t.vy = 0;

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
    f.speed = std::abs(t.vx) + std::abs(t.vy);
    if (u.type == UnitType::Jet) f.speed = std::max(f.speed, 2.0);
    if (u.type == UnitType::Missile) f.speed = std::max(f.speed, 3.0);
    f.snr = t.snr;
    f.jamPressure = (u.type == UnitType::Jammer) ? 0.9 : jamFieldAt(u.x, u.y, Team::Blue);
    f.proximityBlue = prox;
    f.altitudeHint = (u.type == UnitType::Jet || u.type == UnitType::Missile) ? 0.85 :
                     (u.type == UnitType::Jammer ? 0.35 : 0.2);

    t.threat = scoreTrack(f);
    tracks.push_back(t);
  }
}

void World::tickOnce() {
  ++tick;

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
    if (d <= 1.5 && h < static_cast<int>(pHit * 100)) {
      tgt->hp -= (u.type == UnitType::Missile) ? 40 : 15;
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
      << "\"hp\":" << u.hp << ","
      << "\"integrity\":" << u.integrity << ","
      << "\"jamRadius\":" << u.jamRadius << ","
      << "\"targetId\":" << jsonString(u.targetId) << ","
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
      << "\"classProbs\":{"
      << "\"benign\":" << probs.benign << ","
      << "\"watch\":" << probs.watch << ","
      << "\"hostile\":" << probs.hostile
      << "},"
      << "\"features\":[" << joinComma(feats) << "]"
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