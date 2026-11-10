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

## Phone console

A phone-sized web page for driving the simulator by hand: tap units on the map, give move / jam / launch / cease orders, advance ticks, add units, and read threat scores and the event log. It calls the same `edge-sim` binary as the agent tools. No model login and no `npm install` needed (the server uses Node built-ins only).

```bash
npm run web          # builds edge-sim if needed, then serves on port 3100
# open http://localhost:3100
```

On Replit: import this repo and press Run. The root `.replit` file runs the same script and exposes the port. The world is shared by everyone who has the page open and lives in a temp file, so it resets when the server restarts.

## Layout

| Path | Role |
| --- | --- |
| `bot/` | Agent instructions + tools |
| `native/` | C++ sim + `aiml_scorer` |
| `web/` | Phone console (`server.mjs`, `index.html`, `run.sh`) |
| `evals/` | Smoke eval |

## Later detail (roadmap)

1. Richer terrain / LOS, more unit doctrine
2. Dump track features → train a small classifier → swap scorer
3. Raspberry Pi field-node HAL behind the same JSON CLI
4. Multi-node networked sim

## Safety line

Abstract engage / jam effects are **game labels** for learning systems + agents. Do not extend this repo into real weaponization or exploit code.