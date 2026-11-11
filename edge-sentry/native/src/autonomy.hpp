#pragma once

// Autonomy for the defending side: which contact point defense covers and how
// each fighter flies. It works only from an Observation, the message the
// simulator (or any other source) sends each tick. An Observation holds what
// the side can measure about itself and its contacts. It never holds a
// contact's orders or target.
//
// The same planner runs in two places: inside edge-sim (`edge-sim autopilot`)
// and inside edge-node, a separate process meant for a small computer on the
// network. Both read the Observation as JSON, so they decide identically.

#include <string>
#include <vector>

struct OwnUnit {
  std::string id;
  std::string type;
  int x = 0, y = 0, z = 0;
  double hp = 100;
  std::string action;  // current order: none, move, altitude, orbit, launch, defend, ...
  std::string target;  // unit it is attacking or defending against, if any
  int tx = -1, ty = -1, tz = -1;
};

struct Contact {
  std::string id;
  std::string cls;      // contact class: jet, stealth, bomber, drone, missile, jammer, ...
  int x = 0, y = 0, z = 0;
  int vx = 0, vy = 0, vz = 0;  // observed movement over the last tick
  double snr = 0;
  bool locked = false;  // track is good enough for point defense to shoot at
  double score = 0;     // threat model: chance it harms a blue unit within 5 ticks
  int jam = 0;          // jam field radius if it is an emitter
  std::string hit;      // one of our units it damaged in the last 3 ticks, if any
};

struct Observation {
  int version = 1;
  int tick = 0;
  int width = 12, height = 12, ceiling = 5;
  double watchAt = 0.33;  // threat score at which a contact is labelled "watch"
  std::vector<OwnUnit> own;
  std::vector<Contact> contacts;
};

struct PlanOrder {
  std::string id;
  std::string action;
  int tx = -1, ty = -1, tz = -1;
  std::string target;
  std::string mode;    // behavior this order belongs to: DEFEND, HOLD, PATROL, SHADOW, INTERCEPT, ENGAGE, RTB
  std::string reason;
};

struct PlannerConfig {
  int horizon = 16;       // ticks the expected-loss model looks ahead
  bool lead = true;       // fly to where the target will be, not where it is
  bool timedDefense = true;  // point defense goes to the contact about to do the most damage
  bool patrol = true;     // with nothing to shoot, wait between the assets and the likeliest attacker
  bool holdFire = true;   // only shoot at contacts labelled watch or hostile; shadow the rest
  bool selfDefense = true;  // fight a contact that is hunting one of our aircraft: shoot back whatever its
                            // label, never turn and run from it, and let point defense cover it
  // What counts as a contact hunting one of our aircraft. 0 (default): it has hit one. 2: also a fighter inside
  // two cells, at our level, flying straight at us. Tuning showed 2 saves the fighter slightly more often but
  // shoots more aircraft that were only passing, so a hit is what clears a fighter to shoot back.
  int huntMode = 0;
  // An engaged fighter stays on its target unless the score falls well below the watch line. Off by default:
  // it stops some back-and-forth between shadowing and engaging, at the cost of more bystanders shot.
  bool commit = false;
  // A damaged fighter (at or below half health) stops picking fights with armed aircraft that have done nothing
  // yet, but keeps attacking the ones that cannot shoot back (bombers, drones) and the ones already seen hitting
  // a blue unit. Going home helps nobody: nothing is repaired there, and the strike carries on without it.
  // Off ("gohome"): it fights only a contact hunting it and otherwise returns to headquarters.
  bool pressOn = true;
  // How much of a covered contact's loss point defense is trusted to stop. Below
  // 1 because a lock can drop, headquarters can be hit, and the model can be
  // wrong; it is what makes a fighter worth sending at a contact that point
  // defense is already covering.
  double coverTrust = 0.75;
};
// "full", or one behavior switched off for comparison: "nolead", "notimed", "nopatrol", "freefire", "nodefense",
// "gohome", "untaught" (neither self-defense nor pressing on: the planner before it was taught either);
// or one option switched on: "closein" (a close approach also counts as hunting), "commit".
bool plannerConfigFromName(const std::string& name, PlannerConfig& out);

std::string observationToJson(const Observation& o);
bool observationFromJson(const std::string& json, Observation& out);

// The orders to give now. Units that should carry on as they are get no order.
std::vector<PlanOrder> planDefense(const Observation& o, const PlannerConfig& cfg = {});
std::string planToJson(int tick, const std::vector<PlanOrder>& orders, double micros);
