#pragma once

#include <string>
#include <vector>

// AIML = AI/ML track threat scoring.
// v1: weighted heuristic on hand-crafted features (learnable later).
// Swap scoreTrack() internals for a trained model without changing the sim API.

struct TrackFeatures {
  double speed = 0;          // cells per tick
  double snr = 0;            // sensor quality 0..1
  double jamPressure = 0;    // 0..1 how hard this track is jammed/jamming
  double proximityBlue = 0;  // 0..1 closer to blue assets => higher
  double altitudeHint = 0;   // 0 ground-ish .. 1 air-ish (from unit type prior)
};

struct ThreatScore {
  double score = 0;          // 0..1
  std::string label;         // benign | watch | hostile
  std::vector<double> features; // exported for later training dumps
};

ThreatScore scoreTrack(const TrackFeatures& f);

// Softmax-style class probs for learning demos / eval dumps.
struct ClassProbs {
  double benign = 0;
  double watch = 0;
  double hostile = 0;
};

ClassProbs classProbsFromScore(double score);