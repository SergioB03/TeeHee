# Threat model

A logistic regression that replaces the hand-weighted threat score. This page says what it predicts, what it is allowed to see, how it was trained, and where it falls short. Results are in [`RESULTS.md`](RESULTS.md).

## The task

For every red contact on every tick: **will this contact harm a blue asset within the next 5 ticks?** Harm means it damages a blue unit, or it is a jammer holding a blue sensor, relay or HQ inside its field.

Labels come from the simulator's future, not from a rule. Each rollout is run to the end, then every contact-tick is labelled by what that contact went on to do.

## What the model may see

Twelve inputs, all observable from the blue side and all scaled to 0..1:

| Input | Meaning |
| --- | --- |
| `speed` | Displacement over the last tick |
| `snr` | Best blue sensor signal quality on the contact |
| `emitter` | The contact is radiating as a jammer |
| `proximity` | Closeness to the nearest blue asset |
| `airborne` | Altitude prior for the contact class |
| `closing` | Fastest range rate toward any blue asset |
| `heading` | How directly its motion points at a blue asset |
| `aimRange` | Closeness of the asset it is pointed at |
| `blueInField` | Strongest jam pressure it puts on a blue asset |
| `ewCover` | Red jam field strength at the contact |
| `recentFire` | It damaged a blue asset in the last 3 ticks |
| `intercept` | Time until it reaches a blue asset at its current range rate |

Plus two interaction terms, `closing × proximity` and `heading × aimRange`.

The model never reads a contact's orders or target. The earlier heuristic's `closingOnBlue` input does read the target assignment, which is why it can call a missile hostile at tick 0 and why it is reported separately as a privileged baseline.

## Data

`simlab.random_scenario(seed)` builds a random blue layout and two to six red contacts: attackers, contacts in transit (half of them routed close to a blue asset without attacking), loiterers, jammers, and harmless ground units. About a third change behavior mid-run: attackers stand down, idle contacts turn and attack. Each rollout runs 22 ticks through the real `edge-sim` binary.

- Training: seeds 0 to 479 (32,206 contact-ticks, 35% positive)
- Validation: seeds 480 to 599, used only to choose the two label cut-offs
- Held-out test: seeds 100000 to 100199, never touched by training

Splits are by rollout, so no scenario contributes rows to more than one split.

## Training

`train.py` fits the model with Newton's method (IRLS) and a ridge penalty of 1.0, then picks the label cut-offs on the validation split by fixed rules:

- **hostile** at the score that maximizes F1 (0.45)
- **watch** at the highest score that still catches 95% of harmful contacts (0.08)

It writes `native/src/model_weights.hpp`, which is compiled into `edge-sim`, and `model.json` for the Python reference.

```bash
npm run build:native
python3 ml/train.py        # needs numpy
npm run build:native       # picks up the new weights
python3 ml/evaluate.py     # standard library only
```

## Evaluation

`evaluate.py` compares three scorers on identical simulator output: the learned model, the heuristic that reads the target, and the original heuristic. It reports AUC, precision, recall and F1 on the held-out rollouts, runs the 14 scripted scenarios in `scenarios.py`, and checks that the C++ scorer and the Python reference agree on every row. With `--check` it exits non-zero if a floor is missed, which is what CI runs.

The scenario expectations were written from each scenario's intent (who attacks, who never does) before looking at any scorer's output.

## Limits

- It is a linear model on single-tick features. A contact flying toward a sensor looks the same whether it will attack or pass by, so early calls on air contacts are uncertain by construction. On jets and missiles alone, the privileged heuristic still ranks better (AUC 0.939 against 0.918).
- It needs one tick of motion before it can say anything about intent.
- A jet that parks next to HQ without firing reads as low threat, because in the training data parked jets rarely attack. A human would keep watching it.
- The simulator is small and its hit roll is a deterministic hash, so these numbers describe this sandbox, not the real world.
- The training distribution is my scenario generator. Different mixes of attackers and fly-bys would move the cut-offs.
