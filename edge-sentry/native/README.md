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

`src/aiml_scorer.cpp` maps track features → `{benign, watch, hostile}`.

v1 uses a weighted heuristic. Later: dump `features` + labels, train a small classifier, swap the scorer body without changing the agent tools.
