#pragma once

#include "aiml_scorer.hpp"

#include <string>
#include <vector>

enum class UnitType { Sensor, Relay, Jammer, Jet, Missile, HQ };
enum class Team { Blue, Red, Unknown };
enum class OrderAction { None, Move, Jam, Launch, Cease, Defend };

struct Order {
  OrderAction action = OrderAction::None;
  int tx = -1;
  int ty = -1;
  std::string targetId;
};

struct Unit {
  std::string id;
  UnitType type = UnitType::Sensor;
  Team team = Team::Blue;
  int x = 0;
  int y = 0;
  int px = 0;               // position one tick ago (for observed velocity)
  int py = 0;
  double hp = 100;
  double integrity = 1.0;   // sensor quality
  int jamRadius = 0;        // for jammers
  std::string targetId;     // missiles / jets
  int lastHitTick = -1000;  // tick this unit last damaged another unit
  // Short track history, updated once per tick (see World::recordHistory).
  double c0 = 0, c1 = 0, c2 = 0;              // closing: now, 1 and 2 ticks ago
  double h0 = 0, h1 = 0, h2 = 0;              // heading: now, 1 and 2 ticks ago
  double r0 = -1, r1 = -1, r2 = -1, r3 = -1;  // range to nearest blue asset; -1 = unknown
  int stopTicks = 0;                          // consecutive ticks without moving
  bool moved = false;                         // has it ever moved
  // Sequence model memory: the GRU hidden state after the last tick it saw.
  std::vector<double> seqHidden;
  int seqTick = -1;                           // tick the hidden state was last advanced on; -1 = never
  Order order;
  bool alive = true;
};

struct Track {
  std::string id;
  std::string sourceUnitId;
  double x = 0;
  double y = 0;
  double vx = 0;
  double vy = 0;
  double snr = 1.0;
  ThreatScore threat;
};

struct SimEvent {
  int tick = 0;
  std::string kind;
  std::string detail;
};

// One order the computer opponent wants to give, and why.
struct OpponentMove {
  std::string id;
  OrderAction action = OrderAction::None;
  int tx = -1;
  int ty = -1;
  std::string targetId;
  std::string reason;
};

// What blue can measure about one contact's motion relative to blue assets.
struct Kinematics {
  double closing = 0;    // fastest range rate toward any blue asset, 0..1
  double heading = 0;    // best alignment of its motion with a blue asset, 0..1
  double aimRange = 0;   // closeness of the asset it is pointed at, 0..1
  double intercept = 0;  // 1 - (ticks to reach an asset at this range rate) / 10
  double miss = 0;       // 1 - (how far its line of motion passes from that asset) / 3
  double nearest = 1e9;  // range to the nearest blue asset, in cells
};

struct World {
  int tick = 0;
  int width = 12;
  int height = 12;
  std::vector<Unit> units;
  std::vector<Track> tracks;
  std::vector<SimEvent> events;
  std::vector<int> cover; // width*height, 0/1

  // Which scorer fills threatScore/threatLabel. Process-wide so it applies to
  // worlds scored while loading; set from `--scorer` before any world exists.
  static ScorerKind activeScorer;

  static World defaultScenario();
  // A larger, order-free setup for play mode: nobody has a target yet.
  static World skirmishScenario();
  static World fromJson(const std::string& json);
  std::string toJson(bool pretty = false) const;

  Unit* findUnit(const std::string& id);
  const Unit* findUnit(const std::string& id) const;

  void reset();
  void tickOnce();
  void scoreAllTracks();
  Kinematics kinematics(const Unit& u) const;
  void recordHistory();
  // True if an alive HQ's point defense is assigned to this unit this tick.
  bool isDefended(const std::string& id, std::string* byHq = nullptr) const;
  // Computer opponent: the orders `side` would give right now, with reasons.
  std::vector<OpponentMove> planOpponent(Team side) const;
  bool spawn(UnitType type, Team team, int x, int y, std::string* err);
  bool issueOrder(const std::string& id, OrderAction action, int tx, int ty,
                  const std::string& targetId, std::string* err);

  double jamFieldAt(int x, int y, Team against) const;
  double dist(int x1, int y1, int x2, int y2) const;
};

std::string unitTypeToString(UnitType t);
bool unitTypeFromString(const std::string& s, UnitType& out);
std::string teamToString(Team t);
bool teamFromString(const std::string& s, Team& out);
std::string orderToString(OrderAction a);
bool orderFromString(const std::string& s, OrderAction& out);