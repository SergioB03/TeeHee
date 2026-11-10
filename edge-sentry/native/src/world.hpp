#pragma once

#include "aiml_scorer.hpp"

#include <string>
#include <vector>

enum class UnitType { Sensor, Relay, Jammer, Jet, Missile, HQ };
enum class Team { Blue, Red, Unknown };
enum class OrderAction { None, Move, Jam, Launch, Cease };

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
  static World fromJson(const std::string& json);
  std::string toJson(bool pretty = false) const;

  Unit* findUnit(const std::string& id);
  const Unit* findUnit(const std::string& id) const;

  void reset();
  void tickOnce();
  void scoreAllTracks();
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