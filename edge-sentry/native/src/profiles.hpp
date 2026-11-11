#pragma once

#include <string>

// How a unit type flies and fights. Ground types have ceiling 0. One table,
// shared by the simulator (the rules) and the autonomy planner (what it
// assumes about each class of contact). Game numbers, not real performance
// data: the stealth fighter is fast and hard to see, the bomber slow, obvious
// and heavy-hitting from high level, the drone slow and low. Missiles may hit
// from any level.
struct AirProfile {
  int speed;         // cells per tick
  int climb;         // altitude levels per tick, up or down
  int ceiling;       // highest level it can reach
  int cruise;        // level it starts at
  int release;       // must be at or below this level to hit a ground target
  double signature;  // how strongly sensors see it (1 = ordinary)
  double damage;     // to a ground target per hit
  double hitFactor;  // multiplies the hit chance
  bool airToAir;     // may attack aircraft
};

inline const AirProfile& profileByName(const std::string& type) {
  //                                  speed climb ceil cruise release sig   dmg  hit   a2a
  static const AirProfile ground   = {1,    0,    0,   0,     0,      1.0,  0,   0.0,  false};
  static const AirProfile jet      = {1,    1,    5,   3,     2,      1.0,  15,  0.6,  true};
  static const AirProfile stealth  = {2,    2,    5,   4,     3,      0.35, 20,  0.7,  true};
  static const AirProfile bomber   = {1,    1,    5,   4,     4,      1.3,  35,  0.5,  false};
  static const AirProfile drone    = {1,    1,    2,   1,     1,      0.5,  8,   0.5,  false};
  static const AirProfile missile  = {1,    1,    5,   2,     5,      0.8,  40,  1.0,  true};
  if (type == "jet") return jet;
  if (type == "stealth") return stealth;
  if (type == "bomber") return bomber;
  if (type == "drone") return drone;
  if (type == "missile") return missile;
  return ground;
}
