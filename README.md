# edge-sentry

A small air and electronic-warfare sandbox with a trained threat model, an LLM ops agent, and a phone console. The project lives in [`edge-sentry/`](./edge-sentry); start with its [README](./edge-sentry/README.md).

**Live demo:** [edge-sentry.replit.app](https://edge-sentry.replit.app)

![Phone console demo](edge-sentry/docs/demo.gif)

- **C++ simulator** with a JSON CLI: sensors, jammers, jets, missiles, a tick loop
- **Threat model** trained on simulator rollouts: AUC 0.978 on held-out scenarios using observable inputs only; triage by its scores cuts hits taken by more than half ([results](./edge-sentry/ml/RESULTS.md))
- **Ops agent** with four tools, and a **phone console** you can run on Replit
