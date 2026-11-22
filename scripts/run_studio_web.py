#!/usr/bin/env python3
"""CLI Launcher for Universal AI Video Studio Local WebUI.

Usage:
    python scripts/run_studio_web.py [--host 127.0.0.1] [--port 8050]
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.web.server import run_studio_server, get_default_port


def main():
    parser = argparse.ArgumentParser(description="Universal AI Video Studio Local WebUI")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="TCP port to listen on (default: auto-discover 8050-8090 or STUDIO_WEB_PORT)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug"],
        help="Log level",
    )

    args = parser.parse_args()

    try:
        run_studio_server(host=args.host, port=args.port, log_level=args.log_level)
    except KeyboardInterrupt:
        print("\nStudio WebUI shutdown gracefully.")
    except Exception as exc:
        print(f"\n[ERROR] Failed to start Studio WebUI: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
