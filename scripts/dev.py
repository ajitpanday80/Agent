"""Run the Flask dashboard and LiveKit agent worker for local development."""
from __future__ import annotations

import os
import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the helpdesk dashboard and LiveKit agent.")
    parser.add_argument("--agent-mode", choices=("dev", "start"), default="dev",
                        help="Use dev for browser testing or start for production-dispatch phone testing.")
    args = parser.parse_args()
    env = os.environ.copy()
    children = [
        subprocess.Popen([sys.executable, "-m", "msp_agent.web"], cwd=ROOT, env=env),
        subprocess.Popen([sys.executable, "-m", "msp_agent.agent", args.agent_mode], cwd=ROOT, env=env),
    ]
    print("Dashboard: http://127.0.0.1:5000", flush=True)
    print(f"LiveKit agent '{os.getenv('LIVEKIT_AGENT_NAME', 'msp-helpdesk')}' starting in {args.agent_mode} mode.", flush=True)
    if args.agent_mode == "dev":
        print("Open your project's Agent Console in LiveKit Cloud to connect.", flush=True)
    else:
        print("Agent is registering in the production deployment for LiveKit dispatch rules.", flush=True)
    try:
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
