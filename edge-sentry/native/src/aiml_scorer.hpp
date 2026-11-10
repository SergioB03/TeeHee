#pragma once

#include <string>
#include <vector>

// AIML = AI/ML track threat scoring.
//
// Two scorers share one interface:
//   - learned (default): a small neural network trained on simulator rollouts
//     (see ml/). Weights are generated into model_weights.hpp.
//   - heuristic: hand-set weights over TrackFeatures (the original v1).
// `edge-sim --scorer heuristic` selects the baseline so the two can be
// compared on identical worlds.

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

// Inputs to the learned model, in order. Everything is observable from the
// blue side: kinematics now, a few ticks of track history, emissions, and
// effects on blue assets. Nothing is read from the contact's orders or
// target. `edge-sim inputs` prints this list so the Python side cannot drift.
inline const std::vector<std::string>& modelInputNames() {
  static const std::vector<std::string> names = {
      // this tick
      "speed",        // observed displacement over the last tick
      "snr",          // best blue sensor signal quality on the contact
      "emitter",      // 1 if the contact is radiating as a jammer
      "proximity",    // closeness to the nearest blue asset
      "airborne",     // altitude prior for the contact class
      "closing",      // fastest range rate toward any blue asset
      "heading",      // how directly its motion points at a blue asset
      "aimRange",     // closeness of the asset it is pointed at
      "blueInField",  // strongest jam pressure it puts on a blue asset
      "ewCover",      // red jam field strength at the contact
      "recentFire",   // 1 if it damaged a blue asset in the last 3 ticks
      "intercept",    // time until it reaches a blue asset at this range rate
      "inRange",      // 1 if within 1.5 cells of a blue asset
      "onAsset",      // 1 if sitting on a blue asset's cell
      "stopped",      // 1 if it did not move this tick
      "miss",         // how close its current line of motion passes to an asset
      "deadOn",       // 1 if it is pointed almost exactly at an asset
      // track history
      "stopTicks",    // consecutive ticks without moving
      "movedBefore",  // 1 if it has moved at any point
      "rangeDrop3",   // how much closer it is than three ticks ago
      "closing1",     // closing, one tick ago
      "heading1",     // heading, one tick ago
      "closing2",     // closing, two ticks ago
      "heading2",     // heading, two ticks ago
  };
  return names;
}

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
  std::vector<double> observed;  // learned-model inputs, see modelInputNames()
};

double heuristicScore(const TrackFeatures& f);
// `inputs` must follow modelInputNames(); values are clamped to 0..1.
double learnedScore(const std::vector<double>& inputs);
std::string labelForScore(double score, ScorerKind kind);
// The learned model's "worth watching" cut-off, whatever scorer is active.
double learnedWatchAt();

ThreatScore scoreTrack(const TrackFeatures& f, const std::vector<double>& inputs, ScorerKind kind);

// Softmax-style class probs for learning demos / eval dumps.
struct ClassProbs {
  double benign = 0;
  double watch = 0;
  double hostile = 0;
};

ClassProbs classProbsFromScore(double score);
