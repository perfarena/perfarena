#!/usr/bin/env bash
# Build + run the PerfArena harness TEST container with the repo mounted at
# /work. The repo is mounted (not copied), so your local edits to the harnesses
# apply immediately — no rebuild.
#
#   ./docker/harness-test/run.sh build     # build the image (slow: all toolchains)
#   ./docker/harness-test/run.sh           # drop into a shell in /work (default)
#   ./docker/harness-test/run.sh server    # run the :8787 arena runner
#   ./docker/harness-test/run.sh <cmd...>  # run an arbitrary command in the container
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
IMAGE="perfarena-harness-test:latest"

# Optional RAPL passthrough: on a LINUX host that exposes intel-rapl powercap,
# set PERFARENA_RAPL=1 to try real hardware energy instead of the TDP estimate.
# No effect on macOS/Windows (the Docker VM has no RAPL to pass through).
RAPL_ARGS=()
if [ "${PERFARENA_RAPL:-0}" = "1" ] && [ -d /sys/class/powercap ]; then
  RAPL_ARGS=(--privileged -v /sys/class/powercap:/sys/class/powercap:ro)
fi

_ensure_built() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$HERE"
}

cmd="${1:-shell}"
case "$cmd" in
  build)
    docker build -t "$IMAGE" "$HERE"
    ;;
  shell)
    _ensure_built
    docker run --rm -it "${RAPL_ARGS[@]}" -v "$REPO_ROOT:/work" "$IMAGE" bash
    ;;
  server)
    shift
    _ensure_built
    # The handler (perfarena_handler.py) sits next to the server script, so
    # running the script directly puts it on sys.path[0]. Port 8787 -> host.
    docker run --rm -it "${RAPL_ARGS[@]}" -p 8787:8787 -v "$REPO_ROOT:/work" "$IMAGE" \
      python /work/webapp/examples/harness_runner_server.py \
        --host 0.0.0.0 --port 8787 \
        --measure-handler perfarena_handler:measure_once \
        --validate-handler perfarena_handler:validate_once "$@"
    ;;
  *)
    _ensure_built
    docker run --rm -it "${RAPL_ARGS[@]}" -v "$REPO_ROOT:/work" "$IMAGE" "$@"
    ;;
esac
