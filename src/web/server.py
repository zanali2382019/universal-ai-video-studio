"""Studio WebUI Server Runner with Dynamic Port Discovery.

Ensures reliable binding on 127.0.0.1 and automatic fallback if port 8050 is occupied.
"""

import os
import socket
from typing import Optional
import uvicorn

from src.core.logger import get_logger
from src.web.app import create_app

logger = get_logger("studio.web.server")

DEFAULT_PORT = 8050
DEFAULT_MAX_PORT = 8090


def get_default_port() -> int:
    """Resolve default port using STUDIO_WEB_PORT env var or fallback to 8050."""
    env_port = os.getenv("STUDIO_WEB_PORT")
    if env_port and env_port.strip():
        try:
            return int(env_port.strip())
        except ValueError:
            logger.warning(
                "Invalid STUDIO_WEB_PORT '%s', falling back to default %d.",
                env_port,
                DEFAULT_PORT,
            )
    return DEFAULT_PORT


def find_available_port(
    host: str = "127.0.0.1",
    start_port: Optional[int] = None,
    max_port: Optional[int] = None,
) -> int:
    """Find the first available TCP port within the given range."""
    effective_start = start_port if start_port is not None else get_default_port()
    effective_max = max_port if max_port is not None else DEFAULT_MAX_PORT

    if effective_max < effective_start:
        effective_max = effective_start + (DEFAULT_MAX_PORT - DEFAULT_PORT)

    for port in range(effective_start, effective_max + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No available port found on {host} between {effective_start} and {effective_max}."
    )


def run_studio_server(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    log_level: str = "info",
) -> None:
    """Launch the local Studio WebUI server.

    Port resolution priority:
    1. Explicit `port` argument (e.g. from CLI --port).
    2. STUDIO_WEB_PORT environment variable.
    3. Default 8050 (with automatic discovery in range 8050-8090).
    """
    # Enforce loopback binding for security (ADR-013)
    if host not in ("127.0.0.1", "localhost"):
        logger.warning(
            "Overriding host '%s' to '127.0.0.1' for strict local isolation (ADR-013).",
            host,
        )
        host = "127.0.0.1"

    if port is not None:
        target_port = port
    else:
        start_p = get_default_port()
        max_p = max(DEFAULT_MAX_PORT, start_p + (DEFAULT_MAX_PORT - DEFAULT_PORT))
        target_port = find_available_port(host=host, start_port=start_p, max_port=max_p)

    app = create_app()

    logger.info("Universal AI Video Studio WebUI starting...")
    logger.info("Local WebUI URL: http://%s:%d", host, target_port)
    print(f"\n=======================================================")
    print(f" Universal AI Video Studio — Local WebUI")
    print(f" URL: http://{host}:{target_port}")
    print(f" Health: http://{host}:{target_port}/health")
    print(f" Press Ctrl+C to stop the server.")
    print(f"=======================================================\n")

    uvicorn.run(
        app,
        host=host,
        port=target_port,
        log_level=log_level,
    )


if __name__ == "__main__":
    run_studio_server()
