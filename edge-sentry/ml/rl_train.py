#!/usr/bin/env python3
"""Train a defense policy by reinforcement learning (PPO), to compare with the planner.

    python3 ml/rl_train.py                      # a full run; uses the GPU if PyTorch can see one
    python3 ml/rl_train.py --steps 300000       # shorter
    python3 ml/rl_train.py --roe free           # without the hold-fire mask, to see what the rule costs
    python3 ml/rl_train.py --smoke              # a one-minute check that everything runs (CI)

Needs PyTorch. The policy plays blue's point defense and fighter from the same
observation message the planner gets (see ml/rl_env.py). Every few updates it
is tested, choosing greedily, on rollouts and battles it never trains on, next
to the planner and to no defense on those same games. The best version so far
is written to ml/rl_runs/latest.json, in a form ml/rl_policy.py can run
without PyTorch. `python3 ml/rl_eval.py --weights ml/rl_runs/latest.json
--no-write` then gives the full comparison with the planner. The policy the
repository ships, ml/rl_policy.json, is replaced only when you copy a run
over it.
"""
import argparse
import json
import math
import os
import tempfile
import time

import torch
import torch.nn as nn

import rl_env
import rl_policy
import simlab

p = argparse.ArgumentParser()
p.add_argument("--steps", type=int, default=2_000_000, help="simulator ticks to train on")
p.add_argument("--envs", type=int, default=0, help="simulators run at once (default: one per CPU thread, at most 64)")
p.add_argument("--hidden", type=int, default=32, help="width of every layer")
p.add_argument("--lr", type=float, default=3e-4)
p.add_argument("--rounds", type=int, default=4, help="rollouts per simulator between updates")
p.add_argument("--epochs", type=int, default=4)
p.add_argument("--minibatch", type=int, default=1024)
p.add_argument("--gamma", type=float, default=0.99)
p.add_argument("--lam", type=float, default=0.95)
p.add_argument("--clip", type=float, default=0.2)
p.add_argument("--entropy", type=float, default=0.02, help="exploration bonus at the start; it fades to a tenth of this")
p.add_argument("--battle-share", type=float, default=0.3, help="share of training rollouts flown against red's doctrine")
p.add_argument("--roe", choices=["mask", "free"], default="mask", help="mask: hold-fire is enforced; free: it is not")
p.add_argument("--device", default="auto", help="auto, cpu or cuda")
p.add_argument("--seed", type=int, default=0)
p.add_argument("--test-every", type=int, default=10, help="updates between tests on held-out games")
p.add_argument("--out", default=os.path.join(simlab.HERE, "rl_runs", "latest.json"),
               help="where the best weights go. The shipped policy, ml/rl_policy.json, is only replaced on purpose")
p.add_argument("--smoke", action="store_true", help="tiny run that checks the whole pipeline and writes nothing to ml/")
args = p.parse_args()

if args.smoke:
    args.steps, args.envs, args.rounds, args.test_every, args.minibatch = 6000, 4, 2, 2, 256
    args.out = os.path.join(tempfile.mkdtemp(prefix="rl-smoke-"), "rl_policy.json")
os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
torch.manual_seed(args.seed)
device = torch.device("cuda" if (args.device == "auto" and torch.cuda.is_available()) else ("cpu" if args.device == "auto" else args.device))
n_envs = args.envs or min(64, os.cpu_count() or 4)
roe = args.roe == "mask"
K, F, G, H = rl_env.MAX_CONTACTS, rl_env.N_CONTACT, rl_env.N_GLOBAL, args.hidden

# Held-out games the policy is tested on while it trains. Training draws seeds from 2,000,000 up;
# the final evaluation (ml/rl_eval.py) uses the planner evaluation's own seeds, which nothing here touches.
TEST_GAMES = [(1_900_000 + i, False) for i in range(8 if args.smoke else 48)] + [(1_950_000 + i, True) for i in range(4 if args.smoke else 24)]


