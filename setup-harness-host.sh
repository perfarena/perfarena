#!/usr/bin/env bash
# ============================================================================
# setup-harness-host.sh — prepare a machine to host the PerfArena benchmark
# harness (the arena-local performance/correctness oracle).
#
# Installs the fourteen language toolchains the harness compiles and runs
# (C, C++, C#, Go, Java, JavaScript, Kotlin, PHP, Python, Ruby, Rust, Scala,
# Swift, TypeScript), the energy-measurement stack (perf + RAPL on Linux,
# powermetrics on macOS), and a Python venv with codecarbon + psutil for the
# arena-local runner.
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
KOTLIN_VERSION="${KOTLIN_VERSION:-}"   # empty => resolve the latest release tag
DART_VERSION="${DART_VERSION:-}"       # empty => resolve the latest stable release
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
      pm_install ca-certificates curl wget git make pkg-config build-essential unzip zip gnupg
      pm_install gcc g++ clang
      pm_install libboost-dev libgmp-dev   # C++ pidigits (Boost.Multiprecision) + Python gmpy2 (GMP)
      pm_install libpcre2-dev              # C/C++ regex-redux (PCRE2)
      # C CLBG whole-program references: binary-trees links APR pools (-lapr-1),
      # k-nucleotide uses htslib/khash, regex-redux links legacy PCRE (-lpcre).
      pm_install libapr1-dev libaprutil1-dev libhts-dev libpcre3-dev
      pm_install openjdk-21-jdk-headless
      install_node
      pm_install php-cli php-gmp           # php-gmp: PHP pidigits (GMP bignum)
      pm_install ruby
      pm_install golang-go
      pm_install python3 python3-pip python3-venv python3-dev
      ;;
    dnf)
      pm_install ca-certificates curl wget git make pkgconf-pkg-config gcc gcc-c++ clang unzip zip gnupg2
      pm_install boost-devel gmp-devel pcre2-devel
      # C CLBG references: APR pools (binary-trees), legacy PCRE (regex-redux).
      pm_install apr-devel apr-util-devel pcre-devel
      pm_install htslib-devel || warn "htslib-devel unavailable (may need EPEL); C k-nucleotide clbg needs it"
      pm_install java-21-openjdk-devel
      install_node
      pm_install php-cli php-gmp
      pm_install ruby
      pm_install golang
      pm_install python3 python3-pip python3-devel
      ;;
    brew)
      pm_install gcc llvm libomp openjdk@21 node php ruby go python@3.12 boost gmp pcre pcre2 apr apr-util htslib
      warn "macOS: C/C++ uses Apple clang from Xcode CLT (xcode-select --install); the C CLBG cells need GNU gcc (CC=gcc-<N>) for -fopenmp/-march=native, and brew apr is keg-only (pass its include/lib paths)."
      ;;
  esac

  # PHP short_open_tag — several CLBG PHP references open with `<?` (not
  # `<?php`). With the default (off) PHP echoes the source instead of running
  # it, so every such cell fails. Enable it via a conf.d drop-in in the ini dir
  # this PHP scans (falls back to the loaded ini).
  if command -v php >/dev/null 2>&1; then
    _php_scan="$(php -i 2>/dev/null | sed -n 's/^Scan this dir for additional \.ini files => //p' | head -1)"
    if [ -n "$_php_scan" ]; then
      run "$SUDO mkdir -p \"$_php_scan\""
      run "printf 'short_open_tag=On\\n' | $SUDO tee \"$_php_scan/99-perfarena.ini\" >/dev/null"
      log "enabled PHP short_open_tag ($_php_scan/99-perfarena.ini)"
    else
      _php_ini="$(php -i 2>/dev/null | sed -n 's/^Loaded Configuration File => //p' | head -1)"
      if [ -n "$_php_ini" ] && [ "$_php_ini" != "(none)" ]; then
        run "printf 'short_open_tag=On\\n' | $SUDO tee -a \"$_php_ini\" >/dev/null"
        log "enabled PHP short_open_tag ($_php_ini)"
      else
        warn "could not locate a PHP ini to enable short_open_tag"
      fi
    fi
  fi

  # TypeScript compiler — harness_ts.js resolves it via 'npm root -g'.
  if command -v npm >/dev/null 2>&1; then
    info "installing TypeScript (global npm)"
    # Pin CLASSIC TypeScript (5.x). The default/`next` tag can pull the native
    # preview compiler (tsgo), whose lib/tsc.js is an ESM shim needing a native
    # platform package (@typescript/typescript-<plat>) that breaks tsc here.
    run "$SUDO npm install -g typescript@5 || npm install -g typescript@5"
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

  # JVM + native newcomers (Kotlin, Scala, Swift). Each is installed to a
  # stable path and symlinked into /usr/local/bin so the arena runner's
  # subprocess PATH sees them (the #1 cause of "kotlinc: not found").
  install_kotlin
  install_scala
  install_swift
  install_dart
}

