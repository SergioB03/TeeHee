#!/usr/bin/env python3
"""Train the sequence (GRU) threat model with PyTorch and export it to C++.

    npm run build:native
    python3 ml/train_seq.py        # needs torch (CPU build is enough)
    npm run build:native           # rebuild so edge-sim uses the new weights
    python3 ml/evaluate.py         # compares it with the default model

Same task, data and splits as ml/train.py: rollout seeds 0..N-1, the last
fifth held back for validation. Validation is used for early stopping and for
the two label cut-offs (hostile = best F1, watch = 95% recall). The held-out
seeds in ml/evaluate.py are never seen here.

The model gets only the inputs that describe the current tick, one tick at
a time, and has to build its own memory of the track. See ml/seqlab.py.
"""
import argparse
import time

import torch
from torch import nn

import seqlab
import simlab

p = argparse.ArgumentParser()
p.add_argument("--scenarios", type=int, default=600, help="rollouts (seeds 0..N-1); the last fifth is validation")
p.add_argument("--hidden", type=int, default=16, help="GRU hidden units")
p.add_argument("--seed", type=int, default=0, help="weight initialization and batch order seed")
p.add_argument("--epochs", type=int, default=400, help="maximum epochs")
p.add_argument("--patience", type=int, default=40, help="stop after this many epochs without a better validation loss")
args = p.parse_args()

# Double precision: the model is tiny, and it makes the exported weights
# reproduce the training-time scores exactly in C++ and in the Python reference.
torch.set_default_dtype(torch.float64)
torch.manual_seed(args.seed)
torch.set_num_threads(1)

t0 = time.time()
all_rows = simlab.dataset(range(args.scenarios))
first_val_seed = args.scenarios - args.scenarios // 5
train = seqlab.sequences([r for r in all_rows if r["seed"] < first_val_seed])
val = seqlab.sequences([r for r in all_rows if r["seed"] >= first_val_seed])
n_train = sum(len(s["y"]) for s in train)
n_val = sum(len(s["y"]) for s in val)
positive = sum(sum(s["y"]) for s in train) / n_train
print(f"{len(all_rows)} rows from {args.scenarios} rollouts in {time.time() - t0:.0f}s: "
      f"{len(train)} train sequences ({n_train} rows, {positive:.1%} positive), "
      f"{len(val)} validation sequences ({n_val} rows)")


def pack(seqs):
    """Pad to the longest sequence. Returns inputs, labels and a mask of real steps."""
    longest = max(len(s["y"]) for s in seqs)
    x = torch.zeros(len(seqs), longest, seqlab.SEQ_INPUTS)
    y = torch.zeros(len(seqs), longest)
    mask = torch.zeros(len(seqs), longest)
    for i, s in enumerate(seqs):
        n = len(s["y"])
        x[i, :n] = torch.tensor(s["x"])
        y[i, :n] = torch.tensor(s["y"], dtype=torch.get_default_dtype())
        mask[i, :n] = 1.0
    return x, y, mask


class SequenceScorer(nn.Module):
    def __init__(self, inputs, hidden):
        super().__init__()
        self.gru = nn.GRU(inputs, hidden, batch_first=True)
        self.out = nn.Linear(hidden, 1)

    def forward(self, x):
        states, _ = self.gru(x)
        return self.out(states).squeeze(-1)  # one logit per tick


def masked_loss(logits, y, mask):
    per_step = nn.functional.binary_cross_entropy_with_logits(logits, y, reduction="none")
    return (per_step * mask).sum() / mask.sum()


xt, yt, mt = pack(train)
xv, yv, mv = pack(val)
net = SequenceScorer(seqlab.SEQ_INPUTS, args.hidden)
opt = torch.optim.Adam(net.parameters(), lr=3e-3, weight_decay=1e-5)
batch = 64
best = (float("inf"), None, 0)
for epoch in range(1, args.epochs + 1):
    net.train()
    order = torch.randperm(len(train))
    for i in range(0, len(train), batch):
        idx = order[i:i + batch]
        opt.zero_grad()
        loss = masked_loss(net(xt[idx]), yt[idx], mt[idx])
        loss.backward()
        nn.utils.clip_grad_norm_(net.parameters(), 1.0)
        opt.step()
    net.eval()
    with torch.no_grad():
        val_loss = masked_loss(net(xv), yv, mv).item()
    if val_loss < best[0] - 1e-5:
        best = (val_loss, {k: v.clone() for k, v in net.state_dict().items()}, epoch)
    if epoch % 20 == 0:
        print(f"epoch {epoch}: validation loss {val_loss:.4f} (best {best[0]:.4f} at epoch {best[2]})")
    if epoch - best[2] >= args.patience:
        break
net.load_state_dict(best[1])
net.eval()
print(f"stopped after {epoch} epochs; kept epoch {best[2]} with validation loss {best[0]:.4f}")

model = {
    "inputs": seqlab.INPUT_NAMES,
    "hidden": args.hidden,
    "weight_ih": net.gru.weight_ih_l0.tolist(),  # [3 * hidden][inputs], gates r, z, n
    "weight_hh": net.gru.weight_hh_l0.tolist(),  # [3 * hidden][hidden]
    "bias_ih": net.gru.bias_ih_l0.tolist(),
    "bias_hh": net.gru.bias_hh_l0.tolist(),
    "out_weights": net.out.weight[0].tolist(),
    "out_bias": float(net.out.bias[0]),
    "horizon": simlab.HORIZON,
}

# Score validation with the exported weights and the plain-Python forward pass,
# and make sure that matches PyTorch before trusting the export.
val_scores, val_labels, worst = [], [], 0.0
with torch.no_grad():
    torch_scores = torch.sigmoid(net(xv))
for i, s in enumerate(val):
    ref = seqlab.scores(model, s["x"])
    worst = max(worst, max(abs(a - float(b)) for a, b in zip(ref, torch_scores[i, :len(ref)])))
    val_scores += ref
    val_labels += s["y"]
if worst > 1e-6:
    raise SystemExit(f"exported weights disagree with PyTorch by {worst:.2e}; not writing the model")

grid = [i / 100 for i in range(5, 96)]
hostile_at = max(grid, key=lambda t: simlab.prf(val_scores, val_labels, t)[2])
catching = [t for t in grid if t < hostile_at and simlab.prf(val_scores, val_labels, t)[1] >= 0.95]
watch_at = max(catching) if catching else min(grid)
vp, vr, vf = simlab.prf(val_scores, val_labels, hostile_at)
print(f"validation AUC {simlab.auc(val_scores, val_labels):.4f}; hostile at {hostile_at:.2f} "
      f"(precision {vp:.2f}, recall {vr:.2f}, F1 {vf:.2f}); watch at {watch_at:.2f} "
      f"(recall {simlab.prf(val_scores, val_labels, watch_at)[1]:.2f}); "
      f"export matches PyTorch to {worst:.1e}")

model.update({
    "watch_at": watch_at,
    "hostile_at": hostile_at,
    "training": {"scenarios": args.scenarios, "sequences": len(train), "rows": n_train,
                 "validation_rows": n_val, "positive_rate": positive, "hidden": args.hidden,
                 "seed": args.seed, "epochs": best[2], "validation_loss": best[0],
                 "torch": torch.__version__.split("+")[0]},
})
seqlab.write(model, f"Trained with PyTorch on {n_train} rows in {len(train)} sequences; "
                    f"label cut-offs chosen on {n_val} validation rows.")
print("wrote native/src/sequence_weights.hpp and ml/sequence.json. Rebuild edge-sim to use them.")
