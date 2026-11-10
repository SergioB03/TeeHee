# Threat model

A small neural network that replaces the hand-weighted threat score. This page says what it predicts, what it is allowed to see, how it was trained, how it is judged, and where it falls short. Numbers are in [`RESULTS.md`](RESULTS.md) (prediction) and [`OUTCOMES.md`](OUTCOMES.md) (decisions).

## The task

For every red contact on every tick: **will this contact harm a blue asset within the next 5 ticks?** Harm means it damages a blue unit, or it is a jammer holding a blue sensor, relay or HQ inside its field.

Labels come from the simulator's future, not from a rule. Each rollout is run to the end, then every contact-tick is labelled by what that contact went on to do.

## What the model may see

24 inputs, all observable from the blue side and all scaled to 0..1. `edge-sim inputs` prints the list, and the simulator computes every one of them, so the Python side never re-derives a feature.

| Group | Inputs |
| --- | --- |
| Motion this tick | `speed`, `closing` (range rate toward any blue asset), `heading` (how directly it points at one), `aimRange`, `intercept` (time to reach an asset), `miss` (how close its line of motion passes), `deadOn`, `stopped` |
| Position | `proximity`, `inRange` (within 1.5 cells), `onAsset` |
| Track history | `stopTicks`, `movedBefore`, `rangeDrop3` (closer than three ticks ago), `closing1`, `heading1`, `closing2`, `heading2` |
| Emissions and effects | `emitter`, `blueInField`, `ewCover`, `recentFire`, `snr`, `airborne` |

The model never reads a contact's orders or target. The earlier heuristic's `closingOnBlue` input does read the target assignment, which is why it is reported separately as a privileged baseline.

Track history is what closed the gap on jets and missiles. With single-tick inputs a linear model scored AUC 0.918 on air contacts, behind the target-reading heuristic at 0.939. Simple streak counters did not help. Remembering whether a contact has moved, how long it has been stopped, and what it was doing one and two ticks ago took it to 0.949: a jet that flew in and stopped beside a sensor is a different thing from one that has sat there all along.

## Data

`simlab.random_scenario(seed)` builds a random blue layout and two to six red contacts: attackers, contacts in transit (half of them routed close to a blue asset without attacking), loiterers, jammers, and harmless ground units. About a third change behavior mid-run: attackers stand down, idle contacts turn and attack. Each rollout runs 22 ticks through the real `edge-sim` binary.

- Training: seeds 0 to 479 (32,206 contact-ticks, 35% positive)
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

## Evaluation: prediction

`evaluate.py` compares three scorers on identical simulator output: the learned model, the heuristic that reads the target, and the original heuristic. It reports AUC, precision, recall and F1 on the held-out rollouts, runs the 14 scripted scenarios in `scenarios.py`, and checks that the C++ scorer and the Python reference agree on every row. With `--check` it exits non-zero if a floor is missed, which is what CI runs.

The scenario expectations were written from each scenario's intent (who attacks, who never does) and were not changed after seeing any scorer's output. The model fails one of them.

## Evaluation: outcomes

A good score only matters if acting on it helps. `outcome.py` gives blue one defensive resource (the HQ's point defense covers against one contact per tick) and lets each scorer decide where to put it, by score × severity. Every policy plays the same 150 rollouts and is scored on mission value: the average over every tick and blue asset of health × integrity.

The agent is graded the same way. `evals/outcome.eval.ts` seeds five scripted scenarios into the agent's workspace, asks it to run the defense for 12 ticks, recomputes mission value from its own `run_tick` results, and compares it with no defense and with the learned-model policy. It also fails the run if the agent orders a red unit or resets the scenario. That eval needs a model login and has not been run yet.

## Limits

- A contact flying toward a sensor looks the same whether it will attack or pass by. History narrows that, it does not remove it.
- It needs a tick or two of motion before it can say anything about intent.
- A jet that parks next to HQ without firing reads as benign, because in the training data parked jets rarely attack. A human would keep watching it. This is the scripted scenario the model fails.
- One-step lookahead is greedy, so it is a strong reference for outcomes, not a true ceiling. On one scripted scenario the learned policy beats it.
- The outcome policy assumes the contact's class (jet, missile, jammer) is known.
- The simulator is small and its hit roll is a deterministic hash, so these numbers describe this sandbox, not the real world.
- The training distribution is the scenario generator. Different mixes of attackers and fly-bys would move the cut-offs.