# Node.js — Debian/Ubuntu apt ships an ANCIENT Node (12 on 20.04) that can't
# even PARSE the JS harnesses (nullish coalescing `??`, etc.), so every JS/TS
# measure run dies at module-load time. Install a current LTS from NodeSource.
install_node() {
  local major
  major="$(node --version 2>/dev/null | sed -n 's/^v\([0-9]*\).*/\1/p')"
  if [ -n "$major" ] && [ "$major" -ge 18 ] 2>/dev/null; then
    log "node $(node --version) already current"; return
  fi
  case "$PKG" in
    apt)
      info "installing Node.js 20 LTS (NodeSource; apt node is too old for the harnesses)"
      # ${SUDO:+...} so this works as root too: non-root -> `sudo -E bash -`,
      # root (SUDO empty) -> `bash -` (a bare `-E` would be "command not found").
      run "curl -fsSL https://deb.nodesource.com/setup_20.x | ${SUDO:+$SUDO -E }bash -"
      pm_install nodejs ;;
    dnf)
      info "installing Node.js 20 LTS (NodeSource)"
      run "curl -fsSL https://rpm.nodesource.com/setup_20.x | $SUDO bash -"
      pm_install nodejs ;;
    brew) pm_install node ;;
  esac
}

# Kotlin — JVM. Official compiler zip from JetBrains releases (needs a JRE,
# provided by openjdk above) + unzip. Symlinks kotlinc/kotlin into PATH.
install_kotlin() {
  command -v kotlinc >/dev/null 2>&1 && { log "kotlin already present"; return; }
  if [ "$PKG" = "brew" ]; then pm_install kotlin; return; fi
  local ver="$KOTLIN_VERSION"
  [ -n "$ver" ] || ver="$(curl -fsSL https://api.github.com/repos/JetBrains/kotlin/releases/latest 2>/dev/null \
        | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)"
  [ -n "$ver" ] || ver="2.1.20"   # fallback pin if the API is unreachable
  info "installing Kotlin compiler $ver -> /opt/kotlinc"
  run "curl -fsSL -o /tmp/kotlin-compiler.zip https://github.com/JetBrains/kotlin/releases/download/v${ver}/kotlin-compiler-${ver}.zip" \
    && run "$SUDO rm -rf /opt/kotlinc" \
    && run "$SUDO unzip -q /tmp/kotlin-compiler.zip -d /opt" \
    && run "$SUDO ln -sf /opt/kotlinc/bin/kotlinc /usr/local/bin/kotlinc" \
    && run "$SUDO ln -sf /opt/kotlinc/bin/kotlin  /usr/local/bin/kotlin" \
    && log "kotlin $ver installed" || warn "kotlin install failed — kotlin cells will not compile"
}

