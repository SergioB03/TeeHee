# edge-sim and edge-node (C++17)

The simulator behind `edge-sentry`, and the autonomy planner as a program of its own. No dependencies beyond the standard library and POSIX sockets.

**Game math only.** Jam fields, tracks, aircraft, missiles and threat scores are simulation labels. No real RF jammer recipes, seeker or guidance code, or exploit tooling.

## Build

```bash
cmake -S . -B build && cmake --build build     # or, from the folder above: npm run build:native
```

| Program | What it is |
| --- | --- |
| `build/edge-sim` | The simulator: world, tick loop, sensing, threat models, both computer opponents. One JSON reply per command |
| `build/edge-node` | The autonomy planner alone, for a separate computer. It reads observations and writes plans, over TCP or stdin |

## Tests

```bash
python3 tests/test_cli.py       # the simulator, through its command line
python3 tests/test_planner.py   # the planner: written-out observations in, orders out; and the node over TCP
```

Standard library only. Either takes the path of a different binary as its argument, which is how CI runs them against the arm64 build.

## Commands

The world lives in a JSON file, so every command is one process: load, act, save, print.

```bash
S="./build/edge-sim --world /tmp/world.json"
$S reset                      # the default scenario; add --scenario skirmish for the larger one play mode uses
$S status                     # the whole world: units, tracks with threat scores, recent events
$S tick --n 3
$S spawn --type jet --team red --x 10 --y 2 --z 3
$S order --id R-JET1 --action launch --target B-HQ
$S order --id B-HQ --action defend --target R-JET1 --actor me --reason "closing on HQ"
$S order --id R-JET1 --action move --tx 5 --ty 5 --tz 4     # also: altitude, orbit, jam, cease
$S observe                    # what blue's autonomy is given: own units and tracks, never red's orders
$S autopilot                  # run the planner for blue and give its orders (add --dry-run to only look)
$S opponent --side red        # let the computer give red's orders for this tick
$S sensing --noise 0.7        # sensors miss contacts and report noisy positions; a Kalman tracker fuses them
$S decisions --n 10           # the decision log
./build/edge-sim inputs       # the learned model's input names, in order
```

Run `edge-sim` with no arguments for the full list of options.

For bulk work there is `edge-sim serve`: one process that keeps the world in memory and reads one command per line on stdin, the same words separated by tabs, answering each with one line of JSON. `load<TAB>{world json}` replaces the world, and `--no-world` on any command leaves the world out of the reply. `observe` also reports `blueValue`, blue's mission value, beside the observation (never inside it): that is the reward the learned policy trains on. A served game is the same game; a test plays one both ways and compares every reply.

The rules, briefly:

- **Altitude.** Levels run from 0 (ground) to 5. Each aircraft type has its own speed, climb rate, ceiling, signature, damage and release altitude, all in one table (`src/profiles.hpp`). An attacker has to come down to its release altitude to hit a ground target.
- **Tracks.** Track quality falls with range, with a small signature, and behind cover at low level. Point defense cannot stop a contact it has no lock on.
- **Point defense.** `defend` covers against one contact per tick. A covered contact's hit is blocked (a missile is expended) and a covered jammer's field is suppressed.
- **Decision log.** Every `order`, `spawn` and `reset`, accepted or refused, is appended to `<world path>.decisions.jsonl` with the actor, the reason and the threat picture beforehand. `--no-log` skips it for bulk rollouts.

## Threat models

`src/aiml_scorer.cpp` labels each track `benign`, `watch` or `hostile` with one of three scorers:

- `--scorer learned` (default): a small neural network (16 tanh units) over 28 observed inputs, trained on simulator rollouts. `ml/train.py` generates its weights into `src/model_weights.hpp`.
- `--scorer sequence`: a 16-unit GRU that sees only the 20 inputs describing the current tick and remembers the track itself. Each contact's hidden state is saved in the world file and advanced once per tick.
- `--scorer heuristic`: the original hand-weighted baseline over 6 features. It reads the contact's real target, so it is kept only as a reference.

Every track reports the active scorer's `threatScore` and `threatLabel`, all three scores, the input vector, and the cut-offs in use. See [`../ml/README.md`](../ml/README.md).

## The planner and the node

`src/autonomy.cpp` plans blue's defense from one `Observation` message and nothing else: which contact point defense covers, and what each fighter does. It keeps no state between ticks. `edge-sim autopilot` and `edge-node` run the same code through the same message, so they cannot decide differently; [`../docs/INTERFACE.md`](../docs/INTERFACE.md) is the contract.

```bash
./build/edge-node --listen 7700                  # this machine only; add --bind 0.0.0.0 for the network
./build/edge-node --stdin                        # observations on stdin, plans on stdout
./build/edge-node --ping 127.0.0.1:7700          # is a node alive there? exit 0 if so
```

The node is one thread that watches every connection with `poll()`, and it stops at once on SIGTERM. [`../sil/`](../sil/README.md) runs it against the simulator over a link, and [`../docker/`](../docker/README.md) puts it in an image by itself.
