# edge-sentry

Scalable-but-small **terrain electronic-warfare / air sandbox** for internship portfolio work.

- **Agent SDK** ops console (TypeScript) — brief the map, issue sim orders, refresh AIML scores
- **C++ `edge-sim`** — 12×12 terrain, sensors / jammers / jets / missiles, tick loop
- **AIML hook** — track features → `benign | watch | hostile` (heuristic now; train later)

Simulation / wargame math only. No real RF jammer designs, seeker guidance, or offensive cyber tooling.

## Quick start

```bash
npm install
npm run build:native
npx @grok/agent-sdk login   # once, for model turns
npx @grok/agent-sdk dev
# open http://127.0.0.1:3000/playground
```

## Layout

| Path | Role |
| --- | --- |
| `bot/` | Agent instructions + tools |
| `native/` | C++ sim + `aiml_scorer` |
| `evals/` | Smoke eval |

## Later detail (roadmap)

1. Richer terrain / LOS, more unit doctrine
2. Dump track features → train a small classifier → swap scorer
3. Raspberry Pi field-node HAL behind the same JSON CLI
4. Multi-node networked sim

## Safety line

Abstract engage / jam effects are **game labels** for learning systems + agents. Do not extend this repo into real weaponization or exploit code.