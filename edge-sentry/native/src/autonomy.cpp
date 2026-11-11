#include "autonomy.hpp"

#include "json_mini.hpp"
#include "profiles.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <sstream>

// The planner minimizes expected loss over the next few ticks, in "hp-ticks":
// one point of damage, or one point of lost sensor integrity, for one tick.
// That is the same thing mission value measures, so a hit that lands early
// costs more than the same hit landing late (the damage is felt for longer),
// and a jam field costs only while it is on.
//
// For every contact it builds a table: expected loss it causes at each future
// tick if nothing is done. Then:
//   point defense  can change its mind every tick for free, so on each tick it
//                  takes the contact with the largest expected loss that tick
//   a fighter      stops a contact's whole remaining table once it has shot it
//                  down, which takes time to reach it plus time to land hits
// Each fighter is given the contact whose removal lowers the total the most,
// with point defense's choices recomputed around it. Fighters therefore go
// after sustained attackers they can catch, and leave one-shot missiles that
// are seconds from impact to point defense.

namespace {
constexpr int kNever = 1 << 20;
// Chance one attack pass lands before the class's own hit factor. In the
// simulator it runs from 0.55 point blank to 0.43 at the edge of range.
constexpr double kPassHit = 0.5;
// Integrity lost per unit of jam pressure is 0.7, on a 0..1 scale; hp is 0..100.
constexpr double kJamLoss = 70.0;
// A fighter's hit on an aircraft does 50 of its 100 hp.
constexpr double kHitsToKill = 2.0;
// A fighter at or below this hp stops hunting and returns to base.
constexpr double kBingoHp = 50.0;
// Expected loss, in hp-ticks, below which a contact is not worth a fighter.
constexpr double kMinGain = 1.0;

int clampi(int v, int lo, int hi) { return std::max(lo, std::min(hi, v)); }
int cheb(int x1, int y1, int x2, int y2) { return std::max(std::abs(x1 - x2), std::abs(y1 - y2)); }
double dist(int x1, int y1, int x2, int y2) {
  const double dx = x1 - x2, dy = y1 - y2;
  return std::sqrt(dx * dx + dy * dy);
}

bool isAsset(const std::string& type) { return type == "hq" || type == "sensor" || type == "relay"; }
bool isFighter(const std::string& type) { return type == "jet" || type == "stealth"; }
bool flies(const std::string& type) { return profileByName(type).ceiling > 0; }

std::string num(double v, int digits = 1) {
  std::ostringstream o;
  o << std::fixed << std::setprecision(digits) << v;
  return o.str();
}

// Expected loss this contact causes at each tick 1..H if nobody stops it.
std::vector<double> lossTable(const Contact& c, const std::vector<const OwnUnit*>& assets, const Observation& o, int H) {
  std::vector<double> e(static_cast<size_t>(H + 1), 0.0);
  if (assets.empty() || c.score <= 0) return e;
  const AirProfile& p = profileByName(c.cls);

  if (c.jam > 0) {
    // An emitter: integrity lost by every asset inside its field, assuming it keeps moving as it is.
    for (int t = 1; t <= H; ++t) {
      const int px = clampi(c.x + c.vx * t, 0, o.width - 1), py = clampi(c.y + c.vy * t, 0, o.height - 1);
      double field = 0;
      for (const OwnUnit* a : assets) {
        const double d = dist(a->x, a->y, px, py);
        if (d < c.jam) field += kJamLoss * (1.0 - d / c.jam);
      }
      e[static_cast<size_t>(t)] = c.score * field;
    }
    return e;
  }
  if (p.ceiling == 0) return e;  // a ground unit with no weapon

  // An attacker: the first tick it could be in position over the nearest
  // asset, then a chance to hit on every pass (a missile gets one hit).
  int range = kNever;
  for (const OwnUnit* a : assets) range = std::min(range, cheb(a->x, a->y, c.x, c.y));
  const int closeIn = (std::max(0, range - 1) + p.speed - 1) / p.speed;
  const int descend = c.cls == "missile" ? 0 : (std::max(0, c.z - p.release) + p.climb - 1) / p.climb;
  const int arrive = std::max(1, std::max(closeIn, descend));
  const double pass = kPassHit * p.hitFactor;
  for (int t = arrive; t <= H; ++t) {
    const double lands = c.cls == "missile" ? pass * std::pow(1.0 - pass, t - arrive) : pass;
    e[static_cast<size_t>(t)] = c.score * p.damage * lands * (H - t + 1);
  }
  return e;
}

// Ticks until this fighter could be in firing position on the contact, and where that would be.
int ticksToReach(const OwnUnit& f, const Contact& c, const Observation& o, int limit, int* ix, int* iy, int* iz) {
  const AirProfile& p = profileByName(f.type);
  for (int t = 0; t <= limit; ++t) {
    const int px = clampi(c.x + c.vx * t, 0, o.width - 1), py = clampi(c.y + c.vy * t, 0, o.height - 1);
    const int pz = clampi(c.z + c.vz * t, 0, profileByName(c.cls).ceiling);
    const bool level = std::abs(f.z - pz) - p.climb * t <= 1;
    if (cheb(f.x, f.y, px, py) - p.speed * t <= 1 && level) {
      if (ix) *ix = px;
      if (iy) *iy = py;
      if (iz) *iz = std::min(pz, p.ceiling);
      return t;
    }
  }
  return kNever;
}
}  // namespace

