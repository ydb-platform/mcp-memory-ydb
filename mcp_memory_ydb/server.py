"""
MCP server for memory, built on top of ydb-mcp: the SQL tools are disabled and
memory_search / memory_save are added.

Memory is built up front (in cli.main) and passed in ready — the server does no
lazy initialization, so by the time run() is called the YDB and LLM connections
have already been verified. mem0's synchronous calls run in a thread pool with a
timeout.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging

from ydb_mcp import YDBMCPServer

from .config import Config

log = logging.getLogger("mcp_memory_ydb")

# Sent to the client during the MCP handshake (FastMCP `instructions`). Clients
# that honor server instructions (e.g. Claude Code) will fold this into the
# agent's context, so memory works automatically without a manual system prompt.
# Clients that ignore it lose nothing — the same guidance is in the README.
SERVER_INSTRUCTIONS = (
    "This server gives the agent persistent long-term memory about the user and their projects, \n"
    "stored across sessions and shared across different agents and projects.\n"
    "- Before answering, call `memory_search` with the user's request to retrieve "
    "any relevant facts you already know about them.\n"
    "- After answering, if the user revealed something durable — about themselves, "
    "their project, domain rules, or any context that would help you work correctly "
    "across sessions (preferences, identity, ongoing projects, decisions, important "
    "design choices) — call `memory_save` to remember it. Do not save transient or "
    "trivial details.\n"
    "- Use these tools silently. Do not announce that you are searching or saving "
    "memory, and do not narrate what you found or stored — just weave any relevant "
    "facts into your reply and continue the conversation naturally.\n"
    "- Save facts in the user's own language: pass the text to `memory_save` in the "
    "language the user wrote, without translating it.\n"
    "Facts saved here travel with the user across agents, sessions, and projects."
)


class MemoryMCPServer(YDBMCPServer):
    """YDB MCP: memory tools only, SQL tools disabled."""

    generic_tools = set()

    def __init__(self, memory, config: Config, **kwargs):
        # endpoint/database are passed to the parent for completeness, but with
        # generic_tools empty the parent's YDB driver is never activated — all
        # YDB access goes through mem0 / langchain_ydb.
        # instructions reaches FastMCP via YDBMCPServer's **kwargs passthrough.
        kwargs.setdefault("instructions", SERVER_INSTRUCTIONS)
        super().__init__(endpoint=config.ydb_endpoint, database=config.ydb_database, **kwargs)
        self._memory = memory
        self._cfg = config
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._register_memory_tools()

    async def _run(self, fn):
        """
        mem0 is synchronous, so we run it in a thread pool. asyncio.wait (not
        wait_for) does not cancel the future, but it returns control on timeout:
        the MCP call responds with an error in time even if the thread is still
        running in the background. The main causes of hangs (unreachable YDB/LLM)
        are already ruled out by the startup probes in build_memory.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, fn)
        done, _ = await asyncio.wait({future}, timeout=self._cfg.memory_timeout)
        if not done:
            raise TimeoutError(
                f"Operation did not finish within {self._cfg.memory_timeout:.0f}s. "
                f"Check that YDB and the LLM are reachable."
            )
        return future.result()

    def _register_memory_tools(self):
        # The namespace comes from the server config, not from the agent: one
        # process serves one namespace, so the agent cannot read or write the
        # wrong partition. mem0's own filter/partition key is "user_id", so we
        # pass the namespace under that key when calling mem0 — that mapping is
        # the only place the two names meet.
        namespace = self._cfg.namespace
        threshold = self._cfg.threshold

        @self.tool()
        async def memory_search(query: str, limit: int = 5) -> str:
            """
            Retrieve what is already known about the user and them projects from long-term memory.

            Call this BEFORE answering, on essentially every user turn, to fetch
            relevant facts (preferences, identity, past decisions) stored across
            previous sessions. Pass the user's request, or key terms from it, as
            the query. Returns memories ranked by relevance score; an empty list
            means nothing relevant is stored yet.

            This is the authoritative cross-agent memory: facts stored here are
            available regardless of which agent or project the user is working in.
            """
            log.info("memory_search: ns=%s query=%.80r limit=%d", namespace, query, limit)
            try:
                raw = await self._run(
                    lambda: self._memory.search(query, filters={"user_id": namespace}, top_k=limit)
                )
            except Exception as e:
                log.error("memory_search failed: %s", e)
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            results = [
                {"memory": r["memory"], "score": round(r.get("score", 0), 4)}
                for r in raw.get("results", [])
                if r.get("score", 0) >= threshold
            ]
            log.info("memory_search: %d results (threshold=%.2f)", len(results), threshold)
            # Compact (no indent): the result is consumed by the agent, and a flat
            # payload keeps the client's tool-call card small.
            return json.dumps(results, ensure_ascii=False)

        @self.tool()
        async def memory_save(text: str) -> str:
            """
            Persist durable information to long-term memory.

            Call this AFTER answering whenever the user revealed something durable
            — about themselves, their project, domain rules, or important context
            for future sessions (preferences, identity, ongoing projects, decisions,
            relationships, key design choices). Pass the raw statement in the user's
            own language, do not translate it; mem0 extracts the salient facts,
            deduplicates them, and resolves contradictions automatically. Do not
            call it for transient or trivial details.

            Facts saved here are portable across agents, sessions, and
            projects, and follow the user to every MCP-compatible client.
            """
            log.info("memory_save: ns=%s text=%.80r", namespace, text)
            try:
                await self._run(
                    lambda: self._memory.add([{"role": "user", "content": text}], user_id=namespace)
                )
            except Exception as e:
                log.error("memory_save failed: %s", e)
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            log.info("memory_save: done")
            return json.dumps({"saved": True, "namespace": namespace}, ensure_ascii=False)
