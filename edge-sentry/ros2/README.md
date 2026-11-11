# ROS 2 bridge

The autonomy interface in [`docs/INTERFACE.md`](../docs/INTERFACE.md) as ROS 2 topics, so the planner can sit in a ROS 2 system the way it would on a real vehicle.

```
 sim_node  ── edge_sentry/observation (edge_sentry_msgs/Observation) ──▶  autonomy_node
           ◀── edge_sentry/plan        (edge_sentry_msgs/Plan)        ──
```

- `edge_sentry_msgs`: five typed messages (`Observation`, `Contact`, `OwnUnit`, `Plan`, `Order`). They carry the same fields as the JSON interface, nothing more: a contact never carries its orders or its target.
- `edge_sentry_bridge`:
  - `sim_node` runs the simulator, publishes what blue can see once per tick, and applies a plan only if it answers the current tick. A late plan is dropped and the units keep their last orders.
  - `autonomy_node` hands each observation to `edge-node` (the same C++ planner that is cross-built for arm64) and publishes its answer. It keeps nothing between messages.

## Run it

Needs ROS 2 Jazzy and the two programs built by `npm run build:native`.

```bash
source /opt/ros/jazzy/setup.bash
cd ros2 && colcon build && source install/setup.bash && cd ..
ros2 launch edge_sentry_bridge demo.launch.py \
  sim:=$PWD/native/build/edge-sim node:=$PWD/native/build/edge-node
```

In another terminal, `ros2 topic echo /edge_sentry/plan` shows each order with its behavior mode and reason.

## How it is checked

CI builds both packages in the official `ros:jazzy` container and runs `check_bridge.py`. That starts the two nodes as separate processes, listens on both topics from a third node, and checks that an observation goes out every tick, a plan comes back for it, each plan is exactly what the planner gives for that observation, and the orders take effect in the simulator.

## Limits

- Tested on one machine. Nothing here measures ROS 2 over a real radio link; `sil/` does the link-loss experiments on the plain TCP interface.
- The planner is reached by starting `edge-node` as a child process, not by linking it into a C++ ROS 2 node.
- Default quality-of-service settings (reliable, keep last 10). A lossy link would want best-effort with the tick check that is already there.
