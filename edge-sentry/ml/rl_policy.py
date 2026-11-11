"""The learned defense policy, run without PyTorch.

Standard library only. `rl_train.py` trains the network with PyTorch and
writes its weights to `rl_policy.json`; this file is the same arithmetic in
plain Python, so the evaluations (and CI) can run the policy with nothing
installed. The trainer refuses to write weights unless this forward pass
reproduces PyTorch's numbers.

The network reads a set of contacts, so it does not care how many there are
or what order they come in:

    each contact's numbers -> small network -> a vector per contact
    the average and the maximum of those vectors, plus blue's own numbers -> a context vector
    (contact vector, context) -> one score per contact for point defense, one for the fighter
    context -> the score for "cover nothing" and for "go back to station"

The highest score that the rules allow wins.
"""
import json
import math
import os

import rl_env

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "rl_policy.json")


def _linear(layer, x):
    return [sum(w * v for w, v in zip(row, x)) + b for row, b in zip(layer["w"], layer["b"])]


def _tanh(x):
    return [math.tanh(v) for v in x]


def forward(weights, contacts, globals_):
    """Scores for one picture: (point-defense scores, fighter scores), each [none/station, contact 0, ...]."""
    L = weights["layers"]
    hidden = weights["hidden"]
    h = [_tanh(_linear(L["enc2"], _tanh(_linear(L["enc1"], row)))) for row in contacts]
    if h:
        mean = [sum(col) / len(h) for col in zip(*h)]
        top = [max(col) for col in zip(*h)]
    else:
        mean, top = [0.0] * hidden, [0.0] * hidden
    ctx = _tanh(_linear(L["ctx"], list(globals_) + mean + top))
    pd = [_linear(L["pd_none"], ctx)[0]] + [_linear(L["pd2"], _tanh(_linear(L["pd1"], hi + ctx)))[0] for hi in h]
    ft = [_linear(L["ft_none"], ctx)[0]] + [_linear(L["ft2"], _tanh(_linear(L["ft1"], hi + ctx)))[0] for hi in h]
    return pd, ft


def best(scores, mask):
    """The highest-scoring choice among those the mask allows."""
    return max((i for i, ok in enumerate(mask) if ok), key=lambda i: scores[i])


def load(path=DEFAULT_PATH):
    with open(path) as f:
        weights = json.load(f)
    if weights["contact_inputs"] != rl_env.N_CONTACT or weights["global_inputs"] != rl_env.N_GLOBAL:
        raise SystemExit(f"{path} was trained on a different set of inputs. Retrain with ml/rl_train.py.")
    return weights


def choose(weights, obs):
    """The policy's two choices for one observation, and the picture they were made on."""
    feat = rl_env.featurize(obs, roe=weights.get("roe", True))
    pd, ft = forward(weights, feat["contacts"], feat["globals"])
    return feat, best(pd, feat["pd_mask"]), best(ft, feat["ft_mask"])


def actor(weights):
    """A policy in the shape the evaluations use: act(sim, world) gives blue's orders for this tick.

    It asks the simulator for the observation message and reads nothing else, exactly like the planner.
    """
    def act(sim, world):
        obs = sim.run("observe", world=False)["observation"]
        feat, pd_choice, ft_choice = choose(weights, obs)
        for line in rl_env.order_lines(feat, pd_choice, ft_choice):
            sim.run(*line, world=False)
    return act
