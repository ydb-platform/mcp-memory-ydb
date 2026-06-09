# mcp-memory-ydb

Long-term memory MCP server for AI agents. It uses [mem0](https://github.com/mem0ai/mem0)
to turn raw text into deduplicated facts and [YDB Serverless](https://yandex.cloud/en/services/ydb)
as the vector store behind them.

Built on top of [`ydb-mcp`](https://github.com/ydb-platform/ydb-mcp) (Yandex's MCP
server for YDB): the generic SQL tools are switched off and replaced with two
memory tools. Works with any OpenAI-compatible LLM/embeddings provider (OpenAI,
Ollama, Yandex Cloud, …).

## Tools

| Tool | Description |
|------|-------------|
| `memory_search(query, limit)` | Semantic search across the namespace's memory |
| `memory_save(text)` | Save a fact — mem0 extracts and deduplicates it |

## How it works

`memory_save` does not just store the text you give it. It hands that text to
**mem0**, which calls your LLM to extract the salient facts, deduplicate them
against what is already stored, and resolve contradictions (so "I use Postgres"
later overwrites "I use MySQL" instead of piling up). Each resulting fact is
embedded and written to YDB.

**Language of stored facts.** mem0's extraction prompt is English, so out of the
box facts are stored in English even if the user writes another language. To keep
the user's own language, set `MEMORY_FACT_INSTRUCTIONS` to a short instruction —
and write it **in that language**: empirically the model honors a same-language
directive far more reliably than an English "keep the source language" note. For
Russian, for example:

```
MEMORY_FACT_INSTRUCTIONS=Сохраняй каждый факт на языке исходного сообщения, не переводи.
```

The server ships no default instruction, leaving mem0's well-tuned prompt
untouched unless you opt in.

`memory_search` embeds the query and runs a cosine-similarity search over those
facts in YDB, returning the closest matches above `MEMORY_THRESHOLD`.

```
text ─▶ mem0 (LLM: extract + dedup) ─▶ embeddings ─▶ YDB vector store
                                                          │
query ─▶ embeddings ─▶ cosine search ◀────────────────────┘
```

- **mem0** — fact extraction, deduplication, contradiction resolution. This is a
  required dependency, not an optional layer; it is what makes the memory smart
  rather than an append-only log.
- **[langchain-ydb](https://github.com/ydb-platform/langchain-ydb)** — the vector
  store; embeds facts and runs similarity search in YDB.
- **YDB Serverless** — the database that holds the vectors and metadata.

## Memory namespaces

All memory lives under a single **namespace**, set once via `MEMORY_NAMESPACE`
(default `default`). A namespace is a partition label — use a different one to
keep, say, work and personal memory separate, or one per project. Point a second
server instance at a different namespace and the two never see each other's facts.

The agent does **not** choose the namespace: it is fixed per server process, so
the agent calls `memory_save(text)` / `memory_search(query)` and cannot read or
write the wrong partition by mistake. To switch namespaces, run another instance
with its own config.

> **A namespace is not a security boundary.** It is a query filter, not row-level
> security or authentication. Anything with access to the YDB database can read
> every namespace. Treat the server as single-user-trusted: run your own
> instance against your own database. It is not a multi-tenant backend — that
> would require an authenticated transport that maps each caller to a namespace
> the agent cannot forge.

## Quick start

### 1. Configure

```bash
# From PyPI (once published):
uvx mcp-memory-ydb setup

# From source — option 1: uvx (closest to production, isolated environment):
git clone https://github.com/ydb-platform/mcp-memory-ydb
cd mcp-memory-ydb
uvx --from . mcp-memory-ydb setup

# From source — option 2: venv (convenient for development):
python -m venv .venv && source .venv/bin/activate
pip install -e .
mcp-memory-ydb setup
```

The wizard creates `.mcp-memory-ydb.env` and prints ready-to-use commands for connecting to your agent.

### 2. Connect to your agent

**Claude Code:**
```bash
# From PyPI:
claude mcp add --scope user memory-ydb -- uvx mcp-memory-ydb

# From source via uvx (production approach):
claude mcp add --scope user memory-ydb -- uvx --from /path/to/mcp-memory-ydb mcp-memory-ydb

# From source via venv:
claude mcp add --scope user memory-ydb -- /path/to/.venv/bin/mcp-memory-ydb
```

**Cursor / VS Code** — add this to your MCP settings (the wizard prints a ready-to-paste version):
```json
{
  "mcpServers": {
    "memory-ydb": {
      "command": "uvx",
      "args": ["mcp-memory-ydb"]
    }
  }
}
```

The server reads its config from `~/.mcp-memory-ydb.env` automatically (the path `setup` saves to by default), so the MCP entry needs only the command — no `env` block required.
For source via uvx, add `"--from", "/path/to/mcp-memory-ydb"` before `"mcp-memory-ydb"` in `args`.
For venv, set `command` to `/path/to/.venv/bin/mcp-memory-ydb` and clear `args`.

### 3. Agent system prompt (usually optional)

The server ships **MCP instructions** that tell the agent to search memory before
answering and save after. Clients that honor server instructions (e.g. Claude
Code) pick this up automatically on connect — **no system prompt needed**.

For clients that ignore server instructions, add this to the agent's system
prompt as a fallback:

```
Before answering, call memory_search for relevant context about the user.
After answering, call memory_save if you learned something important.
Use these tools silently — do not announce or narrate searching or saving.
Save facts in the user's own language, without translating.
```

## How you use it

You never call the tools by hand. Once the server is connected (step 2 above),
the **agent** calls `memory_search` and `memory_save` on its own — guided by the
server's built-in instructions (or the fallback system prompt) — while you just
have a normal conversation. Memory works in the
background.

To confirm it works, test it through a conversation. The flow is the same in
Claude Code, Cursor, and VS Code:

1. **Verify the server is connected.**
   - Claude Code: run `/mcp` — `memory-ydb` should be listed with its two tools.
   - Cursor / VS Code: open the MCP settings panel — `memory-ydb` should show a
     connected status.
2. **Teach it a fact.** Say:
   *"Remember that my favorite language is Rust and I work in the Moscow timezone."*
   The agent calls `memory_save` (Claude Code shows the tool call inline).
3. **Recall it in a fresh conversation.** Start a new chat and ask:
   *"What's my favorite programming language?"*
   The agent calls `memory_search` and answers from memory.

If step 3 works in a brand-new conversation, the full save → store → search
round-trip across sessions is verified.

## Troubleshooting

The server fails fast with a single readable error. The two most common ones:

**`Could not connect to YDB … PERMISSION_DENIED`**
The service account cannot access the database. Check that:
- the SA has the **`ydb.editor`** role on the database's folder
  (`yc resource-manager folder add-access-binding <folder> --role ydb.editor --subject serviceAccount:<sa-id>`);
- `YDB_SA_KEY_FILE` points at the **right** key for that SA (a valid key for the
  *wrong* SA also yields `PERMISSION_DENIED`);
- `YDB_DATABASE` is the full path `/ru-central1/<folder>/<db>`.

**`LLM/Embeddings unavailable … CERTIFICATE_VERIFY_FAILED`**
Python cannot verify the provider's TLS certificate. This is almost always a
local trust-store issue, not a problem with the server. If you have a custom
`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` set in your environment, it may be a
narrow bundle missing the provider's CA. Either add the CA to that bundle, or
point the variable at the [certifi](https://pypi.org/project/certifi/) bundle
when launching the server:
```bash
SSL_CERT_FILE="$(python -m certifi)" mcp-memory-ydb
```
To confirm the cause, compare a request made with your bundle vs. certifi's —
if certifi works and yours does not, your bundle is missing the CA.

## Configuration

All settings live in `.mcp-memory-ydb.env`. Annotated template: [`.env.example`](.env.example).

The server validates its configuration, connects to YDB, and probes the LLM/embeddings
at startup. If something is unreachable or misconfigured, it **fails clearly within a
few seconds** instead of hanging.

### Environment variables

| Variable | Required | Description |
|----------|:---:|-------------|
| `LLM_BASE_URL` | yes | Provider base URL |
| `LLM_MODEL` | yes | OpenAI: `gpt-4o-mini`; Yandex: `gpt://<folder_id>/yandexgpt/latest`; Ollama: `llama3` |
| `LLM_API_KEY` | yes | API key |
| `EMBEDDINGS_MODEL` | yes | OpenAI: `text-embedding-3-small`; Yandex: `emb://<folder_id>/text-search-query/latest` |
| `EMBEDDINGS_BASE_URL` | | Embeddings base URL (default: `LLM_BASE_URL`) |
| `EMBEDDINGS_API_KEY` | | Embeddings API key (default: `LLM_API_KEY`) |
| `YDB_ENDPOINT` | yes | `grpcs://ydb.serverless.yandexcloud.net:2135` |
| `YDB_DATABASE` | yes | `/ru-central1/<folder>/<db>` |
| `YDB_SA_KEY_FILE` | | Path to the service-account JSON key |
| `YDB_SA_KEY` | | Service-account key as a JSON string (alternative to `YDB_SA_KEY_FILE`) |
| `MEMORY_NAMESPACE` | | Memory partition, e.g. per project (default: `default`) |
| `MEMORY_FACT_INSTRUCTIONS` | | Steers mem0 extraction (e.g. stored language); empty = mem0 default. Write it in your own language. |
| `MEMORY_THRESHOLD` | | Minimum cosine score (default: `0.15`) |
| `MEMORY_TIMEOUT` | | Memory operation timeout, seconds (default: `30`) |
| `PROBE_TIMEOUT` | | Startup LLM/embeddings probe timeout (default: `MEMORY_TIMEOUT`) |
| `YDB_TIMEOUT` | | YDB `discovery_request_timeout` (default: `MEMORY_TIMEOUT`) |

## Requirements

- Python 3.11+
- An OpenAI-compatible **LLM and embeddings** provider with an API key (OpenAI,
  Yandex Cloud, Ollama, …) — mem0 uses the LLM to extract facts and the
  embeddings to vectorize them
- A YDB Serverless database and a service account with the `ydb.editor` role

## License

[Apache 2.0](LICENSE)
