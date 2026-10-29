#!/usr/bin/env python3
"""Restart the harness runner on bicho with exactly the command, working
directory, and environment of the running one (its auth token included), so
that it loads the new harness files. Prints no environment value.

Run ON bicho:  python3 restart_runner.py
Refuses while the runner has a child process (a measurement in progress).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

LOG = os.path.expanduser("~/harness_runner.out")


def runner_pid() -> int | None:
    out = subprocess.run(["pgrep", "-f", "harness_runner_server.py"], capture_output=True, text=True).stdout
    pids = [int(p) for p in out.split() if int(p) != os.getpid()]
    return pids[0] if pids else None


def children(pid: int) -> list[str]:
    out = subprocess.run(["ps", "--ppid", str(pid), "-o", "pid=,args="], capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def health(port: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
            return json.load(r) if r.status == 200 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main() -> int:
    pid = runner_pid()
    if pid is None:
        sys.exit("no running harness_runner_server.py: start it the usual way")
    busy = children(pid)
    if busy:
        sys.exit(f"the runner is measuring ({len(busy)} child process); try again when it is idle")
    argv = open(f"/proc/{pid}/cmdline", "rb").read().rstrip(b"\0").split(b"\0")
    env = dict(kv.split(b"=", 1) for kv in open(f"/proc/{pid}/environ", "rb").read().split(b"\0") if b"=" in kv)
    cwd = os.readlink(f"/proc/{pid}/cwd")
    port = "8787"
    if b"--port" in argv:
        port = argv[argv.index(b"--port") + 1].decode()
    if (health(port) or {}).get("busy"):
        sys.exit("the runner reports busy (a measurement in progress); try again when it is idle")
    os.kill(pid, signal.SIGTERM)
    for _ in range(60):
        if not os.path.exists(f"/proc/{pid}"):
            break
        time.sleep(0.5)
    else:
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
    with open(LOG, "ab") as log:
        new = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(60):
        if health(port) is not None:
            print(f"runner restarted: pid {new.pid}, port {port}, log {LOG}")
            return 0
        time.sleep(1)
    sys.exit(f"the new runner (pid {new.pid}) does not answer /healthz; see {LOG}")


if __name__ == "__main__":
    sys.exit(main())