bool plannerConfigFromName(const std::string& name, PlannerConfig& out) {
  out = PlannerConfig{};
  if (name.empty() || name == "full") return true;
  if (name == "nolead") out.lead = false;
  else if (name == "notimed") out.timedDefense = false;
  else if (name == "nopatrol") out.patrol = false;
  else if (name == "freefire") out.holdFire = false;
  else if (name.rfind("trust", 0) == 0) out.coverTrust = std::atof(name.c_str() + 5) / 100.0;  // trust50, trust100: tuning runs
  else return false;
  return true;
}

std::vector<PlanOrder> planDefense(const Observation& o, const PlannerConfig& cfg) {
  std::vector<PlanOrder> orders;
  const int H = std::max(1, cfg.horizon);
  std::vector<const OwnUnit*> assets, hqs, fighters;
  for (const auto& u : o.own) {
    if (isAsset(u.type)) assets.push_back(&u);
    if (u.type == "hq") hqs.push_back(&u);
    if (isFighter(u.type)) fighters.push_back(&u);
  }
  const size_t n = o.contacts.size();
  std::vector<std::vector<double>> loss;
  loss.reserve(n);
  for (const auto& c : o.contacts) loss.push_back(lossTable(c, assets, o, H));

  // The simpler rule used for comparison: highest threat score x damage, never
  // looking at timing. Contacts point defense can lock come first.
  auto untimedPick = [&](size_t skip) -> int {
    int best = -1;
    double bestValue = 0;
    for (int pass = 0; pass < 2 && best < 0; ++pass) {
      for (size_t i = 0; i < n; ++i) {
        const Contact& c = o.contacts[i];
        if (i == skip || (pass == 0 && !c.locked)) continue;
        const double severity = c.jam > 0 ? 3.0 : profileByName(c.cls).damage;
        const double value = c.score * severity;
        if (value > bestValue) {
          bestValue = value;
          best = static_cast<int>(i);
        }
      }
    }
    return best;
  };
  const int fixedDefense = cfg.timedDefense ? -1 : untimedPick(n);

  // Expected loss over the horizon, given the tick at which each contact is shot down.
  auto total = [&](const std::vector<int>& downAt) -> double {
    double sum = 0;
    std::vector<double> covered;
    for (int t = 1; t <= H; ++t) {
      covered.clear();
      for (size_t i = 0; i < n; ++i) {
        const double v = t >= downAt[i] ? 0.0 : loss[i][static_cast<size_t>(t)];
        sum += v;
        const bool takes = cfg.timedDefense ? o.contacts[i].locked : static_cast<int>(i) == fixedDefense && o.contacts[i].locked;
        if (takes) covered.push_back(v);
      }
      // Each headquarters removes one contact's loss this tick: the largest ones.
      std::sort(covered.begin(), covered.end(), [](double a, double b) { return a > b; });
      for (size_t k = 0; k < hqs.size() && k < covered.size(); ++k) sum -= cfg.coverTrust * covered[k];
    }
    return sum;
  };

  // --- fighters -----------------------------------------------------------------
  std::vector<int> downAt(n, kNever);
  for (const OwnUnit* f : fighters) {
    const AirProfile& fp = profileByName(f->type);
    const int killTicks = static_cast<int>(std::ceil(kHitsToKill / (kPassHit * fp.hitFactor)));
    int pick = -1, pickReach = 0, px = 0, py = 0, pz = 0;
    double pickGain = kMinGain;
    if (f->hp > kBingoHp) {
      const double before = total(downAt);
      for (size_t i = 0; i < n; ++i) {
        const Contact& c = o.contacts[i];
        if (!flies(c.cls) || downAt[i] != kNever) continue;
        int ix = 0, iy = 0, iz = 0;
        const int reach = ticksToReach(*f, c, o, H, &ix, &iy, &iz);
        if (reach > H) continue;
        std::vector<int> trial = downAt;
        trial[i] = reach + killTicks;
        double gain = before - total(trial);
        if (f->target == c.id) gain *= 1.15;  // a little loyalty to the current target, so it does not dither
        if (gain > pickGain) {
          pickGain = gain;
          pick = static_cast<int>(i);
          pickReach = reach;
          px = ix; py = iy; pz = iz;
        }
      }
    }

    PlanOrder order;
    order.id = f->id;
    if (pick >= 0) {
      const Contact& c = o.contacts[static_cast<size_t>(pick)];
      downAt[static_cast<size_t>(pick)] = pickReach + killTicks;
      const std::string why = "shooting it down by about tick " + std::to_string(o.tick + pickReach + killTicks) +
                              " saves an expected " + num(pickGain) + " hp-ticks";
      if (cfg.holdFire && c.score < o.watchAt) {
        // Rules of engagement: not yet assessed as a threat, so no shooting.
        // Stay close enough to act the moment that changes.
        if (cheb(f->x, f->y, px, py) <= 1 && std::abs(f->z - pz) <= 1 && f->target.empty()) continue;
        if (f->action == "move" && f->tx == px && f->ty == py && f->tz == pz) continue;
        order.action = "move";
        order.tx = px; order.ty = py; order.tz = pz;
        order.mode = "SHADOW";
        order.reason = "shadow " + c.id + ": threat " + num(c.score, 2) + " is below the engage line " + num(o.watchAt, 2) +
                       ", so hold fire and stay in reach";
      } else if (pickReach <= 2 || !cfg.lead) {
        if (f->target == c.id && f->action == "launch") continue;  // already on it
        order.action = "launch";
        order.target = c.id;
        order.mode = "ENGAGE";
        order.reason = "engage " + c.id + ": in reach in " + std::to_string(pickReach) + " ticks; " + why;
      } else {
        if (f->action == "move" && f->tx == px && f->ty == py && f->tz == pz) continue;
        order.action = "move";
        order.tx = px; order.ty = py; order.tz = pz;
        order.mode = "INTERCEPT";
        order.reason = "intercept " + c.id + " at " + std::to_string(px) + "," + std::to_string(py) + " level " +
                       std::to_string(pz) + " in " + std::to_string(pickReach) + " ticks; " + why;
      }
      orders.push_back(order);
      continue;
    }

    // Nothing worth shooting. Go home if damaged, otherwise wait where it can respond fastest.
    const bool damaged = f->hp <= kBingoHp;
    if (assets.empty()) continue;
    double sx = 0, sy = 0;
    if (damaged && !hqs.empty()) {
      sx = hqs[0]->x; sy = hqs[0]->y;
    } else {
      for (const OwnUnit* a : assets) { sx += a->x; sy += a->y; }
      sx /= assets.size(); sy /= assets.size();
      if (cfg.patrol) {
        // Lean two cells toward the aircraft most likely to attack.
        const Contact* watch = nullptr;
        for (const auto& c : o.contacts) {
          if (flies(c.cls) && (!watch || c.score > watch->score)) watch = &c;
        }
        if (watch) {
          const double d = std::max(1e-9, std::hypot(watch->x - sx, watch->y - sy));
          const double lean = std::min(2.0, d);
          sx += (watch->x - sx) / d * lean;
          sy += (watch->y - sy) / d * lean;
        }
      }
    }
    if (!cfg.patrol && !damaged) {
      // Comparison variant: stop attacking a contact that is no longer worth it, and stay put.
      if (f->target.empty()) continue;
      order.action = "cease";
      order.mode = "HOLD";
      order.reason = "break off: no contact is worth an intercept";
      orders.push_back(order);
      continue;
    }
    const int tx = clampi(static_cast<int>(std::lround(sx)), 0, o.width - 1);
    const int ty = clampi(static_cast<int>(std::lround(sy)), 0, o.height - 1);
    const int tz = std::min(3, fp.ceiling);
    order.mode = damaged ? "RTB" : "PATROL";
    if (cheb(f->x, f->y, tx, ty) <= 1) {
      if (f->action == "orbit") continue;
      order.action = "orbit";
      order.tz = tz;
      order.reason = damaged ? "damaged: holding over headquarters" : "on station: orbit until a contact is worth an intercept";
    } else {
      if (f->action == "move" && f->tx == tx && f->ty == ty) continue;
      order.action = "move";
      order.tx = tx; order.ty = ty; order.tz = tz;
      order.reason = damaged ? "damaged: returning to headquarters"
                             : "no contact is worth an intercept: move to the patrol station at " +
                                   std::to_string(tx) + "," + std::to_string(ty);
    }
    orders.push_back(order);
  }

  // --- point defense ------------------------------------------------------------
  std::vector<bool> taken(n, false);
  for (const OwnUnit* hq : hqs) {
    int pick = -1;
    std::string why;
    if (cfg.timedDefense) {
      // The contact with the largest expected loss on the first tick anything can happen.
      for (int t = 1; t <= H && pick < 0; ++t) {
        double best = 0;
        for (size_t i = 0; i < n; ++i) {
          const double v = t >= downAt[i] ? 0.0 : loss[i][static_cast<size_t>(t)];
          if (!o.contacts[i].locked || taken[i] || v <= best) continue;
          best = v;
          pick = static_cast<int>(i);
        }
        if (pick >= 0) {
          why = (t == 1 ? std::string("can cost the most next tick") : "is the first that can do damage, in " + std::to_string(t) + " ticks") +
                " (expected " + num(best) + " hp-ticks)";
        }
      }
    } else {
      pick = untimedPick(n);
      if (pick >= 0) why = "has the highest threat score x damage";
    }
    PlanOrder order;
    order.id = hq->id;
    if (pick >= 0) {
      taken[static_cast<size_t>(pick)] = true;
      const Contact& c = o.contacts[static_cast<size_t>(pick)];
      if (hq->action == "defend" && hq->target == c.id) continue;
      order.action = "defend";
      order.target = c.id;
      order.mode = "DEFEND";
      order.reason = "cover " + c.id + ": it " + why;
      orders.push_back(order);
    } else if (hq->action == "defend") {
      order.action = "cease";
      order.mode = "HOLD";
      order.reason = "no contact that can be locked is a threat within " + std::to_string(H) + " ticks";
      orders.push_back(order);
    }
  }
  return orders;
}

