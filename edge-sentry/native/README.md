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
./build/edge-sim --world /tmp/world.json order --id B-HQ --action defend --target R-MSL1 --actor cli --reason "closing on HQ"
./build/edge-sim --world /tmp/world.json decisions --n 10
./build/edge-sim inputs
```

`defend` is an abstract point defense for an HQ: it covers against one contact at a time. A covered contact's hit is blocked (a missile is expended) and a covered jammer's field is suppressed.

Every `order`, `spawn` and `reset` is appended to `<world path>.decisions.jsonl` with the actor, the reason, whether the sim accepted it, and the threat picture beforehand. `--no-log` skips it for bulk rollouts.

## AIML hook

`src/aiml_scorer.cpp` maps each track to `{benign, watch, hostile}` with one of two scorers:

- `--scorer learned` (default): a small neural network (16 tanh units) over 24 observed inputs, trained on simulator rollouts. Weights are generated into `src/model_weights.hpp` by `ml/train.py`; `edge-sim inputs` lists the inputs. See `../ml/README.md`.
- `--scorer heuristic`: the hand-weighted baseline over 6 features (speed, snr, jamPressure, proximityBlue, altitudeHint, closingOnBlue).

Every track reports `threatScore` and `threatLabel` from the active scorer, plus `learnedScore`, `heuristicScore`, the `observed` and `features` input vectors, and the `watchAt` / `hostileAt` cut-offs in use.

Units carry `px` / `py` (position one tick ago) and a short track history (`c0..c2`, `h0..h2`, `r0..r3`, `stopTicks`, `moved`), so tracks have an observed velocity and history that survive between CLI calls.
