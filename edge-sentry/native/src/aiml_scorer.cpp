#include "aiml_scorer.hpp"

#include <algorithm>
#include <cmath>

namespace {
constexpr double W_SPEED = 0.20;
constexpr double W_SNR = 0.10;
constexpr double W_JAM = 0.30;
constexpr double W_PROX = 0.25;
constexpr double W_ALT = 0.15;

double clamp01(double v) {
  return std::max(0.0, std::min(1.0, v));
}
}  // namespace

ThreatScore scoreTrack(const TrackFeatures& f) {
  const double logit =
      W_SPEED * clamp01(f.speed / 3.0) +
      W_SNR * (1.0 - clamp01(f.snr)) +  // low SNR under EW pressure looks worse
      W_JAM * clamp01(f.jamPressure) +
      W_PROX * clamp01(f.proximityBlue) +
      W_ALT * clamp01(f.altitudeHint);

  ThreatScore out;
  out.score = clamp01(logit);
  out.features = {
      clamp01(f.speed / 3.0),
      clamp01(f.snr),
      clamp01(f.jamPressure),
      clamp01(f.proximityBlue),
      clamp01(f.altitudeHint),
  };

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