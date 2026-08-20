#!/usr/bin/env bash
# ============================================================================
# setup-harness-host.sh — prepare a machine to host the PerfArena benchmark
# harness (the arena-local performance/correctness oracle).
#
# Installs the ten language toolchains the harness compiles and runs
# (C++, C#, Go, Java, JavaScript, PHP, Python, Ruby, Rust, TypeScript), the
# energy-measurement stack (perf + RAPL on Linux, powermetrics on macOS), and
# a Python venv with codecarbon + psutil for the arena-local runner.
#
# Bare-metal only, no container. A container adds cgroup and namespace layers
# that perturb RAPL package-energy accounting and add scheduling overhead, so
# the measurement host runs every toolchain directly on the OS.
#
# Supported hosts: Debian/Ubuntu (apt), Fedora/RHEL (dnf), macOS (Homebrew).
# The primary measurement target is a bare-metal Intel Linux host.
#
# Usage:
#   ./setup-harness-host.sh [--venv PATH] [--tune] [--skip-toolchains]
#                           [--skip-energy] [--dotnet-channel N.0] [--dry-run]
#
#   --venv PATH        measurement venv location (default: ~/.venvs/perfrunner)
#   --tune             apply measurement tuning (perf governor, no turbo). LINUX
#                      ONLY, changes system state; off by default.
#   --skip-toolchains  do not (re)install language toolchains
#   --skip-energy      do not touch RAPL/perf access or kernel knobs
#   --dotnet-channel   .NET SDK channel to install (default: 10.0; the harness
#                      csproj targets net10.0)
#   --dry-run          print the actions without running them
# ============================================================================
set -uo pipefail

# ---------------------------------------------------------------- config/args
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_DIR="$REPO_ROOT/Energy-Languages"
VENV="${HOME}/.venvs/perfrunner"
DOTNET_CHANNEL="10.0"
DO_TUNE=0; SKIP_TOOLCHAINS=0; SKIP_ENERGY=0; DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --venv) VENV="$2"; shift 2 ;;
    --tune) DO_TUNE=1; shift ;;
    --skip-toolchains) SKIP_TOOLCHAINS=1; shift ;;
    --skip-energy) SKIP_ENERGY=1; shift ;;
    --dotnet-channel) DOTNET_CHANNEL="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- logging
c() { [ -t 1 ] && printf '\033[%sm' "$1" || true; }
log()  { printf '%s[ok]%s %s\n'   "$(c '1;32')" "$(c 0)" "$*"; }
info() { printf '%s[..]%s %s\n'   "$(c '1;34')" "$(c 0)" "$*"; }
warn() { printf '%s[!!]%s %s\n'   "$(c '1;33')" "$(c 0)" "$*" >&2; }
die()  { printf '%s[xx]%s %s\n'   "$(c '1;31')" "$(c 0)" "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '  + %s\n' "$*"; else eval "$@"; fi; }

# ---------------------------------------------------------------- platform
OS="$(uname -s)"; ARCH="$(uname -m)"
PKG=""; SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
if [ "$OS" = "Linux" ]; then
  if   command -v apt-get >/dev/null 2>&1; then PKG="apt"
  elif command -v dnf     >/dev/null 2>&1; then PKG="dnf"
  else die "unsupported Linux: need apt-get or dnf"; fi
elif [ "$OS" = "Darwin" ]; then
  PKG="brew"
  command -v brew >/dev/null 2>&1 || die "Homebrew required on macOS: https://brew.sh"
else
  die "unsupported OS: $OS"
fi
info "host: $OS/$ARCH  pkg=$PKG  repo=$REPO_ROOT  venv=$VENV"
[ -d "$HARNESS_DIR/selection" ] || warn "harness dir $HARNESS_DIR/selection not found (run this from the repo root)"

pm_install() {  # package-manager install, best-effort per package
  case "$PKG" in
    apt)  run "$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $*" ;;
    dnf)  run "$SUDO dnf install -y $*" ;;
    brew) run "brew install $* || true" ;;
  esac
}

