from __future__ import annotations

import argparse
import multiprocessing
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Gold Signal Desk")
    parser.add_argument("--engine", action="store_true")
    parser.add_argument("--engine-stopped", action="store_true")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--preview")
    parser.add_argument("--page", type=int, default=0)
    args = parser.parse_args()
    if args.engine:
        from signaldesk.service import run_service
        run_service(args.mode)
        return 0
    if args.preview:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
    from signaldesk.desktop import run_desktop
    return run_desktop(args.mode, args.preview, args.page, args.engine_stopped)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
