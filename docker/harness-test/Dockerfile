# PerfArena harness TEST container — a local replica of the bicho runner host.
#
# Purpose: reproduce the arena-local harness environment (all 14 language
# toolchains + the codecarbon measurement venv) on your laptop, so you can run
# `make validate` / `make measure`, the per-language harnesses, and the 8787
# runner WITHOUT deploying to bicho. This is for TROUBLESHOOTING, not for
# publishable energy numbers: a container cannot read RAPL package counters the
# way bare metal does, so codecarbon falls back to a TDP estimate here. Runtime,
# memory, correctness, compile/run behaviour, and harness logic all reproduce
# faithfully — only the absolute energy value is an estimate.
#
# The repo is MOUNTED at /work at run time (see run.sh), never COPY'd, so your
# local edits to the harnesses take effect immediately with no rebuild.
#
# Mirrors setup-harness-host.sh (the bare-metal bicho setup) toolchain-for-
# toolchain. Core languages (C, C++, C#, Go, Java, JS, PHP, Python, Ruby, Rust,
# TypeScript) are guaranteed; the JVM/native newcomers (Kotlin, Scala, Swift)
# are best-effort so a flaky download never fails the whole image.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- 1. base build tools -----------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git make pkg-config build-essential \
        unzip zip xz-utils gnupg \
    && rm -rf /var/lib/apt/lists/*

# ---- 2. C / C++ (Boost for pidigits, GMP for gmpy2, PCRE2 for regex-redux) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ clang libboost-dev libgmp-dev libpcre2-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- 3. Java 21 (also the JRE that Kotlin + Scala run on) --------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# ---- 4. Node.js 20 (NodeSource) + TypeScript 5 -------------------------------
# apt's Node is too old to parse the JS/TS harnesses; match bicho's Node 20.
# TypeScript pinned to CLASSIC 5.x (the `next`/tsgo preview breaks tsc here).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g typescript@5 \
    && rm -rf /var/lib/apt/lists/*

# ---- 5. PHP (+ GMP for pidigits), Ruby, Go ----------------------------------
# short_open_tag: several CLBG PHP references open with `<?`; without it PHP
# echoes the source instead of running the program.
RUN apt-get update && apt-get install -y --no-install-recommends \
        php-cli php-gmp ruby golang-go \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'short_open_tag=On\n' > "$(php -i | sed -n 's/^Scan this dir for additional .ini files => //p' | head -1)/99-perfarena.ini"

# ---- 6. Python + measurement venv (codecarbon + psutil + gmpy2) --------------
# The venv becomes the default `python3`/`python` (PATH below), so the Makefiles'
# `python3 -m perfarena.runners.codecarbon_runner` and the harness scripts all
# use an interpreter that has codecarbon/psutil. gmpy2 is for CLBG pidigits.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir codecarbon psutil gmpy2 pyyaml
ENV PATH="/opt/venv/bin:${PATH}"

# ---- 7. Rust (rustup, stable) ------------------------------------------------
ENV CARGO_HOME=/opt/cargo RUSTUP_HOME=/opt/rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path
ENV PATH="/opt/cargo/bin:${PATH}"

# ---- 8. .NET SDK 10.0 (harness csproj targets net10.0) -----------------------
ENV DOTNET_ROOT=/opt/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 10.0 --install-dir /opt/dotnet \
    && ln -sf /opt/dotnet/dotnet /usr/local/bin/dotnet \
    && rm -f /tmp/dotnet-install.sh
ENV PATH="/opt/dotnet:${PATH}"

# ---- 9. Kotlin (best-effort) -------------------------------------------------
ARG KOTLIN_VERSION=2.1.20
RUN ( curl -fsSL -o /tmp/kotlin.zip \
        "https://github.com/JetBrains/kotlin/releases/download/v${KOTLIN_VERSION}/kotlin-compiler-${KOTLIN_VERSION}.zip" \
      && unzip -q /tmp/kotlin.zip -d /opt \
      && ln -sf /opt/kotlinc/bin/kotlinc /usr/local/bin/kotlinc \
      && ln -sf /opt/kotlinc/bin/kotlin  /usr/local/bin/kotlin \
      && rm -f /tmp/kotlin.zip ) \
    || echo "WARNING: Kotlin install failed — kotlin cells will not run in this image"

# ---- 10. Scala (coursier, best-effort) ---------------------------------------
RUN ( curl -fsSL https://github.com/coursier/launchers/raw/master/cs-x86_64-pc-linux.gz \
        | gzip -d > /usr/local/bin/cs \
      && chmod +x /usr/local/bin/cs \
      && cs install --install-dir /usr/local/bin scala scalac ) \
    || echo "WARNING: Scala install failed — scala cells will not run in this image"

# ---- 11. Swift (swiftly, best-effort) ----------------------------------------
# Swift's link-time deps + the toolchain. swiftly is finicky under Docker; if it
# fails the image still builds and every other language works.
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils libc6-dev libcurl4-openssl-dev libedit2 libncurses-dev \
        libpython3-dev libxml2-dev libz3-dev tzdata zlib1g-dev \
    && rm -rf /var/lib/apt/lists/* \
    ; ( curl -fsSL -o /tmp/swiftly.tar.gz https://download.swift.org/swiftly/linux/swiftly-x86_64.tar.gz \
        && mkdir -p /tmp/swiftly-x && tar -xzf /tmp/swiftly.tar.gz -C /tmp/swiftly-x \
        && SWIFTLY_ASSUME_YES=1 SWIFTLY_HOME_DIR=/opt/swiftly SWIFTLY_BIN_DIR=/opt/swiftly/bin \
             /tmp/swiftly-x/swiftly init --assume-yes --quiet-shell-followup \
        && SWIFTLY_HOME_DIR=/opt/swiftly SWIFTLY_BIN_DIR=/opt/swiftly/bin \
             /opt/swiftly/bin/swiftly install latest --assume-yes \
        && ln -sf /opt/swiftly/bin/swiftc /usr/local/bin/swiftc ) \
    || echo "WARNING: Swift install failed — swift cells will not run in this image"

# ---- sudo passthrough shim ---------------------------------------------------
# perfarena.mk's Linux `measure` target prefixes `sudo` (RAPL MSR access on bare
# metal). This container runs as root and measures with codecarbon (no
# privileged access needed), so a passthrough `sudo` that just execs its args
# avoids "sudo: not found" without a real sudo/tty/hostname setup.
RUN printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo && chmod +x /usr/local/bin/sudo

# ---- runtime environment -----------------------------------------------------
# codecarbon (no RAPL in a container) is the energy backend; PYTHONPATH points at
# the MOUNTED repo so `perfarena.runners.codecarbon_runner` imports; the repo
# lives at /work (mounted by run.sh).
ENV PERFARENA_PROFILER=codecarbon \
    PYTHONPATH=/work/Energy-Languages \
    PERFARENA_MEASURE=10 \
    PERFARENA_WARMUP=5 \
    PERFARENA_IDLE_S=2
WORKDIR /work

# Record the toolchain versions into the image (mirrors harness-host-versions).
RUN { \
      echo "=== PerfArena harness-test image toolchains ==="; \
      for p in 'gcc --version' 'g++ --version' 'python3 --version' 'node --version' \
               'tsc --version' 'javac -version' 'dotnet --version' 'php --version' \
               'ruby --version' 'go version' 'rustc --version' \
               'kotlinc -version' 'scalac -version' 'swiftc --version'; do \
        printf '%-10s ' "${p%% *}:"; eval "$p" 2>&1 | head -1 || echo MISSING; \
      done; \
      /opt/venv/bin/python -c 'import codecarbon,psutil;print("codecarbon",codecarbon.__version__,"psutil",psutil.__version__)'; \
    } > /image-toolchains.txt 2>&1 || true

CMD ["bash"]