# Scala — JVM (Scala 3). Coursier installs the scala + scalac launchers; the
# harness runs compiled classes via `java` (libs discovered from the toolchain)
# or falls back to `scala run`, so either layout works. Symlinked into PATH.
install_scala() {
  command -v scalac >/dev/null 2>&1 && { log "scala already present"; return; }
  if [ "$PKG" = "brew" ]; then pm_install scala; return; fi
  local csa="x86_64"; [ "$ARCH" = "aarch64" ] && csa="aarch64"
  info "installing Scala (coursier) -> $HOME/.local/bin"
  run "mkdir -p \"$HOME/.local/bin\"" \
    && run "curl -fsSL https://github.com/coursier/launchers/raw/master/cs-${csa}-pc-linux.gz | gzip -d > /tmp/cs && chmod +x /tmp/cs" \
    && run "/tmp/cs install --install-dir \"$HOME/.local/bin\" scala scalac" \
    && run "$SUDO ln -sf \"$HOME/.local/bin/scala\"  /usr/local/bin/scala" \
    && run "$SUDO ln -sf \"$HOME/.local/bin/scalac\" /usr/local/bin/scalac" \
    && log "scala installed" || warn "scala install failed — scala cells will not compile"
}

# Swift — native (Linux). swiftly is Swift.org's official toolchain manager: it
# detects the distro, pulls the toolchain, and exposes proxy binaries under
# ~/.local/share/swiftly/bin. Symlink swiftc into PATH for the runner.
install_swift() {
  command -v swiftc >/dev/null 2>&1 && { log "swift already present"; return; }
  if [ "$PKG" = "brew" ]; then warn "macOS Swift ships with Xcode CLT (xcode-select --install)"; return; fi
  # link-time deps the Swift toolchain needs. Installed one-per-call because the
  # exact names drift across distro versions and apt aborts the whole batch on a
  # single unknown package; swiftly also resolves most of these itself.
  local p
  case "$PKG" in
    apt) for p in binutils libc6-dev libcurl4-openssl-dev libedit2 libncurses-dev \
                  libpython3-dev libxml2-dev libz3-dev tzdata zlib1g-dev; do pm_install "$p"; done ;;
    dnf) for p in binutils gcc-c++ libcurl-devel libedit-devel libuuid-devel \
                  libxml2-devel ncurses-devel python3-devel sqlite; do pm_install "$p"; done ;;
  esac
  local sa="x86_64"; [ "$ARCH" = "aarch64" ] && sa="aarch64"
  info "installing Swift via swiftly"
  run "curl -fsSL -o /tmp/swiftly.tar.gz https://download.swift.org/swiftly/linux/swiftly-${sa}.tar.gz" \
    && run "rm -rf /tmp/swiftly-x && mkdir -p /tmp/swiftly-x && tar -xzf /tmp/swiftly.tar.gz -C /tmp/swiftly-x" \
    && run "SWIFTLY_ASSUME_YES=1 /tmp/swiftly-x/swiftly init --assume-yes --quiet-shell-followup" \
    && run "set +u; . \"$HOME/.local/share/swiftly/env.sh\" 2>/dev/null || true; swiftly install latest --assume-yes; set -u" \
    && run "$SUDO ln -sf \"$HOME/.local/share/swiftly/bin/swiftc\" /usr/local/bin/swiftc" \
    && log "swift installed" || warn "swift install failed — swift cells will not compile"
}

# Dart — the official SDK zip from Google's dart-archive (same source on every
# OS/arch; avoids Homebrew's "untrusted tap" refusal and apt-repo key setup).
# The single `dart` CLI provides both `dart run` (the per-op leetcode harness,
# JIT) and `dart compile exe` (the CLBG whole-program harness, AOT), so one
# symlink into /usr/local/bin is all the runner needs.
install_dart() {
  command -v dart >/dev/null 2>&1 && { log "dart already present"; return; }
  local dos="linux"; [ "$OS" = "Darwin" ] && dos="macos"
  local darch="x64"; case "$ARCH" in aarch64|arm64) darch="arm64" ;; esac
  local ver="$DART_VERSION"
  [ -n "$ver" ] || ver="$(curl -fsSL https://storage.googleapis.com/dart-archive/channels/stable/release/latest/VERSION 2>/dev/null \
        | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1)"
  [ -n "$ver" ] || ver="3.13.5"   # fallback pin if the VERSION endpoint is unreachable
  info "installing Dart SDK $ver ($dos-$darch) -> /opt/dart-sdk"
  run "curl -fsSL -o /tmp/dart-sdk.zip https://storage.googleapis.com/dart-archive/channels/stable/release/${ver}/sdk/dartsdk-${dos}-${darch}-release.zip" \
    && run "$SUDO rm -rf /opt/dart-sdk" \
    && run "$SUDO unzip -q /tmp/dart-sdk.zip -d /opt" \
    && run "$SUDO ln -sf /opt/dart-sdk/bin/dart /usr/local/bin/dart" \
    && log "dart $ver installed" || warn "dart install failed — dart cells will not compile"
}

