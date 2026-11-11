# Autonomy interface, version 1

The contract between whatever produces the picture (here, `edge-sim`) and the
autonomy that decides what blue does (`edge-sim autopilot` or `edge-node`).
Both messages are a single line of JSON. Source of truth: `native/src/autonomy.hpp`.

## Observation (to the autonomy)

`edge-sim --world w.json observe` prints it under `"observation"`.

| Field | Meaning |
| --- | --- |
| `v` | Message version. Must be `1`; anything else is refused |
| `tick` | Simulation tick the picture was taken on |
| `width`, `height`, `ceiling` | Map size in cells and the highest altitude level |
| `watchAt` | Threat score at which a contact is labelled "watch" (the engage line) |
| `own[]` | Blue's live units: `id`, `type`, `x`, `y`, `z`, `hp`, current order `act`, its target `tgt`, and `tx`, `ty`, `tz` |
| `contacts[]` | Blue's tracks: `id`, class `cls`, position `x`, `y`, `z`, movement over the last tick `vx`, `vy`, `vz`, track quality `snr`, `locked` (1 if point defense can shoot at it), threat `score`, and `jam` radius if it is an emitter |

A contact's orders and target are never in the message. A regression test
checks that no blue unit id appears anywhere in `contacts`.

## Plan (from the autonomy)

| Field | Meaning |
| --- | --- |
| `ok` | `true`, or `false` with an `error` string |
| `tick` | The tick of the observation this plan answers |
| `micros` | Time spent planning, in microseconds |
| `orders[]` | Orders to give now: `id`, `action` (`defend`, `launch`, `move`, `orbit`, `cease`), `target`, `tx`, `ty`, `tz`, the behavior `mode`, and a `reason` in plain words |

`mode` is one of `DEFEND`, `HOLD`, `PATROL`, `SHADOW`, `INTERCEPT`, `ENGAGE`, `RTB`.
A unit that should carry on as it is gets no order.

## Rules both sides keep

1. **Stateless.** Everything the planner needs is in the observation. A node that restarts mid-mission is correct again on the next message.
2. **A late plan is a dead plan.** The receiver discards a reply that arrives after the tick budget, and never applies a plan to a later tick.
3. **No reply means no change.** Units keep the orders they have.
4. **Only blue.** The planner orders blue units only; the simulator refuses anything else.
5. **Hold fire.** A fighter may be sent to shoot only at a contact whose score is at or above `watchAt`. Below it, the fighter may follow (`SHADOW`) but not engage.
6. **Transport.** Newline-delimited JSON over TCP, or stdin and stdout. One reply per message, in order. `{"ping":1}` is answered with `{"pong":1}`.