// --- messages ---------------------------------------------------------------------

std::string observationToJson(const Observation& o) {
  std::ostringstream out;
  out << "{\"v\":" << o.version << ",\"tick\":" << o.tick << ",\"width\":" << o.width << ",\"height\":" << o.height
      << ",\"ceiling\":" << o.ceiling << ",\"watchAt\":" << num(o.watchAt, 4) << ",\"own\":[";
  for (size_t i = 0; i < o.own.size(); ++i) {
    const OwnUnit& u = o.own[i];
    out << (i ? "," : "") << "{\"id\":" << jsonString(u.id) << ",\"type\":" << jsonString(u.type) << ",\"x\":" << u.x
        << ",\"y\":" << u.y << ",\"z\":" << u.z << ",\"hp\":" << num(u.hp, 2) << ",\"act\":" << jsonString(u.action)
        << ",\"tgt\":" << jsonString(u.target) << ",\"tx\":" << u.tx << ",\"ty\":" << u.ty << ",\"tz\":" << u.tz << "}";
  }
  out << "],\"contacts\":[";
  for (size_t i = 0; i < o.contacts.size(); ++i) {
    const Contact& c = o.contacts[i];
    out << (i ? "," : "") << "{\"id\":" << jsonString(c.id) << ",\"cls\":" << jsonString(c.cls) << ",\"x\":" << c.x
        << ",\"y\":" << c.y << ",\"z\":" << c.z << ",\"vx\":" << c.vx << ",\"vy\":" << c.vy << ",\"vz\":" << c.vz
        << ",\"snr\":" << num(c.snr, 4) << ",\"locked\":" << (c.locked ? 1 : 0) << ",\"score\":" << num(c.score, 6)
        << ",\"jam\":" << c.jam << "}";
  }
  out << "]}";
  return out.str();
}

