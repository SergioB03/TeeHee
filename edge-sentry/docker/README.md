# Running edge-sentry with Docker

Nothing to install but Docker. From the `edge-sentry` folder:

```bash
docker compose up                        # console on http://localhost:3100, planner on 127.0.0.1:7700
docker compose run --rm sil              # software-in-the-loop across two containers
docker compose --profile ros2 up ros2    # the ROS 2 demo, with no ROS on your machine
docker compose down                      # stop; add -v to also delete the saved worlds
```

## What gets built

One `Dockerfile` makes three images, and `ros2/Dockerfile` a fourth.

| Image | What is in it | Base |
| --- | --- | --- |
| `edge-sentry-node` | The autonomy planner, one statically linked 2 MB file. No shell, no libraries | `scratch` |
| `edge-sentry-console` | The web console and the simulator | `node:22-bookworm-slim` |
| `edge-sentry-tools` | The simulator, the planner and the test harnesses | `python:3.12-slim-bookworm` |
| `edge-sentry-ros2` | The ROS 2 messages and the two bridge nodes | `ros:jazzy` |

The compiler stage runs on the build machine's own architecture and cross-compiles when the target is different, so the planner image for a Raspberry Pi or Jetson builds on a laptop without emulating a compiler:

```bash
docker buildx build --platform linux/arm64 --target edge-node -t edge-sentry-node:arm64 .
```

## Two computers on one machine

`docker compose run --rm sil` is the software-in-the-loop test from [`sil/`](../sil/README.md) with a real boundary between the two halves: the simulator runs in one container, the planner in another, and they can only talk over the network. The planner's container is held to half a CPU core, 32 MB of memory and 8 processes, which is the kind of budget a small edge computer gives you.

On the development machine that run agrees with the in-simulator planner on every tick, plans in about 13 µs and answers in about a quarter of a millisecond, with no reply over the 50 ms budget. A container limit caps how much of this CPU the planner may use; it does not make the CPU as slow as a Pi's, so this is still not a timing on real hardware.

## Training on your own machine

```bash
docker compose build train                 # several GB: PyTorch with its CUDA libraries
docker compose run --rm train              # trains the learned policy; uses an NVIDIA GPU if it can see one
docker compose run --rm train python3 ml/rl_eval.py --weights ml/rl_runs/latest.json --no-write
```

The weights are written to `ml/rl_runs/latest.json` in your checkout. On a machine without an NVIDIA GPU use `train-cpu` in place of `train`; the first line of the log says which device is in use. On Windows the GPU reaches the container through Docker Desktop's WSL 2 backend and needs a current NVIDIA driver.

What to expect: the network is small and the simulator runs on the CPU, so CPU threads set the pace (one simulator per thread) and the GPU has little to do at the default size. It starts to matter with a wider network (`--hidden 256`) and long runs (`--steps 20000000`). A run with `--roe free` trains without the hold-fire mask, which is how to measure what that rule costs.

## How the containers are confined

- Every image runs as a non-root user.
- The console, the planner and the test tools run on a read-only filesystem with all Linux capabilities dropped and privilege escalation switched off. The console can write to one place, the `worlds` volume, which keeps each visitor's world and game across rebuilds.
- The planner's port is published on `127.0.0.1` only. The planner has no authentication, so it is not offered to the network unless you change that line yourself.
- Both programs stop at once on `docker stop`. A program that is the first process in a container never gets the default "terminate" action for a signal, so each one handles the stop request itself and exits with status 0.
- The planner image has no shell or curl for a health check, so the planner checks itself: `edge-node --ping 127.0.0.1:7700`.

## How it is checked

`python3 docker/check.py` builds the images, starts the stack on spare ports under its own project name, and checks all of the above from outside: what is in the planner image, who each container runs as and what the kernel says its capabilities are, that a write outside the data volume is refused, that worlds survive the console being replaced, that software-in-the-loop passes across the two containers, that the simulator's and planner's own test suites pass inside the tools image, and that both containers stop within three seconds. `python3 docker/check.py --ros2` builds the ROS 2 image, runs the demo and runs the bridge check inside it. CI runs both, and builds the arm64 planner image and runs the planner tests against the binary inside it.

## Limits

- Base images are named by tag, not pinned by digest, and the images are not published to a registry. You build them where you run them.
- The ROS 2 demo runs both nodes in one container. Two containers talking DDS across a network is the next step.
- The resource limits show the planner fits a small budget. They say nothing about a Pi's or Jetson's speed.
