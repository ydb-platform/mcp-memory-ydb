"""
Configuration for mcp-memory-ydb, read from environment variables.

All validation happens in one place — Config.from_env() — which fails with a
single, readable error (ConfigError) listing every missing variable at once.
Library code never calls sys.exit; only cli.py does.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Configuration is incomplete or invalid. The message is ready to show the user."""


# mem0's fact-extraction prompt is English, so by default it stores facts in
# English even when the user writes another language. mem0 injects
# custom_instructions into that prompt as a top-priority section, which lets you
# steer extraction — most usefully, the stored language.
#
# We ship NO default instruction (empty), keeping the server language-neutral and
# mem0's well-tuned prompt untouched out of the box. To make facts keep the user's
# language, set MEMORY_FACT_INSTRUCTIONS. One empirical caveat: the model honors a
# language directive written IN the target language far more reliably than an
# English "keep the source language" note (which it tends to ignore). So phrase it
# in your own primary language, e.g. for Russian:
#
#   MEMORY_FACT_INSTRUCTIONS=Сохраняй каждый факт на языке исходного сообщения, не переводи.
DEFAULT_FACT_INSTRUCTIONS = ""


def _default_namespace() -> str:
    # The namespace partitions memory (e.g. one per project or agent). It is a
    # filter label, not an identity or a security boundary — see the README.
    return os.getenv("MEMORY_NAMESPACE", "").strip() or "default"


def _parse_endpoint(endpoint: str) -> tuple[str, int, bool]:
    """grpcs://host:port -> (host, port, secure). secure=True for grpcs."""
    proto, sep, hostport = endpoint.partition("://")
    if not sep:  # no scheme: the whole string is host[:port]
        proto, hostport = "", endpoint
    secure = proto == "grpcs"
    host, sep, port_str = hostport.rpartition(":")
    if not sep:  # no port given
        host, port_str = hostport, ""
    if not host:
        raise ConfigError(f"YDB_ENDPOINT={endpoint!r}: could not parse host.")
    if port_str and not port_str.isdigit():
        raise ConfigError(f"YDB_ENDPOINT={endpoint!r}: port {port_str!r} is not a number.")
    port = int(port_str) if port_str else 2135
    return host, port, secure


@dataclass
class Config:
    # LLM (mem0 fact extraction)
    llm_base_url: str
    llm_model: str
    llm_api_key: str

    # Embeddings (vector search)
    emb_base_url: str
    emb_model: str
    emb_api_key: str
    emb_is_yandex: bool          # emb://<folder_id>/<model>/latest
    emb_folder_id: str           # extracted from the emb:// URI, otherwise ""

    # YDB
    ydb_endpoint: str
    ydb_database: str
    ydb_host: str
    ydb_port: int
    ydb_secure: bool
    ydb_sa_key_file: str | None
    ydb_sa_key_json: str | None  # raw JSON key as a string

    # Memory / timeouts
    namespace: str               # partitions memory; a filter label, not identity
    fact_instructions: str       # injected into mem0's extraction prompt (e.g. language)
    threshold: float
    memory_timeout: float        # per-request timeout for mem0 operations
    probe_timeout: float         # timeout for the startup LLM/embeddings probe
    ydb_timeout: float           # YDB discovery_request_timeout

    @classmethod
    def from_env(cls) -> "Config":
        missing: list[str] = []

        def req(key: str) -> str:
            val = os.getenv(key, "").strip()
            if not val:
                missing.append(key)
            return val

        llm_base_url = req("LLM_BASE_URL")
        llm_model = req("LLM_MODEL")
        llm_api_key = req("LLM_API_KEY")

        emb_model = req("EMBEDDINGS_MODEL")
        # Embeddings base_url/api_key default to the LLM ones.
        emb_base_url = os.getenv("EMBEDDINGS_BASE_URL", "").strip() or llm_base_url
        emb_api_key = os.getenv("EMBEDDINGS_API_KEY", "").strip() or llm_api_key

        ydb_endpoint = req("YDB_ENDPOINT")
        ydb_database = req("YDB_DATABASE")

        # Parse the emb:// URI (Yandex) — folder_id comes from the URI itself.
        emb_is_yandex = emb_model.startswith("emb://")
        emb_folder_id = ""
        if emb_is_yandex:
            parts = emb_model.split("/")  # ['emb:', '', folder_id, model, ...]
            emb_folder_id = parts[2] if len(parts) > 2 else ""
            if not emb_folder_id:
                raise ConfigError(
                    f"EMBEDDINGS_MODEL={emb_model!r}: could not extract folder_id from the emb:// URI. "
                    f"Expected emb://<folder_id>/<model>/latest."
                )

        if missing:
            raise ConfigError(
                "Missing required environment variables:\n  "
                + "\n  ".join(missing)
                + "\n\nRun 'mcp-memory-ydb setup' for interactive configuration, "
                "or fill in .mcp-memory-ydb.env."
            )

        host, port, secure = _parse_endpoint(ydb_endpoint)

        def fnum(key: str, default: str) -> float:
            raw = os.getenv(key, default).strip() or default
            try:
                return float(raw)
            except ValueError:
                raise ConfigError(f"{key}={raw!r}: expected a number.")

        memory_timeout = fnum("MEMORY_TIMEOUT", "30")

        return cls(
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            emb_base_url=emb_base_url,
            emb_model=emb_model,
            emb_api_key=emb_api_key,
            emb_is_yandex=emb_is_yandex,
            emb_folder_id=emb_folder_id,
            ydb_endpoint=ydb_endpoint,
            ydb_database=ydb_database,
            ydb_host=host,
            ydb_port=port,
            ydb_secure=secure,
            ydb_sa_key_file=os.getenv("YDB_SA_KEY_FILE", "").strip() or None,
            ydb_sa_key_json=os.getenv("YDB_SA_KEY", "").strip() or None,
            namespace=_default_namespace(),
            fact_instructions=os.getenv("MEMORY_FACT_INSTRUCTIONS", "").strip() or DEFAULT_FACT_INSTRUCTIONS,
            threshold=fnum("MEMORY_THRESHOLD", "0.15"),
            memory_timeout=memory_timeout,
            probe_timeout=fnum("PROBE_TIMEOUT", str(memory_timeout)),
            ydb_timeout=fnum("YDB_TIMEOUT", str(memory_timeout)),
        )