class Policy(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc1, self.enc2 = nn.Linear(F, H), nn.Linear(H, H)
        self.ctx = nn.Linear(G + 2 * H, H)
        self.pd1, self.pd2, self.pd_none = nn.Linear(2 * H, H), nn.Linear(H, 1), nn.Linear(H, 1)
        self.ft1, self.ft2, self.ft_none = nn.Linear(2 * H, H), nn.Linear(H, 1), nn.Linear(H, 1)
        self.v1, self.v2 = nn.Linear(H, H), nn.Linear(H, 1)
        for layer in (self.pd2, self.ft2, self.pd_none, self.ft_none):  # start close to "no strong opinion"
            nn.init.orthogonal_(layer.weight, 0.01)
            nn.init.zeros_(layer.bias)

    def forward(self, contacts, present, globals_):
        """contacts [B,K,F], present [B,K] (1 where a row is a real contact), globals_ [B,G]."""
        h = torch.tanh(self.enc2(torch.tanh(self.enc1(contacts)))) * present.unsqueeze(-1)
        count = present.sum(1, keepdim=True)
        mean = h.sum(1) / count.clamp(min=1)
        top = (h - (1 - present.unsqueeze(-1)) * 1e9).max(1).values * (count > 0)
        ctx = torch.tanh(self.ctx(torch.cat([globals_, mean, top], 1)))
        pair = torch.cat([h, ctx.unsqueeze(1).expand(-1, h.shape[1], -1)], 2)
        pd = torch.cat([self.pd_none(ctx), self.pd2(torch.tanh(self.pd1(pair))).squeeze(-1)], 1)
        ft = torch.cat([self.ft_none(ctx), self.ft2(torch.tanh(self.ft1(pair))).squeeze(-1)], 1)
        value = self.v2(torch.tanh(self.v1(ctx))).squeeze(-1)
        return pd, ft, value


def batch(feats, dtype=torch.float32):
    """Pictures from the environment -> padded tensors."""
    n = len(feats)
    contacts = torch.zeros(n, K, F, dtype=dtype)
    present = torch.zeros(n, K, dtype=dtype)
    globals_ = torch.zeros(n, G, dtype=dtype)
    pd_mask = torch.zeros(n, K + 1, dtype=torch.bool)
    ft_mask = torch.zeros(n, K + 1, dtype=torch.bool)
    for i, f in enumerate(feats):
        k = len(f["contacts"])
        if k:
            contacts[i, :k] = torch.tensor(f["contacts"], dtype=dtype)
            present[i, :k] = 1
        globals_[i] = torch.tensor(f["globals"], dtype=dtype)
        pd_mask[i, :k + 1] = torch.tensor(f["pd_mask"])
        ft_mask[i, :k + 1] = torch.tensor(f["ft_mask"])
    return contacts, present, globals_, pd_mask, ft_mask


def masked(logits, mask):
    return torch.distributions.Categorical(logits=logits.masked_fill(~mask, -1e9))


def export(net, path, note):
    """Write the weights for ml/rl_policy.py, but only if that plain-Python forward pass agrees with PyTorch."""
    state = {k: v.detach().cpu().double() for k, v in net.state_dict().items()}
    layers = {name: {"w": state[name + ".weight"].tolist(), "b": state[name + ".bias"].tolist()}
              for name in ("enc1", "enc2", "ctx", "pd1", "pd2", "pd_none", "ft1", "ft2", "ft_none")}
    weights = {"contact_inputs": F, "global_inputs": G, "hidden": H, "roe": roe, "note": note, "layers": layers}
    probe = rl_env.VecEnv(1, fixed=[TEST_GAMES[0], TEST_GAMES[-1]], roe=roe)
    worst = 0.0
    try:
        feats = probe.reset()
        for _ in range(2 * rl_env.TICKS):
            # In double precision on both sides, so any difference is a real disagreement and not rounding.
            contacts, present, globals_, pd_mask, ft_mask = batch(feats, torch.float64)
            with torch.no_grad():
                pd, ft, _ = net.cpu().double()(contacts, present, globals_)
            mine_pd, mine_ft = rl_policy.forward(weights, feats[0]["contacts"], feats[0]["globals"])
            k = len(feats[0]["contacts"]) + 1
            worst = max([worst] + [abs(a - b) for a, b in zip(mine_pd, pd[0, :k].tolist())] + [abs(a - b) for a, b in zip(mine_ft, ft[0, :k].tolist())])
            feats, _, _ = probe.step([rl_policy.best(mine_pd, feats[0]["pd_mask"])], [rl_policy.best(mine_ft, feats[0]["ft_mask"])])
    finally:
        probe.close()
        net.float().to(device)
    if worst > 1e-9:
        raise SystemExit(f"the plain-Python policy disagrees with PyTorch by {worst:.2e}; nothing was written")
    with open(path, "w") as f:
        json.dump(weights, f, separators=(",", ":"))
    return worst


def play_fixed(choose):
    """Mission value on the held-out games, split into (rollouts, battles). choose(feats) -> (pd, ft) lists."""
    env = rl_env.VecEnv(len(TEST_GAMES), fixed=TEST_GAMES, roe=roe)
    try:
        feats = env.reset()
        totals = [0.0] * len(TEST_GAMES)
        for _ in range(rl_env.TICKS):
            pd, ft = choose(feats)
            feats, rewards, _ = env.step(pd, ft)
            totals = [a + r for a, r in zip(totals, rewards)]
    finally:
        env.close()
    values = [1 + t / rl_env.TICKS for t in totals]
    split = sum(1 for _, battle in TEST_GAMES if not battle)
    return sum(values[:split]) / split, sum(values[split:]) / (len(values) - split)


def greedy(net):
    def choose(feats):
        contacts, present, globals_, pd_mask, ft_mask = (t.to(device) for t in batch(feats))
        with torch.no_grad():
            pd, ft, _ = net(contacts, present, globals_)
        return (pd.masked_fill(~pd_mask, -1e9).argmax(1).tolist(), ft.masked_fill(~ft_mask, -1e9).argmax(1).tolist())
    return choose


def planner_reference():
    """The planner and no defense on the same held-out games, for the log."""
    out = {}
    for name, command in (("no defense", None), ("planner", ["autopilot"])):
        sim = simlab.Sim(fast=True)
        values = []
        for seed, battle in TEST_GAMES:
            units, red = rl_env.scenario(seed, battle)
            sim.load(units)
            total = 0.0
            for t in range(rl_env.TICKS):
                for line in red(t):
                    sim.run(*line, world=False)
                if command:
                    sim.run(*command, world=False)
                sim.run("tick", world=False)
                total += sim.run("observe", world=False)["blueValue"]
            values.append(total / rl_env.TICKS)
        sim.close()
        split = sum(1 for _, battle in TEST_GAMES if not battle)
        out[name] = (sum(values[:split]) / split, sum(values[split:]) / (len(values) - split))
    return out


net = Policy().to(device)
opt = torch.optim.Adam(net.parameters(), lr=args.lr, eps=1e-5)
env = rl_env.VecEnv(n_envs, seed=args.seed, battle_share=args.battle_share, roe=roe)
per_update = n_envs * rl_env.TICKS * args.rounds
updates = max(1, math.ceil(args.steps / per_update))
print(f"device {device}" + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else "")
      + f", {n_envs} simulators, {per_update} ticks per update, {updates} updates, hold-fire {'enforced' if roe else 'off'}")
