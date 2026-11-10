# edge-sentry review findings

Review of commit `8f5f284` on `cursor/edge-sentry-0858` ("Add edge-sentry terrain EW sandbox with C++ sim and AIML scoring"), tested 2026-11-10.

This file is for whoever picks the branch up next (Cursor agent or human). It lists what was tested, what broke, what this branch (`claude/edge-sentry-fixes`) already fixes, and what is still open.

## Summary

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | `cease` does not stop an attack | High | Fixed here |
| 2 | Event log is wiped on every CLI call | High | Fixed here |
| 3 | Bad numeric arguments abort the process | Medium | Fixed here |
| 4 | Invalid orders are accepted silently | Medium | Fixed here |
| 5 | `bot/lib/sim.ts` throws on every rejected command | High | Fixed here (run-tested, not type-checked) |
| 6 | Threat scorer almost never says `hostile` | Design | Fixed on `cursor/edge-sentry-0858` |
| 7 | Default scenario never exercises jamming | Design | Fixed on `cursor/edge-sentry-0858` |
| 8 | Smaller notes (see below) | Low | Open |

## What was tested

- `npm run build:native` with g++ 13.3: builds cleanly.
- Strict build (`-Wall -Wextra -Wpedantic`) plus AddressSanitizer and UBSan: no warnings, no runtime reports.
- Every CLI command (`reset`, `status`, `score`, `tick`, `spawn`, `order`) with valid and invalid input.
- Determinism: the same command sequence produces an identical world, and `tick --n 20` matches twenty single ticks.
- `bot/lib/sim.ts` executed directly under Node 22 with type stripping (it only imports `node:` builtins).

## What was not tested

- `npm install`, `npm run check` (tsc), `agent-sdk validate`, `agent-sdk tool call`, and `agent-sdk eval`. The npm install could not run in the review sandbox, and the eval also needs a Grok login. **Please run `npm run check` and `agent-sdk validate --dir .` on this branch before merging**, since the `sim.ts` change has been executed but not type-checked.
- The four tool files in `bot/tools/` were read, not run.
- The `grok-4.7` model id in `bot/agent.ts` was not verified.

## Findings fixed on this branch

### 1. `cease` does not stop an attack

`tickOnce()` engages whenever `u.targetId` is non-empty, but `issueOrder(..., Cease)` only reset `u.order`.

```bash
edge-sim --world w.json reset
edge-sim --world w.json order --id R-JET1 --action launch --target B-R1
edge-sim --world w.json tick --n 6
edge-sim --world w.json order --id R-JET1 --action cease   # B-R1 at 85 hp
edge-sim --world w.json tick --n 20                        # before: B-R1 destroyed
```

Fix: `cease` also clears `targetId` (`native/src/world.cpp`).

### 2. Event log is wiped on every CLI call

`World::fromJson` never read `events` back, so each process started from the single `scenario` line. `engage` / `destroyed` / `expended` events were only visible in the output of the exact `tick` call that produced them. `map_status` runs a separate `status` call, so the agent would almost never see them.

Fix: `fromJson` restores `events`. It also now reads the nested `order` object properly, so `order.targetId` survives a reload (it was being dropped).

### 3. Bad numeric arguments abort the process

`std::stoi` was called unguarded. These all died with an uncaught exception (SIGABRT, no JSON on stdout):

```bash
edge-sim --world w.json spawn --type jet --team red            # missing --x/--y
edge-sim --world w.json spawn --type jet --team red --x abc --y 1
edge-sim --world w.json tick --n abc
edge-sim --world w.json tick --n 99999999999
```

Fix: a `parseInt` helper in `native/src/main.cpp`; these now print `{"ok":false,"error":...}` and exit 1. `tick --n` must be an integer from 1 to 1000. Behavior change: `--n 0` and negative values used to be treated as 1 and are now rejected.

### 4. Invalid orders are accepted silently

These returned `ok: true` and then did nothing useful:

- `order --action launch --target GHOST` (target does not exist)
- `order --action launch` with no target at all
- `order --action move` with no `--tx/--ty`
- `order --action move --tx 50 --ty 50` (unit walked to the map edge and kept the order forever)

Fix: `issueOrder` rejects a move outside the map and a launch whose target is missing, destroyed, or the unit itself. Rejected orders no longer rewrite the world file or rescore tracks.

### 5. `bot/lib/sim.ts` throws on every rejected command

`edge-sim` exits 1 when it rejects a command, and Node's promisified `execFile` rejects on any non-zero exit. So `runSim` threw `Error: Command failed: ...` and the tools' `if (!result.ok) return { ok: false, error }` branches were unreachable. The agent would get an opaque failure instead of, for example, `only jammers can jam`.

Fix: `runSim` catches the rejection and parses the captured stdout. Verified by running the original and the fixed file under Node: the original throws, the fixed version returns `{ ok: false, error: 'only jammers can jam' }`.

## Design follow-ups (addressed on `cursor/edge-sentry-0858`)

### 6. Threat scorer almost never says `hostile` — fixed

Added `closingOnBlue` feature + reweighted scorer so a homing missile can reach `hostile` without sitting in a jam field. `jamFieldAt` is team-aware (only opposing jammers). See `native/src/aiml_scorer.cpp` and `scoreAllTracks()`.

Follow-up: `closingOnBlue` is set from the contact's actual `targetId`, so the heuristic is reading ground truth a sensor would not have. It is kept as `--scorer heuristic` for comparison. The default scorer is now a small neural network trained on simulator rollouts that uses observable inputs only (motion, track history, emissions, effects on blue). On 200 held-out scenarios it scores AUC 0.978 against 0.896 for this heuristic, and passes 13 of 14 scripted scenarios against 12. See `ml/README.md` and `ml/RESULTS.md`.

### 7. Default scenario never exercises jamming — fixed

Moved `R-J1` to `(5,6)` and apply an integrity pass on scenario load so `B-R1` starts under the jam cone.

### 8. Smaller notes (still open)

- A unit with both a `move` order and a target moves twice per tick (once in the order pass, once in the engage pass).
- The `jam` order has no effect: a jammer's radius is always active, and `cease` does not switch it off.
- `--action none` is accepted by the CLI but is not in the usage text or the tool schema.
- A corrupt or empty `world.json` is silently replaced by the default scenario. An explicit error may be kinder.
- `spawn` is in the CLI but has no agent tool. If that is intentional, the instructions could say so.
- `json_mini.hpp` extractors do not handle escaped quotes or brackets inside strings. Fine while ids are generated, fragile if free text ever reaches the world file.

## Regression tests

`native/tests/test_cli.py` (Python standard library only) covers the fixes above and the scorer behavior. The original 21 checks all passed on the fix branch; against `8f5f284`, 17 of the 21 failed (the last two as knock-on effects of earlier invalid orders having been accepted). CI now runs the full file on every push, along with the model evaluation.

```bash
npm run build:native
python3 native/tests/test_cli.py
```
