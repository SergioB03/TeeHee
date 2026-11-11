# One file, three images.
#
#   console    the web console with the simulator         docker compose up
#   edge-node  the autonomy planner and nothing else      (same command; it listens on 127.0.0.1:7700)
#   tools      the simulator, the planner and the test    docker compose run --rm sil
#              harnesses, for running the checks in a container
#
# The compiler stage runs on the build machine's own architecture and
# cross-compiles when the target differs, so this makes a Raspberry Pi or
# Jetson image of the planner without emulating a compiler:
#
#   docker buildx build --platform linux/arm64 --target edge-node -t edge-sentry-node:arm64 .
#
# Both programs are linked statically, so the planner image starts FROM
# scratch: one 2 MB file, no shell and no libraries.

# ---- compile ---------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS build
ARG BUILDARCH
ARG TARGETARCH
RUN set -eu; \
    cross=""; \
    if [ "$TARGETARCH" != "$BUILDARCH" ]; then \
      case "$TARGETARCH" in \
        arm64) cross="g++-aarch64-linux-gnu" ;; \
        amd64) cross="g++-x86-64-linux-gnu" ;; \
        *) echo "no cross compiler is set up for $TARGETARCH" >&2; exit 1 ;; \
      esac; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends g++ libc6-dev $cross; \
    rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY native/ native/
RUN set -eu; \
    cxx=g++; \
    if [ "$TARGETARCH" != "$BUILDARCH" ]; then \
      case "$TARGETARCH" in \
        arm64) cxx=aarch64-linux-gnu-g++ ;; \
        amd64) cxx=x86_64-linux-gnu-g++ ;; \
      esac; \
    fi; \
    mkdir /out; \
    $cxx -std=c++17 -O2 -static -s -Inative/src native/src/*.cpp -o /out/edge-sim; \
    $cxx -std=c++17 -O2 -static -s -Inative/src native/node/edge_node.cpp native/src/autonomy.cpp -o /out/edge-node

# ---- the planner alone, as it would run on an edge computer --------------------
FROM scratch AS edge-node
COPY --from=build /out/edge-node /edge-node
USER 65534:65534
EXPOSE 7700
# The image has no shell or curl, so the planner checks itself.
HEALTHCHECK --interval=5s --timeout=3s --start-period=2s --retries=3 \
    CMD ["/edge-node", "--ping", "127.0.0.1:7700"]
ENTRYPOINT ["/edge-node"]
CMD ["--listen", "7700", "--bind", "0.0.0.0"]

# ---- the simulator and the test harnesses -------------------------------------
FROM python:3.12-slim-bookworm AS tools
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /out/edge-sim /out/edge-node native/build/
COPY native/tests/ native/tests/
COPY ml/simlab.py ml/
COPY sil/run_sil.py sil/
USER 65534:65534
CMD ["python3", "sil/run_sil.py", "--check", "--no-write"]

# ---- the web console (last, so a plain `docker build .` makes this one) --------
FROM node:22-bookworm-slim AS console
ENV NODE_ENV=production PORT=3100 EDGE_WORLD=/data/world.json
WORKDIR /app
COPY --from=build /out/edge-sim native/build/edge-sim
COPY web/server.mjs web/index.html web/
RUN mkdir /data && chown node:node /data
USER node
VOLUME ["/data"]
EXPOSE 3100
HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3100/api/state').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "web/server.mjs"]