# ---------------------------------------------------------------- python venv
install_venv() {
  info "creating measurement venv at $VENV (codecarbon + psutil)"
  run "python3 -m venv \"$VENV\""
  run "\"$VENV/bin/pip\" install --upgrade pip"
  run "\"$VENV/bin/pip\" install codecarbon psutil gmpy2"   # gmpy2 for CLBG Python pidigits
  # If the perfarena package ships a pyproject, install it too (CLI + runners).
  # Best-effort: the arena runner imports `perfarena` via PYTHONPATH on the
  # measurement host, so a packaging hiccup here must not fail setup.
  if [ -f "$HARNESS_DIR/pyproject.toml" ]; then
    run "\"$VENV/bin/pip\" install \"$HARNESS_DIR\"" \
      || warn "perfarena package pip-install failed (non-fatal; runner imports it via PYTHONPATH)"
  fi
}

# ---------------------------------------------------------------- energy access
setup_energy() {
  if [ "$OS" = "Darwin" ]; then
    # macOS energy backend is /usr/bin/powermetrics, which needs root. Grant
    # scoped, passwordless sudo for JUST that binary via a sudoers drop-in so the
    # arena runner can sample energy non-interactively. Idempotent.
    if sudo -n /usr/bin/powermetrics -n1 -i100 --samplers cpu_power >/dev/null 2>&1; then
      log "powermetrics already runs under passwordless sudo"
    else
      info "installing scoped NOPASSWD sudoers rule for /usr/bin/powermetrics"
      run "printf '%s ALL=(ALL) NOPASSWD: /usr/bin/powermetrics\\n' \"$(id -un)\" | $SUDO tee /etc/sudoers.d/perfarena-powermetrics >/dev/null" \
        && run "$SUDO chmod 440 /etc/sudoers.d/perfarena-powermetrics"
      if [ "$DRY_RUN" != 1 ]; then
        sudo -n /usr/bin/powermetrics -n1 -i100 --samplers cpu_power >/dev/null 2>&1 \
          && log "powermetrics passwordless sudo configured" \
          || warn "sudoers rule written but 'sudo -n powermetrics' still fails — check /etc/sudoers.d/perfarena-powermetrics"
      fi
    fi
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
      'php --version' 'ruby --version' 'go version' 'rustc --version' 'cargo --version' \
      'kotlinc -version' 'scalac -version' 'swiftc --version' 'dart --version'; do
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

Kotlin, Scala, Swift and Dart are symlinked into /usr/local/bin (already on PATH),
so the runner's subprocesses find kotlinc/scalac/swiftc/dart without extra setup.
Confirm all four appear in harness-host-versions.txt below; if kotlinc is still
"not found" for the runner, restart it so it inherits the updated PATH.

Launch the arena-local runner from the measurement venv so it can measure energy:

    "$VENV/bin/python" $REPO_ROOT/webapp/examples/harness_runner_server.py   # serves :8787

Verify a single case in any language from a cell directory, e.g.:

    cd $HARNESS_DIR/Rust/leetcode/<slug> && bash $HARNESS_DIR/selection/run_harness.sh rust 1.0 0

Toolchain versions were written to: $REPO_ROOT/harness-host-versions.txt
NEXT
