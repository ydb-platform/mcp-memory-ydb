## Unreleased ##
* `memory_search` now returns each match's `id` (previously stripped) so facts can be
  addressed for curation.
* Add `memory_delete(memory_id)` and `memory_update(memory_id, text)` tools, scoped to
  the server's namespace (an id from another namespace is refused, fail-closed; a record
  that vanishes between the ownership check and the mutation returns `not_found`).
* Correct the docs: mem0 2.x is **append-only** — it does NOT auto-deduplicate or resolve
  contradictions. Fixed `memory_save` docstring, README ("two tools" → "four", the
  extract+dedup diagram, the contradiction-resolution claims) and the setup hint.
* Server MCP instructions now tell the agent to `memory_delete`/`memory_update` a stale
  fact when a newer one supersedes it, so the store self-curates.

Requires `langchain-ydb` with `get_by_ids` (Unreleased / >= 0.0.17 once released).