# ---------------------------------------------------------------- toolchains
install_toolchains() {
  info "installing base tools + language toolchains"
  case "$PKG" in
    apt)
      run "$SUDO apt-get update -y"
      pm_install ca-certificates curl wget git make pkg-config build-essential
      pm_install gcc g++ clang
      pm_install openjdk-21-jdk-headless
      pm_install nodejs npm
      pm_install php-cli
      pm_install ruby
      pm_install golang-go
      pm_install python3 python3-pip python3-venv python3-dev
      ;;
    dnf)
      pm_install ca-certificates curl wget git make pkgconf-pkg-config gcc gcc-c++ clang
      pm_install java-21-openjdk-devel
      pm_install nodejs npm
      pm_install php-cli
      pm_install ruby
      pm_install golang
      pm_install python3 python3-pip python3-devel
      ;;
    brew)
      pm_install gcc llvm openjdk@21 node php ruby go python@3.12
      warn "macOS: C/C++ uses Apple clang from Xcode CLT (xcode-select --install)"
      ;;
  esac

  # TypeScript compiler — harness_ts.js resolves it via 'npm root -g'.
  if command -v npm >/dev/null 2>&1; then
    info "installing TypeScript (global npm)"
    run "$SUDO npm install -g typescript || npm install -g typescript"
  else
    warn "npm not found; TypeScript cells will fail until 'npm install -g typescript'"
  fi

  # Rust via rustup (the harness generates + compiles Rust with rustc/cargo).
  if ! command -v rustc >/dev/null 2>&1 && [ ! -x "$HOME/.cargo/bin/rustc" ]; then
    info "installing Rust (rustup, stable)"
    run "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path"
  else
    log "rust already present"
  fi

  # .NET SDK — the harness csproj targets net${DOTNET_CHANNEL/./}. Distro
  # packages lag, so install via Microsoft's channel installer into ~/.dotnet.
  if ! dotnet --list-sdks 2>/dev/null | grep -q "^${DOTNET_CHANNEL%.*}\."; then
    info "installing .NET SDK channel $DOTNET_CHANNEL (~/.dotnet)"
    run "curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh"
    run "bash /tmp/dotnet-install.sh --channel $DOTNET_CHANNEL --install-dir \"$HOME/.dotnet\""
  else
    log ".NET SDK $DOTNET_CHANNEL already present"
  fi
}

# ---------------------------------------------------------------- python venv
install_venv() {
  info "creating measurement venv at $VENV (codecarbon + psutil)"
  run "python3 -m venv \"$VENV\""
  run "\"$VENV/bin/pip\" install --upgrade pip"
  run "\"$VENV/bin/pip\" install codecarbon psutil"
  # If the perfarena package ships a pyproject, install it too (CLI + runners).
  if [ -f "$HARNESS_DIR/pyproject.toml" ]; then
    run "\"$VENV/bin/pip\" install \"$HARNESS_DIR\""
  fi
}

# ---------------------------------------------------------------- energy access
setup_energy() {
  if [ "$OS" = "Darwin" ]; then
    warn "macOS energy backend is /usr/bin/powermetrics; it requires sudo. Launch the runner so it can call: sudo powermetrics ..."
    return
  fi
  # Linux: perf for RAPL + read access to the powercap intel-rapl counters.
  info "installing perf (linux-tools) for RAPL"
  case "$PKG" in
    apt) pm_install "linux-tools-common linux-tools-generic linux-tools-$(uname -r)" || warn "install perf matching your kernel manually" ;;
    dnf) pm_install perf ;;
  esac
  # Load the msr module (for MSR-based readers) — best effort.
  run "$SUDO modprobe msr 2>/dev/null || true"
  # codecarbon/perf read /sys/class/powercap/intel-rapl:*/energy_uj (root 0400).
  if ls /sys/class/powercap/intel-rapl:* >/dev/null 2>&1; then
    info "granting read access to RAPL powercap counters"
    run "$SUDO chmod -R a+r /sys/class/powercap/intel-rapl* 2>/dev/null || true"
    # Persist across reboot via a udev rule.
    run "printf 'SUBSYSTEM==\"powercap\", ACTION==\"add\", MODE=\"0444\"\n' | $SUDO tee /etc/udev/rules.d/99-rapl-read.rules >/dev/null"
    log "RAPL powercap readable (persistent udev rule installed)"
  else
    warn "no intel-rapl powercap found; this host has no RAPL. codecarbon will fall back to a TDP estimate (not a hardware measurement)."
  fi
  # perf RAPL events need a permissive paranoia level.
  run "$SUDO sysctl -w kernel.perf_event_paranoid=0 >/dev/null 2>&1 || true"
  run "printf 'kernel.perf_event_paranoid=0\n' | $SUDO tee /etc/sysctl.d/99-perfarena.conf >/dev/null 2>&1 || true"
}

