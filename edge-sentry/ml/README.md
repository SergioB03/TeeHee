# Threat model

A small neural network that replaces the hand-weighted threat score. This page says what it predicts, what it is allowed to see, how it was trained, how it is judged, and where it falls short. Numbers are in [`RESULTS.md`](RESULTS.md) (prediction) and [`OUTCOMES.md`](OUTCOMES.md) (decisions).

## The task

For every red contact on every tick: **will this contact harm a blue asset within the next 5 ticks?** Harm means it damages a blue unit, or it is a jammer holding a blue sensor, relay or HQ inside its field.

Labels come from the simulator's future, not from a rule. Each rollout is run to the end, then every contact-tick is labelled by what that contact went on to do.

## What the model may see

28 inputs, all observable from the blue side and all scaled to 0..1. `edge-sim inputs` prints the list, and the simulator computes every one of them, so the Python side never re-derives a feature.

| Group | Inputs |
| --- | --- |
| Motion this tick | `speed`, `closing` (range rate toward any blue asset), `heading` (how directly it points at one), `aimRange`, `intercept` (time to reach an asset), `miss` (how close its line of motion passes), `deadOn`, `stopped` |
| Position | `proximity`, `inRange` (within 1.5 cells), `onAsset` |
| Altitude | `altitude` (level 0 to 5), `descent` and `climb` (levels changed this tick) |
| Track history | `stopTicks`, `movedBefore`, `rangeDrop3` (closer than three ticks ago), `closing1`, `heading1`, `closing2`, `heading2`, `altDrop3` (altitude lost over three ticks) |
| Emissions and effects | `emitter`, `blueInField`, `ewCover`, `recentFire`, `snr`, `airborne` |

The model never reads a contact's orders or target. The earlier heuristic's `closingOnBlue` input does read the target assignment, which is why it is reported separately as a privileged baseline.

Track history is what closed the gap on jets and missiles. On the earlier flat (2D) simulator, a linear model with single-tick inputs scored AUC 0.918 on air contacts, behind the target-reading heuristic at 0.939. Simple streak counters did not help. Remembering whether a contact has moved, how long it has been stopped, and what it was doing one and two ticks ago took it to 0.949 there: a jet that flew in and stopped beside a sensor is a different thing from one that has sat there all along. In 3D the air-only gap reopened slightly (0.938 vs 0.942); see Limits.

## Data

`simlab.random_scenario(seed)` builds a random blue layout and two to six red contacts: attackers, contacts in transit (half of them routed close to a blue asset without attacking), loiterers, jammers, and harmless ground units. The aircraft are jets, stealth fighters, bombers and drones at varied starting altitudes; some climb, descend or orbit without ever attacking, so a descent on its own does not give an attack away, and about a quarter of rollouts give blue a fighter of its own. About a third change behavior mid-run: attackers stand down, idle contacts turn and attack. Each rollout runs 22 ticks through the real `edge-sim` binary.

- Training: seeds 0 to 479 (32,618 contact-ticks, 31% positive)
- Validation: seeds 480 to 599, used only to choose the two label cut-offs
- Held-out test: seeds 100000 to 100199, never touched by training
- Outcome evaluation: seeds 200000 to 200149

Splits are by rollout, so no scenario contributes rows to more than one split.

## Training

