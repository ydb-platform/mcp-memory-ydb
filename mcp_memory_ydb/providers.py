"""
Component assembly: embeddings, the YDB vector store, and the mem0 Memory.

Principles:
  • YDB is reached through a single connection owned by langchain_ydb.YDB, which
    connects and waits for readiness itself (ydb_dbapi.connect ->
    driver.wait(fail_fast=True)), raising immediately if the endpoint is down.
  • discovery_request_timeout is passed to YDB via driver_config_kwargs to bound
    that readiness wait; without it the SDK would wait up to 600s on an
    unreachable endpoint.
  • The LLM and embeddings are validated with a cheap probe call BEFORE mem0 is
    built (short timeout, max_retries=0), so a misconfiguration fails clearly and
    immediately instead of hanging inside mem0 on the first memory_save.
"""
from __future__ import annotations

import json
import logging
import os

from .config import Config

log = logging.getLogger("mcp_memory_ydb")


class ProviderError(Exception):
    """The LLM, embeddings, or YDB are unreachable or misconfigured."""


# ── YDB ──────────────────────────────────────────────────────────────────────

def build_ydb_credentials(cfg: Config):
    """ydb.Credentials from a service-account key, or None (anonymous)."""
    import ydb

    if cfg.ydb_sa_key_file:
        # Expand ~ and validate up front: ServiceAccountCredentials.from_file does
        # NOT expand ~ and does NOT fail on a missing file — it silently builds
        # broken credentials that later surface as a confusing PERMISSION_DENIED.
        key_path = os.path.expanduser(cfg.ydb_sa_key_file)
        if not os.path.isfile(key_path):
            raise ProviderError(
                f"YDB_SA_KEY_FILE={cfg.ydb_sa_key_file!r}: file not found "
                f"(resolved to {key_path!r}). Point it at the service-account JSON key."
            )
        try:
            return ydb.iam.ServiceAccountCredentials.from_file(key_path)
        except OSError as e:
            raise ProviderError(
                f"YDB_SA_KEY_FILE={key_path!r}: could not read the file — {e}."
            ) from e
        except (ValueError, KeyError) as e:
            raise ProviderError(f"YDB_SA_KEY_FILE={key_path!r}: invalid key — {e}") from e
    if cfg.ydb_sa_key_json:
        try:
            payload = json.loads(cfg.ydb_sa_key_json)
        except json.JSONDecodeError as e:
            raise ProviderError(f"YDB_SA_KEY: invalid JSON — {e}") from e
        try:
            return ydb.iam.ServiceAccountCredentials(**payload)
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError(f"YDB_SA_KEY: invalid key — {e}") from e
    return None


def build_vectorstore(cfg: Config, embeddings):
    """
    The YDB vector store. YDB performs the connection and wait_ready(fail_fast)
    itself; on an unreachable endpoint it raises ydb_dbapi.InterfaceError, which
    we wrap in ProviderError for uniform diagnostics.
    """
    from langchain_ydb.vectorstores import YDB, YDBSearchStrategy, YDBSettings

    settings = YDBSettings(
        host=cfg.ydb_host,
        port=cfg.ydb_port,
        database=cfg.ydb_database,
        table="memories",
        strategy=YDBSearchStrategy.COSINE_SIMILARITY,
        secure=cfg.ydb_secure,
        credentials=build_ydb_credentials(cfg),
    )
    log.info("connect YDB %s:%d db=%s secure=%s", cfg.ydb_host, cfg.ydb_port,
             cfg.ydb_database, cfg.ydb_secure)
    try:
        return YDB(
            embeddings,
            config=settings,
            driver_config_kwargs={"discovery_request_timeout": int(cfg.ydb_timeout)},
        )
    except Exception as e:
        raise ProviderError(
            f"Could not connect to YDB ({cfg.ydb_endpoint}): {e}"
        ) from e


# ── Embeddings ───────────────────────────────────────────────────────────────

def build_embeddings(cfg: Config):
    if cfg.emb_is_yandex:
        log.info("init embeddings: yandex / %s", cfg.emb_model)
        from langchain_community.embeddings.yandex import YandexGPTEmbeddings

        return YandexGPTEmbeddings(
            api_key=cfg.emb_api_key,
            folder_id=cfg.emb_folder_id,
            model_uri=cfg.emb_model,
            doc_model_uri=cfg.emb_model,
        )

    log.info("init embeddings: openai-compatible / %s @ %s", cfg.emb_model, cfg.emb_base_url)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=cfg.emb_model,
        api_key=cfg.emb_api_key,
        base_url=cfg.emb_base_url,
    )


# ── Startup probes (fail-fast) ─────────────────────────────────────────────────

def probe_embeddings(cfg: Config, embeddings) -> None:
    """Embed a short string — catches a wrong embeddings URL/key/model."""
    log.info("probe embeddings…")
    try:
        if cfg.emb_is_yandex:
            embeddings.embed_query("ping")
        else:
            # A direct client with a hard timeout — langchain's OpenAIEmbeddings
            # does not let us set a short per-call timeout.
            import openai

            client = openai.OpenAI(
                api_key=cfg.emb_api_key,
                base_url=cfg.emb_base_url,
                timeout=cfg.probe_timeout,
                max_retries=0,
            )
            client.embeddings.create(model=cfg.emb_model, input="ping")
    except Exception as e:
        raise ProviderError(
            f"Embeddings unavailable (model={cfg.emb_model} @ {cfg.emb_base_url}): {e}"
        ) from e
    log.info("probe embeddings ok")


def probe_llm(cfg: Config) -> None:
    """A minimal chat request — catches a wrong LLM URL/key/model before mem0 starts."""
    log.info("probe llm…")
    import openai

    client = openai.OpenAI(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        timeout=cfg.probe_timeout,
        max_retries=0,
    )
    try:
        client.chat.completions.create(
            model=cfg.llm_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as e:
        raise ProviderError(
            f"LLM unavailable (model={cfg.llm_model} @ {cfg.llm_base_url}): {e}"
        ) from e
    log.info("probe llm ok")


# ── mem0 Memory ──────────────────────────────────────────────────────────────

def build_memory(cfg: Config):
    """
    Full assembly in fail-fast order: embeddings -> YDB -> probe LLM/emb -> mem0.
    Any error surfaces here, before the MCP server starts.
    """
    from mem0 import Memory

    embeddings = build_embeddings(cfg)
    vectorstore = build_vectorstore(cfg, embeddings)

    probe_embeddings(cfg, embeddings)
    probe_llm(cfg)

    log.info("init mem0 → YDB table=memories")
    mem0_config = {
        "vector_store": {
            "provider": "langchain",
            "config": {"client": vectorstore, "collection_name": "memories"},
        },
        "embedder": {"provider": "langchain", "config": {"model": embeddings}},
        "llm": {"provider": "openai", "config": {
            "model": cfg.llm_model,
            "api_key": cfg.llm_api_key,
            "openai_base_url": cfg.llm_base_url,
        }},
    }
    # Steer mem0's fact extraction (e.g. the stored language) only when configured.
    # Empty by default — see MEMORY_FACT_INSTRUCTIONS / DEFAULT_FACT_INSTRUCTIONS in
    # config.py. When empty, mem0's own well-tuned prompt is left untouched.
    if cfg.fact_instructions:
        mem0_config["custom_instructions"] = cfg.fact_instructions
        log.info("custom fact-extraction instructions: enabled")

    memory = Memory.from_config(mem0_config)
    log.info("mem0 ready")
    return memory
