#include "aiml_scorer.hpp"
#include "model_weights.hpp"

#include <algorithm>
#include <cmath>

namespace {
// Heuristic weights sum to 1.0. closingOnBlue lets a fast air contact cross
// hostile without needing to sit inside a jam field (see REVIEW_FINDINGS #6).
constexpr double W_SPEED = 0.15;
constexpr double W_SNR = 0.08;
constexpr double W_JAM = 0.18;
constexpr double W_PROX = 0.20;
constexpr double W_ALT = 0.12;
constexpr double W_CLOSE = 0.27;

double clamp01(double v) {
  return std::max(0.0, std::min(1.0, v));
}

std::vector<double> heuristicInputs(const TrackFeatures& f) {
  return {
      clamp01(f.speed / 3.0), clamp01(f.snr),          clamp01(f.jamPressure),
      clamp01(f.proximityBlue), clamp01(f.altitudeHint), clamp01(f.closingOnBlue),
  };
}

std::vector<double> observedInputs(const ObservedFeatures& o) {
  return {
      clamp01(o.speed),   clamp01(o.snr),         clamp01(o.emitter), clamp01(o.proximity),
      clamp01(o.airborne), clamp01(o.closing),     clamp01(o.heading), clamp01(o.aimRange),
      clamp01(o.blueInField), clamp01(o.ewCover),  clamp01(o.recentFire), clamp01(o.intercept),
  };
}
}  // namespace

double heuristicScore(const TrackFeatures& f) {
  const std::vector<double> x = heuristicInputs(f);
  const double logit =
      W_SPEED * x[0] +
      W_SNR * (1.0 - x[1]) +  // low SNR under EW pressure looks worse
      W_JAM * x[2] +
      W_PROX * x[3] +
      W_ALT * x[4] +
      W_CLOSE * x[5];
  return clamp01(logit);
}

// Logistic regression: P(contact harms a blue asset within the label horizon).
// Input order must match ml/simlab.py MODEL_INPUTS; ml/evaluate.py checks that
// this function and the Python reference agree on every held-out row.
double learnedScore(const ObservedFeatures& o) {
  const std::vector<double> x = observedInputs(o);
  double z = edge_model::kBias;
  for (int i = 0; i < edge_model::kNumObserved; ++i) z += edge_model::kWeights[i] * x[static_cast<size_t>(i)];
  // Interaction terms: motion toward blue matters more when already close.
  z += edge_model::kWeights[edge_model::kNumObserved] * (x[5] * x[3]);      // closing * proximity
  z += edge_model::kWeights[edge_model::kNumObserved + 1] * (x[6] * x[7]);  // heading * aimRange
  return 1.0 / (1.0 + std::exp(-z));
}

// The heuristic keeps its fixed 0.33 / 0.66 cut-offs. The learned model's
// cut-offs are operating points chosen on validation rollouts by ml/train.py.
static double watchThreshold(ScorerKind kind) {
  return kind == ScorerKind::Learned ? edge_model::kWatchAt : 0.33;
}
static double hostileThreshold(ScorerKind kind) {
  return kind == ScorerKind::Learned ? edge_model::kHostileAt : 0.66;
}

std::string labelForScore(double score, ScorerKind kind) {
  if (score >= hostileThreshold(kind)) return "hostile";
  if (score >= watchThreshold(kind)) return "watch";
  return "benign";
}

ThreatScore scoreTrack(const TrackFeatures& f, const ObservedFeatures& o, ScorerKind kind) {
  ThreatScore out;
  out.heuristicScore = heuristicScore(f);
  out.learnedScore = learnedScore(o);
  out.scorer = (kind == ScorerKind::Learned) ? "learned" : "heuristic";
  out.score = (kind == ScorerKind::Learned) ? out.learnedScore : out.heuristicScore;
  out.label = labelForScore(out.score, kind);
  out.watchAt = watchThreshold(kind);
  out.hostileAt = hostileThreshold(kind);
  out.features = heuristicInputs(f);
  out.observed = observedInputs(o);
  return out;
}

ClassProbs classProbsFromScore(double score) {
  // Temperature-scaled soft assignment around thresholds — placeholder for a classifier head.
  const double t = 0.18;
  const double b = std::exp((0.20 - score) / t);
  const double w = std::exp(-(std::abs(score - 0.50)) / t);
  const double h = std::exp((score - 0.80) / t);
  const double z = b + w + h;
  return {b / z, w / z, h / z};
}