`train.py` fits one hidden layer of 16 tanh units with a sigmoid output (scikit-learn's `MLPClassifier`, fixed seed), then picks the label cut-offs on the validation split by fixed rules:

- **hostile** at the score that maximizes F1 (0.46)
- **watch** at the highest score that still catches 95% of harmful contacts (0.24)

Three different initialization seeds gave the same held-out AUC to three decimals. It writes `native/src/model_weights.hpp`, which is compiled into `edge-sim`, and `model.json` for the Python reference.

```bash
npm run build:native
python3 ml/train.py        # needs numpy and scikit-learn
npm run build:native       # picks up the new weights
python3 ml/evaluate.py     # standard library only
python3 ml/outcome.py      # standard library only, about two minutes
```

## Sequence model (PyTorch)

`train_seq.py` trains a GRU with 16 hidden units on the same rollouts and splits. It reads one contact one tick at a time and sees only the first 20 inputs, the ones that describe the current tick (altitude, climb and descent included). The hand-built history inputs (ticks stopped, range closed and altitude lost over 3 ticks, lagged closing and heading) are withheld, so any memory of the track has to be learned. Validation is used for early stopping and for the same two cut-off rules.

`seqlab.py` holds a plain-Python forward pass and the exporter, so evaluation does not need PyTorch. The trainer refuses to write weights unless that forward pass reproduces PyTorch's scores. In edge-sim each contact's hidden state lives in the world file and advances once per tick.

Result on the 3D simulator: the same held-out AUC as the default model (0.967 vs 0.968), the same scripted scenarios passed, and slightly better triage outcomes (see `OUTCOMES.md`), with less feature engineering. See `RESULTS.md`.

```bash
python3 ml/train_seq.py    # needs torch; or run the "Train sequence model" workflow
```

## Evaluation: prediction

`evaluate.py` compares four scorers on identical simulator output: the learned model, the sequence model, the heuristic that reads the target, and the original heuristic. It reports AUC, precision, recall and F1 on the held-out rollouts, runs the 19 scripted scenarios in `scenarios.py`, and checks that the C++ scorer and the Python reference agree on every row. With `--check` it exits non-zero if a floor is missed, which is what CI runs.

The scenario expectations were written from each scenario's intent (who attacks, who never does) and were not changed after seeing any scorer's output. Five of them exercise the 3D rules: a stealth attack run, a high-level bomber run, a drone coming in low behind cover, a jet that descends far away and holds, and a fighter orbiting at long range. Both models fail the same two of the 19.

## Sensing and tracking

`tracking_eval.py` turns sensing noise on and compares a per-contact Kalman tracker with using the best single detection each tick: how often a contact has a track, how far the picture is from the truth, how many tracks are lost, and what that does to the threat model's AUC and to the planner's mission value. `train.py --noise-mix` retrains the same network on tracker output at mixed noise levels and writes `model_noise_mix.json`; it is scored by the Python reference only and does not replace the shipped weights. Results: `TRACKING.md`.

## Evaluation: outcomes

A good score only matters if acting on it helps. `outcome.py` gives blue one defensive resource (the HQ's point defense covers against one contact per tick) and lets each scorer decide where to put it, by score × severity. Every policy plays the same 150 rollouts and is scored on mission value: the average over every tick and blue asset of health × integrity.

The agent is graded the same way. `evals/outcome.eval.ts` seeds eight scripted scenarios into the agent's workspace, asks it to run the defense for 12 ticks, recomputes mission value from its own `run_tick` results, and compares it with no defense and with the learned-model policy. It also fails the run if the agent orders a red unit or resets the scenario. That eval needs a model login and has not been run yet.

## Learned policy (reinforcement learning)

The planner's rules were written by hand. `rl_train.py` asks whether the same behavior can be learned: a small network, trained with PPO, chooses each tick which contact point defense covers and which aircraft the fighter goes after.

- **What it sees.** The planner's observation message and nothing else (`rl_env.py` turns it into 27 numbers per contact and 13 about blue's own side). The network treats the contacts as a set, so their number and order do not matter.
- **What it is rewarded for.** Blue's mission value after each tick, the measure every evaluation here reports. Nothing else: no bonus for kills, no penalty written in for shooting the wrong aircraft.
- **Rules of engagement.** A mask, not a penalty. A shot at a contact below the watch line is not among the choices unless that contact has hit one of blue's aircraft, so the rule holds from the first random step of training to the last. `--roe free` removes the mask to measure what the rule costs.
- **What it trains on.** Fresh games every rollout, 70% scripted rollouts and 30% battles against red's coordinated doctrine, from seeds no evaluation uses. The simulator runs in `edge-sim serve` processes, one per CPU thread.
- **How it is judged.** `rl_eval.py` plays it on the planner evaluation's 150 rollouts and the opponent evaluation's 80 battles, next to the planner, and checks every order it gives against the hold-fire rule from outside. Results: [`RL.md`](RL.md).

```bash
python3 ml/rl_train.py                 # needs torch; about 17 minutes on two CPU cores, less on more
python3 ml/rl_eval.py --weights ml/rl_runs/latest.json --no-write
cp ml/rl_runs/latest.json ml/rl_policy.json && python3 ml/rl_eval.py     # ship it and rewrite RL.md
python3 ml/test_rl.py                  # the environment and the mask, standard library only
```

`rl_policy.py` is the trained network's arithmetic in plain Python, so evaluation and CI need no PyTorch. The trainer refuses to write weights unless that code reproduces PyTorch's numbers.

## Limits

- A contact flying toward a sensor looks the same whether it will attack or pass by. History narrows that, it does not remove it.
- It needs a tick or two of motion before it can say anything about intent.
- A jet that parks next to HQ without firing reads as benign, because in the training data parked jets rarely attack. A human would keep watching it. This is one of the two scripted scenarios the models fail.
- A distant jammer that is about to drive into range is called hostile at tick 0, before it has moved. The label is technically right (it does jam a relay within the horizon), but the model cannot know that yet, so it is over-calling stationary jammers. This is the other failed scenario.
- On aircraft and missiles alone, the heuristic that reads each contact's real target ranks slightly better in 3D (0.942 vs 0.938 AUC). Altitude added behaviors (bombers that never descend, harmless descents) that observable motion separates less cleanly.
- The model was trained on the perfect picture, where track quality only weakens with stealth, range and terrain and a contact is never lost. With sensing noise on, contacts are missed and tracks are dropped, and the model loses accuracy until it is retrained on that picture (see Sensing and tracking).
- One-step lookahead is greedy, so it is a strong reference for outcomes, not a true ceiling. On one scripted scenario the learned policy beats it.
- The outcome policy assumes the contact's class (jet, stealth fighter, bomber, drone, missile, jammer) is known.
- The simulator is small and its hit roll is a deterministic hash, so these numbers describe this sandbox, not the real world.
- The training distribution is the scenario generator. Different mixes of attackers and fly-bys would move the cut-offs.
