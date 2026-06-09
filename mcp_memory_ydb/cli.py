"""
Entry point for mcp-memory-ydb.

The startup sequence is strictly fail-fast:
  1. Config.from_env()  — validate configuration (one readable error).
  2. build_memory(cfg)  — connect to YDB + probe the LLM/embeddings.
  3. server.run()       — only now start listening on MCP.

Any error in steps 1–2 terminates the process with a readable message and exit
code 1, instead of hanging or entering the MCP handshake half-initialized.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("mcp_memory_ydb")


def _setup_logging() -> None:
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
    log.propagate = False


def _load_env() -> None:
    """Loads .mcp-memory-ydb.env from the current directory, then from ~/.mcp-memory-ydb.env."""
    for candidate in (Path(".mcp-memory-ydb.env"), Path.home() / ".mcp-memory-ydb.env"):
        if candidate.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(candidate)
                log.info("loaded %s", candidate)
            except ImportError:
                pass
            return


def main() -> None:
    _setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from .setup import run_setup

        run_setup()
        return

    # Cross-platform directory for mem0's working files.
    os.environ.setdefault("MEM0_DIR", str(Path(tempfile.gettempdir()) / ".mem0"))

    _load_env()

    from .config import Config, ConfigError
    from .providers import ProviderError, build_memory
    from .server import MemoryMCPServer

    try:
        cfg = Config.from_env()
    except ConfigError as e:
        print(f"\nCONFIGURATION ERROR:\n{e}\n", file=sys.stderr)
        sys.exit(1)

    try:
        memory = build_memory(cfg)
    except (ProviderError, ConfigError) as e:
        log.error("Startup aborted: %s", e)
        sys.exit(1)

    MemoryMCPServer(memory=memory, config=cfg).run()
