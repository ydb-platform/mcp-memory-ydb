"""
Long-term memory MCP server backed by YDB Serverless.

Built on ydb-mcp: disables the SQL tools and adds four memory tools —
memory_search / memory_save / memory_delete / memory_update. Works with any
OpenAI-compatible LLM/embeddings provider (OpenAI, Ollama, Yandex Cloud, …).

Environment variables
──────────────────────────────────────────────────────────────────────────────
  LLM_BASE_URL          Provider base URL                                [req]
  LLM_MODEL             gpt-4o-mini | gpt://<folder_id>/yandexgpt/latest [req]
  LLM_API_KEY           API key                                          [req]

  EMBEDDINGS_MODEL      Embeddings model                                 [req]
                          Yandex: emb://<folder_id>/text-search-query/latest
  EMBEDDINGS_BASE_URL   Embeddings base URL (default = LLM_BASE_URL)      [opt]
  EMBEDDINGS_API_KEY    Embeddings API key (default = LLM_API_KEY)        [opt]

  YDB_ENDPOINT          grpcs://ydb.serverless.yandexcloud.net:2135      [req]
  YDB_DATABASE          /ru-central1/<folder>/<db>                       [req]
  YDB_SA_KEY_FILE       Path to the service-account JSON key             [opt]
  YDB_SA_KEY            Service-account key as a JSON string             [opt]

  MEMORY_NAMESPACE      Memory partition, e.g. per project (default "default")  [opt]
  MEMORY_FACT_INSTRUCTIONS  Steers mem0 extraction (e.g. stored language); empty = mem0 default  [opt]
  MEMORY_THRESHOLD      Minimum cosine score for search (default 0.15)   [opt]
  MEMORY_TIMEOUT        Memory operation timeout, seconds (default 30)   [opt]
  PROBE_TIMEOUT         Startup LLM/embeddings probe timeout (default = MEMORY_TIMEOUT)
  YDB_TIMEOUT           YDB discovery_request_timeout (default = MEMORY_TIMEOUT)

.mcp-memory-ydb.env is looked up in the current directory first, then ~/.mcp-memory-ydb.env.

Usage
──────────────────────────────────────────────────────────────────────────────
  mcp-memory-ydb setup    # interactive setup → .mcp-memory-ydb.env
  mcp-memory-ydb          # run (config from .mcp-memory-ydb.env)
"""
from .cli import main

__all__ = ["main"]
