#!/usr/bin/env bash
# Start the PerfArena harness runner on a measurement host (port 8787):
#
#   nohup setsid runner/start_runner.sh > /dev/null 2>&1 < /dev/null &
#
# The runner settings below are the ones that measured the paper's data.
# The script keeps the caller's environment (PATH, JAVA_HOME, DOTNET_ROOT, ...),
# so set those for your toolchains first, or put the folders in
# PERFARENA_TOOLS_PATH. Settings, with their defaults:
#   PERFARENA_PYTHON        the measurement venv's python (~/.venvs/perfrunner/bin/python,
#                           the venv that setup-harness-host.sh makes)
#   PERFARENA_TOKEN_FILE    a file (mode 600) that holds the shared token; the
#                           backend's harness config.auth_token must be the same
#                           (~/.perfarena_runner_token)
#   PERFARENA_MACHINE_NAME  the machine name stored with each run (the short hostname)
#   PERFARENA_TOOLS_PATH    folders to put first on PATH (empty)
#   PERFARENA_LOG_FILE      the runner's log (~/harness.log). Errors before the runner
#                           starts its log go to <log>-console.log (for example harness-console.log).
#   PERFARENA_HOST          the address to bind (0.0.0.0). Use 127.0.0.1 when the
#                           backend runs on the same host.
# Check: POST /validate with body {} and the token returns 400 when the runner is up.
set -euo pipefail
cd "$(dirname "$0")"

# The system locale. An ssh login from macOS sends LC_CTYPE=UTF-8, a name that
# Linux does not know; the runner then falls back to the C locale (Elixir warns
# about latin1, Ruby reads input as ASCII).
unset LC_ALL LC_CTYPE
export LANG=en_US.UTF-8

if [ -n "${PERFARENA_TOOLS_PATH:-}" ]; then export PATH="$PERFARENA_TOOLS_PATH:$PATH"; fi

export PERFARENA_ROOT="$PWD/../Energy-Languages"
export PYTHONPATH="$PWD/../Energy-Languages"
export PERFARENA_MACHINE_NAME="${PERFARENA_MACHINE_NAME:-$(hostname -s)}"
export PERFARENA_ENERGY_TOOL=codecarbon
export TSC=tsc
export PERFARENA_ISOLATION_CHECK=1
export PERFARENA_HANDLER_TIMEOUT_S=1200
export PERFARENA_LC_MEASURE_TIMEOUT_S=420
export PERFARENA_REQUEST_TIMEOUT_S=1320
export PERFARENA_LOG_FILE="${PERFARENA_LOG_FILE:-$HOME/harness.log}"

# The token goes in through the environment, not --auth-token, so that it
# does not show in ps.
TOKEN_FILE="${PERFARENA_TOKEN_FILE:-$HOME/.perfarena_runner_token}"
[ -r "$TOKEN_FILE" ] || { echo "start_runner.sh: no token file $TOKEN_FILE" >&2; exit 1; }
HARNESS_RUNNER_TOKEN="$(cat "$TOKEN_FILE")"
export HARNESS_RUNNER_TOKEN

exec "${PERFARENA_PYTHON:-$HOME/.venvs/perfrunner/bin/python}" harness_runner_server.py \
  --host "${PERFARENA_HOST:-0.0.0.0}" --port 8787 \
  --measure-handler perfarena_handler:measure_once \
  --validate-handler perfarena_handler:validate_once \
  --request-timeout 1320 --verbose >> "${PERFARENA_LOG_FILE%.log}-console.log" 2>&1
