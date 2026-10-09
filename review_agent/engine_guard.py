"""Independent deadline survives a backend crash (POSIX process groups / Windows Job Objects)."""
from __future__ import annotations
import os
import json
import signal
import subprocess
import sys
import time

# Exit code used when a kill was requested but the child stayed unreaped.
# Distinct from any real child status so callers can tell "gave up waiting"
# apart from "child finished with this code".
GUARD_GIVE_UP = 97


def guard_python() -> str:
    """Run the stdlib-only guard directly, without Windows venv's extra process.

    CPython's Windows venv redirector changes the actual parent PID and makes
    Popen refer to the redirector, not the process that owns the Job handle.
    Workload children still use the caller's venv interpreter and dependencies.
    """
    return getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable


def main() -> int:
    timeout = float(sys.argv[1])
    parent = int(sys.argv[2])
    if os.getppid() != parent:
        return 1
    job = None
    if os.name == "nt":
        # Script entry has this directory on sys.path, even with cwd outside repo.
        from windows_process import GuardJob
        job = GuardJob(parent)

    def parent_alive():
        return job.parent_alive() if job else os.getppid() == parent

    def kill_tree():
        if job:
            job.kill()
        else:
            os.killpg(os.getpgrp(), signal.SIGKILL)

    if not parent_alive():
        return 1
    reap_group = sys.argv[3] == "--bridge-reap-group"
    child = subprocess.Popen(sys.argv[4:] if reap_group else sys.argv[3:])
    deadline = time.monotonic() + timeout if timeout > 0 else float("inf")
    killed_at = None
    while child.poll() is None:
        if time.monotonic() > deadline or not parent_alive():
            # A kill request is not a kill. Windows Job Object termination can
            # fail, and the child can stay unreaped forever -- this loop only
            # tests poll(), so without an escalation it would spin at 5Hz
            # forever and the caller would wait with it. Measured: a report sat
            # in poll() for 34 minutes with every worker already dead.
            #
            # Escalate, then leave. The caller already bounds its own wait and
            # reaps this process; holding the handle open longer only makes
            # that harder.
            if killed_at is None:
                killed_at = time.monotonic()
                kill_tree()
            elif time.monotonic() - killed_at > 5:
                return GUARD_GIVE_UP
        time.sleep(.2)
    if reap_group:
        # A private bridge result is followed by the child's actual exit status.
        # The owner waits for our death before accepting it. Always reap the
        # entire group, even if EOF made Node exit before we noticed parent death.
        try:
            print(json.dumps({"type": "bridge_exit", "code": child.returncode}), flush=True)
        finally:
            kill_tree()
    if not parent_alive():
        kill_tree()
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
