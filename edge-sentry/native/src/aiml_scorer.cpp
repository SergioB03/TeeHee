#include "aiml_scorer.hpp"
#include "model_weights.hpp"
#include "sequence_weights.hpp"

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

// The heuristic keeps its fixed 0.33 / 0.66 cut-offs. The learned model's
// cut-offs are operating points chosen on validation rollouts by ml/train.py.
double watchThreshold(ScorerKind kind) {
  if (kind == ScorerKind::Learned) return edge_model::kWatchAt;
  if (kind == ScorerKind::Sequence) return edge_seq::kWatchAt;
  return 0.33;
}
double hostileThreshold(ScorerKind kind) {
  if (kind == ScorerKind::Learned) return edge_model::kHostileAt;
  if (kind == ScorerKind::Sequence) return edge_seq::kHostileAt;
  return 0.66;
}

double sigmoid(double v) { return 1.0 / (1.0 + std::exp(-v)); }
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

// One hidden layer of tanh units, sigmoid output:
// P(contact harms a blue asset within the label horizon).
// ml/evaluate.py checks that this function and the Python reference agree on
// every held-out row.
double learnedScore(const std::vector<double>& inputs) {
  double z = edge_model::kOutBias;
  for (int h = 0; h < edge_model::kHidden; ++h) {
    double a = edge_model::kHiddenBias[h];
    for (int i = 0; i < edge_model::kNumInputs; ++i) {
      const double x = static_cast<size_t>(i) < inputs.size() ? clamp01(inputs[static_cast<size_t>(i)]) : 0.0;
      a += edge_model::kHiddenWeights[h][i] * x;
    }
    z += edge_model::kOutWeights[h] * std::tanh(a);
  }
  return 1.0 / (1.0 + std::exp(-z));
}

double learnedWatchAt() { return edge_model::kWatchAt; }
int sequenceHiddenSize() { return edge_seq::kHidden; }

// Standard GRU cell, same gate order and equations as torch.nn.GRU:
//   r = sigmoid(Wir x + bir + Whr h + bhr)
//   z = sigmoid(Wiz x + biz + Whz h + bhz)
//   n = tanh(Win x + bin + r * (Whn h + bhn))
//   h' = (1 - z) * n + z * h
void sequenceStep(const std::vector<double>& inputs, std::vector<double>& hidden) {
  const int H = edge_seq::kHidden;
  const int I = edge_seq::kNumInputs;
  hidden.resize(static_cast<size_t>(H), 0.0);
  std::vector<double> gi(static_cast<size_t>(3 * H)), gh(static_cast<size_t>(3 * H));
  for (int row = 0; row < 3 * H; ++row) {
    double a = edge_seq::kBiasIh[row];
    for (int i = 0; i < I; ++i) {
      const double x = static_cast<size_t>(i) < inputs.size() ? clamp01(inputs[static_cast<size_t>(i)]) : 0.0;
      a += edge_seq::kWeightIh[row][i] * x;
    }
    double b = edge_seq::kBiasHh[row];
    for (int j = 0; j < H; ++j) b += edge_seq::kWeightHh[row][j] * hidden[static_cast<size_t>(j)];
    gi[static_cast<size_t>(row)] = a;
    gh[static_cast<size_t>(row)] = b;
  }
  std::vector<double> next(static_cast<size_t>(H));
  for (int j = 0; j < H; ++j) {
    const size_t r = static_cast<size_t>(j), z = static_cast<size_t>(H + j), n = static_cast<size_t>(2 * H + j);
    const double rg = sigmoid(gi[r] + gh[r]);
    const double zg = sigmoid(gi[z] + gh[z]);
    const double ng = std::tanh(gi[n] + rg * gh[n]);
    next[r] = (1.0 - zg) * ng + zg * hidden[r];
  }
  hidden = next;
}

double sequenceScoreFromHidden(const std::vector<double>& hidden) {
  double z = edge_seq::kOutBias;
  for (int j = 0; j < edge_seq::kHidden && static_cast<size_t>(j) < hidden.size(); ++j) {
    z += edge_seq::kOutWeights[j] * hidden[static_cast<size_t>(j)];
  }
  return sigmoid(z);
}

std::string labelForScore(double score, ScorerKind kind) {
  if (score >= hostileThreshold(kind)) return "hostile";
  if (score >= watchThreshold(kind)) return "watch";
  return "benign";
}

ThreatScore scoreTrack(const TrackFeatures& f, const std::vector<double>& inputs,
                       const std::vector<double>& sequenceHidden, ScorerKind kind) {
  ThreatScore out;
  out.heuristicScore = heuristicScore(f);
  out.learnedScore = learnedScore(inputs);
  out.sequenceScore = sequenceScoreFromHidden(sequenceHidden);
  if (kind == ScorerKind::Learned) {
    out.scorer = "learned";
    out.score = out.learnedScore;
  } else if (kind == ScorerKind::Sequence) {
    out.scorer = "sequence";
    out.score = out.sequenceScore;
  } else {
    out.scorer = "heuristic";
    out.score = out.heuristicScore;
  }
  out.label = labelForScore(out.score, kind);
  out.watchAt = watchThreshold(kind);
  out.hostileAt = hostileThreshold(kind);
  out.features = heuristicInputs(f);
  out.observed.reserve(inputs.size());
  for (double v : inputs) out.observed.push_back(clamp01(v));
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