# ---------------------------------------------------------------- tuning (opt)
tune_machine() {
  [ "$OS" = "Linux" ] || { warn "--tune is Linux-only; skipping"; return; }
  info "applying measurement tuning (governor=performance, turbo off)"
  case "$PKG" in apt) pm_install linux-tools-common cpufrequtils ;; dnf) pm_install kernel-tools ;; esac
  run "$SUDO cpupower frequency-set -g performance 2>/dev/null || true"
  # Disable Intel turbo (best effort; path differs by driver).
  run "echo 1 | $SUDO tee /sys/devices/system/cpu/intel_pstate/no_turbo >/dev/null 2>&1 || true"
  warn "for reliable energy: also pin the runner to one core (taskset -c N), stop background services, and ensure good cooling. See docs/measurement plan."
}

# ---------------------------------------------------------------- verify
write_versions() {
  local out="$REPO_ROOT/harness-host-versions.txt"
  export PATH="$HOME/.cargo/bin:$HOME/.dotnet:$PATH"
  info "recording toolchain versions -> $out"
  { echo "date=$(date -Iseconds 2>/dev/null || date)"; echo "host=$OS/$ARCH"
    for probe in \
      'gcc --version' 'g++ --version' 'clang --version' 'python3 --version' \
      'node --version' 'tsc --version' 'javac -version' 'dotnet --version' \
      'php --version' 'ruby --version' 'go version' 'rustc --version' 'cargo --version'; do
      printf '%-20s ' "${probe%% *}:"
      eval "$probe" 2>&1 | head -1 || echo "MISSING"
    done
    echo "venv=$VENV"; "$VENV/bin/python" -c 'import codecarbon,psutil;print("codecarbon",codecarbon.__version__,"psutil",psutil.__version__)' 2>/dev/null || echo "venv=INCOMPLETE"
  } | tee "$out"
}

smoke_test() {
  local rh="$HARNESS_DIR/selection/run_harness.sh"
  [ -f "$rh" ] || { warn "run_harness.sh not found; skipping smoke test"; return; }
  export PATH="$HOME/.cargo/bin:$HOME/.dotnet:$PATH"
  local cell; cell="$(find "$HARNESS_DIR/Python/leetcode" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)"
  [ -n "$cell" ] || { warn "no Python cell found; skipping smoke test"; return; }
  info "smoke test: python3 harness on $(basename "$cell")"
  if [ "$DRY_RUN" = 1 ]; then echo "  + (cd $cell && bash $rh python3 0.2 0)"; return; fi
  ( cd "$cell" && bash "$rh" python3 0.2 0 2>&1 | grep -o 'BEACON=.*' | head -1 ) \
    && log "python3 harness OK" || warn "python3 smoke test did not emit a BEACON — check the toolchain"
}

# ---------------------------------------------------------------- run
[ "$SKIP_TOOLCHAINS" = 1 ] || install_toolchains
install_venv
[ "$SKIP_ENERGY" = 1 ] || setup_energy
[ "$DO_TUNE" = 1 ] && tune_machine
write_versions
smoke_test

# ---------------------------------------------------------------- next steps
cat <<NEXT

$(c '1;32')Done.$(c 0) Add the toolchains to your PATH (append to ~/.bashrc or ~/.zshrc):

    export PATH="\$HOME/.cargo/bin:\$HOME/.dotnet:\$PATH"

Launch the arena-local runner from the measurement venv so it can measure energy:

    "$VENV/bin/python" $REPO_ROOT/webapp/examples/harness_runner_server.py   # serves :8787

Verify a single case in any language from a cell directory, e.g.:

    cd $HARNESS_DIR/Rust/leetcode/<slug> && bash $HARNESS_DIR/selection/run_harness.sh rust 1.0 0

Toolchain versions were written to: $REPO_ROOT/harness-host-versions.txt
NEXT