ref = planner_reference()
print("held-out games (rollouts, battles):  " + "   ".join(f"{k} {a:.3f}, {b:.3f}" for k, (a, b) in ref.items()))

best_score, best_at, started = -1.0, 0, time.time()
feats = env.reset()
for update in range(1, updates + 1):
    progress = (update - 1) / updates
    entropy_coef = args.entropy * (1.0 - 0.9 * progress)
    for group in opt.param_groups:
        group["lr"] = args.lr * (1.0 - 0.9 * progress)
    # --- collect ---------------------------------------------------------------
    store = {k: [] for k in ("contacts", "present", "globals", "pd_mask", "ft_mask", "pd", "ft", "logp", "value", "reward")}
    finished = []
    running = [0.0] * n_envs
    for _ in range(rl_env.TICKS * args.rounds):
        contacts, present, globals_, pd_mask, ft_mask = (t.to(device) for t in batch(feats))
        with torch.no_grad():
            pd_logits, ft_logits, value = net(contacts, present, globals_)
            pd_dist, ft_dist = masked(pd_logits, pd_mask), masked(ft_logits, ft_mask)
            pd, ft = pd_dist.sample(), ft_dist.sample()
            logp = pd_dist.log_prob(pd) + ft_dist.log_prob(ft)
        feats, rewards, done = env.step(pd.tolist(), ft.tolist())
        for key, val in (("contacts", contacts), ("present", present), ("globals", globals_), ("pd_mask", pd_mask),
                         ("ft_mask", ft_mask), ("pd", pd), ("ft", ft), ("logp", logp), ("value", value),
                         ("reward", torch.tensor(rewards, device=device, dtype=torch.float32))):
            store[key].append(val)
        running = [a + r for a, r in zip(running, rewards)]
        if done:
            finished += [1 + r / rl_env.TICKS for r in running]
            running = [0.0] * n_envs
    # --- advantages: every rollout is exactly TICKS long and ends there ---------
    T = len(store["reward"])
    rewards_t, values_t = torch.stack(store["reward"]), torch.stack(store["value"])
    adv = torch.zeros_like(rewards_t)
    last = torch.zeros(n_envs, device=device)
    for t in reversed(range(T)):
        ends = (t + 1) % rl_env.TICKS == 0
        next_value = torch.zeros(n_envs, device=device) if ends else values_t[t + 1]
        delta = rewards_t[t] + args.gamma * next_value - values_t[t]
        last = delta + (0.0 if ends else args.gamma * args.lam) * last
        adv[t] = last
    returns = adv + values_t
    flat = {k: torch.cat(v) if v[0].dim() > 0 and k not in ("reward",) else torch.stack(v).reshape(-1) for k, v in store.items()}
    flat_adv, flat_ret = adv.reshape(-1), returns.reshape(-1)
    flat_adv = (flat_adv - flat_adv.mean()) / (flat_adv.std() + 1e-8)
    # --- update ----------------------------------------------------------------
    size = flat_adv.shape[0]
    for _ in range(args.epochs):
        order = torch.randperm(size, device=device)
        for start in range(0, size, args.minibatch):
            idx = order[start:start + args.minibatch]
            pd_logits, ft_logits, value = net(flat["contacts"][idx], flat["present"][idx], flat["globals"][idx])
            pd_dist, ft_dist = masked(pd_logits, flat["pd_mask"][idx]), masked(ft_logits, flat["ft_mask"][idx])
            logp = pd_dist.log_prob(flat["pd"][idx]) + ft_dist.log_prob(flat["ft"][idx])
            ratio = torch.exp(logp - flat["logp"][idx])
            a = flat_adv[idx]
            policy_loss = -torch.min(ratio * a, torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * a).mean()
            value_loss = (value - flat_ret[idx]).pow(2).mean()
            entropy = (pd_dist.entropy() + ft_dist.entropy()).mean()
            loss = policy_loss + 0.5 * value_loss - entropy_coef * entropy
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), 0.5)
            opt.step()
    if not math.isfinite(loss.item()):
        raise SystemExit("the loss is not a number: training has diverged")
    # --- report, and test on held-out games ----------------------------------------
    ticks = update * per_update
    line = (f"update {update:4d}/{updates}  {ticks:9,d} ticks  {ticks / (time.time() - started):6.0f}/s  "
            f"training games {sum(finished) / len(finished):.3f}  entropy {entropy.item():.2f}")
    if update % args.test_every == 0 or update == updates:
        rollouts, battles = play_fixed(greedy(net))
        score = 0.7 * rollouts + 0.3 * battles
        line += f"  | held-out: rollouts {rollouts:.3f} (planner {ref['planner'][0]:.3f}), battles {battles:.3f} (planner {ref['planner'][1]:.3f})"
        if score > best_score:
            best_score, best_at = score, update
            worst = export(net, args.out, {"ticks": ticks, "rollouts": round(rollouts, 4), "battles": round(battles, 4),
                                           "planner": [round(x, 4) for x in ref["planner"]], "device": str(device),
                                           "hidden": H, "battle_share": args.battle_share})
            line += f"  saved (plain-Python check: {worst:.1e})"
    print(line, flush=True)
env.close()
print(f"\nbest held-out result at update {best_at}; weights in {args.out}")
print(f"compare it with the planner:   python3 ml/rl_eval.py --weights {os.path.relpath(args.out)} --no-write")
print(f"make it the shipped policy:    cp {os.path.relpath(args.out)} ml/rl_policy.json && python3 ml/rl_eval.py")
if args.smoke:
    weights = rl_policy.load(args.out)
    assert set(weights["layers"]) == {"enc1", "enc2", "ctx", "pd1", "pd2", "pd_none", "ft1", "ft2", "ft_none"}
    print("PASS  smoke run: trained, tested on held-out games, exported, and the plain-Python policy matches PyTorch")