bool observationFromJson(const std::string& json, Observation& out) {
  out = Observation{};
  if (!extractIntField(json, "v", out.version) || out.version != 1) return false;
  if (!extractIntField(json, "tick", out.tick)) return false;
  extractIntField(json, "width", out.width);
  extractIntField(json, "height", out.height);
  extractIntField(json, "ceiling", out.ceiling);
  extractNumberField(json, "watchAt", out.watchAt);
  if (json.find("\"own\"") == std::string::npos || json.find("\"contacts\"") == std::string::npos) return false;
  for (const auto& obj : splitObjects(extractArrayBody(json, "own"))) {
    OwnUnit u;
    if (!extractStringField(obj, "id", u.id) || !extractStringField(obj, "type", u.type)) return false;
    extractIntField(obj, "x", u.x);
    extractIntField(obj, "y", u.y);
    extractIntField(obj, "z", u.z);
    extractNumberField(obj, "hp", u.hp);
    extractStringField(obj, "act", u.action);
    extractStringField(obj, "tgt", u.target);
    extractIntField(obj, "tx", u.tx);
    extractIntField(obj, "ty", u.ty);
    extractIntField(obj, "tz", u.tz);
    out.own.push_back(u);
  }
  for (const auto& obj : splitObjects(extractArrayBody(json, "contacts"))) {
    Contact c;
    if (!extractStringField(obj, "id", c.id) || !extractStringField(obj, "cls", c.cls)) return false;
    extractIntField(obj, "x", c.x);
    extractIntField(obj, "y", c.y);
    extractIntField(obj, "z", c.z);
    extractIntField(obj, "vx", c.vx);
    extractIntField(obj, "vy", c.vy);
    extractIntField(obj, "vz", c.vz);
    extractNumberField(obj, "snr", c.snr);
    int locked = 0;
    extractIntField(obj, "locked", locked);
    c.locked = locked != 0;
    extractNumberField(obj, "score", c.score);
    extractIntField(obj, "jam", c.jam);
    out.contacts.push_back(c);
  }
  return true;
}

std::string planToJson(int tick, const std::vector<PlanOrder>& orders, double micros) {
  std::ostringstream out;
  out << "{\"ok\":true,\"v\":1,\"tick\":" << tick << ",\"micros\":" << num(micros, 1) << ",\"orders\":[";
  for (size_t i = 0; i < orders.size(); ++i) {
    const PlanOrder& p = orders[i];
    out << (i ? "," : "") << "{\"id\":" << jsonString(p.id) << ",\"action\":" << jsonString(p.action) << ",\"target\":"
        << jsonString(p.target) << ",\"tx\":" << p.tx << ",\"ty\":" << p.ty << ",\"tz\":" << p.tz << ",\"mode\":"
        << jsonString(p.mode) << ",\"reason\":" << jsonString(p.reason) << "}";
  }
  out << "]}";
  return out.str();
}
