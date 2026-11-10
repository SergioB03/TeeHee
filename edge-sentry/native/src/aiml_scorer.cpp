#include "aiml_scorer.hpp"

#include <algorithm>
#include <cmath>

namespace {
// Weights sum to 1.0. closingOnBlue lets a fast air contact cross hostile
// without needing to sit inside a jam field (see REVIEW_FINDINGS #6).
constexpr double W_SPEED = 0.15;
constexpr double W_SNR = 0.08;
constexpr double W_JAM = 0.18;
constexpr double W_PROX = 0.20;
constexpr double W_ALT = 0.12;
constexpr double W_CLOSE = 0.27;

double clamp01(double v) {
  return std::max(0.0, std::min(1.0, v));
}
}  // namespace

ThreatScore scoreTrack(const TrackFeatures& f) {
  const double speedN = clamp01(f.speed / 3.0);
  const double snrN = clamp01(f.snr);
  const double jamN = clamp01(f.jamPressure);
  const double proxN = clamp01(f.proximityBlue);
  const double altN = clamp01(f.altitudeHint);
  const double closeN = clamp01(f.closingOnBlue);

  const double logit =
      W_SPEED * speedN +
      W_SNR * (1.0 - snrN) +  // low SNR under EW pressure looks worse
      W_JAM * jamN +
      W_PROX * proxN +
      W_ALT * altN +
      W_CLOSE * closeN;

  ThreatScore out;
  out.score = clamp01(logit);
  out.features = {speedN, snrN, jamN, proxN, altN, closeN};

  if (out.score >= 0.66) out.label = "hostile";
  else if (out.score >= 0.33) out.label = "watch";
  else out.label = "benign";
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