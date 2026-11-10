#pragma once

#include <string>
#include <vector>

// AIML = AI/ML track threat scoring.
//
// Two scorers share one interface:
//   - heuristic: hand-set weights over TrackFeatures (the original v1).
//   - learned:   logistic regression trained on simulator rollouts (see ml/),
//                weights generated into model_weights.hpp.
// The learned scorer is the default. `edge-sim --scorer heuristic` selects the
// baseline so the two can be compared on identical worlds.

// Inputs to the heuristic. closingOnBlue reads the contact's actual target
// assignment, which is ground truth the sim knows but a real sensor would not.
struct TrackFeatures {
  double speed = 0;           // cells per tick
  double snr = 0;             // sensor quality 0..1
  double jamPressure = 0;     // 0..1 emitter strength or EW cover
  double proximityBlue = 0;   // 0..1 closer to blue assets => higher
  double altitudeHint = 0;    // 0 ground-ish .. 1 air-ish (from unit type prior)
  double closingOnBlue = 0;   // 0..1 actively targeting / homing on blue
};

// Inputs to the learned model. Everything here is observable from the blue
// side: kinematics, emissions, and effects on blue assets. Nothing is read
// from the contact's orders or target. All values are scaled to 0..1.
struct ObservedFeatures {
  double speed = 0;        // observed displacement over the last tick
  double snr = 0;          // best blue sensor signal quality on the contact
  double emitter = 0;      // 1 if the contact is radiating as a jammer
  double proximity = 0;    // closeness to the nearest blue asset
  double airborne = 0;     // altitude prior for the contact class
  double closing = 0;      // fastest range rate toward any blue asset
  double heading = 0;      // how directly its motion points at a blue asset
  double aimRange = 0;     // closeness of the asset it is pointed at (0 if none)
  double blueInField = 0;  // strongest jam pressure it puts on a blue asset
  double ewCover = 0;      // red jam field strength at the contact
  double recentFire = 0;   // 1 if it damaged a blue asset in the last 3 ticks
  double intercept = 0;    // 1 - (ticks until it reaches a blue asset at its current range rate) / 10
};

enum class ScorerKind { Learned, Heuristic };

struct ThreatScore {
  double score = 0;              // 0..1, from the active scorer
  std::string label;             // benign | watch | hostile
  std::string scorer;            // "learned" | "heuristic"
  double watchAt = 0.33;         // label thresholds for the active scorer
  double hostileAt = 0.66;
  double heuristicScore = 0;     // always computed, for comparison
  double learnedScore = 0;       // always computed, for comparison
  std::vector<double> features;  // heuristic inputs (6)
  std::vector<double> observed;  // learned-model inputs (12)
};

double heuristicScore(const TrackFeatures& f);
double learnedScore(const ObservedFeatures& o);
std::string labelForScore(double score, ScorerKind kind);

ThreatScore scoreTrack(const TrackFeatures& f, const ObservedFeatures& o, ScorerKind kind);

// Softmax-style class probs for learning demos / eval dumps.
struct ClassProbs {
  double benign = 0;
  double watch = 0;
  double hostile = 0;
};

ClassProbs classProbsFromScore(double score);
