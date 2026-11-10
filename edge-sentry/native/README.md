# edge-sim (C++)

Abstract terrain EW / air sandbox for `edge-sentry`.

**Game math only** — jam cones, tracks, jets, missiles, and AIML threat scores are simulation labels. No real RF jammer recipes, seeker/guidance code, or exploit tooling.

## Build

```bash
cmake -S . -B build
cmake --build build
```

Binary: `native/build/edge-sim`

## Tests

```bash
python3 tests/test_cli.py   # CLI regression checks, stdlib only
```

## Commands

```bash
./build/edge-sim --world /tmp/world.json reset
./build/edge-sim --world /tmp/world.json status
./build/edge-sim --world /tmp/world.json tick --n 3
./build/edge-sim --world /tmp/world.json score
./build/edge-sim --world /tmp/world.json spawn --type jammer --team red --x 7 --y 7
./build/edge-sim --world /tmp/world.json order --id R-JET1 --action move --tx 5 --ty 5
```

## AIML hook

`src/aiml_scorer.cpp` maps each track to `{benign, watch, hostile}` with one of two scorers:

- `--scorer learned` (default): logistic regression over 12 observed features, trained on simulator rollouts. Weights are generated into `src/model_weights.hpp` by `ml/train.py`. See `../ml/README.md`.
- `--scorer heuristic`: the hand-weighted baseline over 6 features (speed, snr, jamPressure, proximityBlue, altitudeHint, closingOnBlue).

Every track reports `threatScore` and `threatLabel` from the active scorer, plus `learnedScore`, `heuristicScore`, the `observed` and `features` input vectors, and the `watchAt` / `hostileAt` cut-offs in use.

Units carry `px` / `py` (position one tick ago) so tracks have a real observed velocity that survives between CLI calls.
