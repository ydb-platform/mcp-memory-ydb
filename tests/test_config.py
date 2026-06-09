"""Tests for Config.from_env() and endpoint parsing — pure, no external services."""
import pytest

from mcp_memory_ydb.config import DEFAULT_FACT_INSTRUCTIONS, Config, ConfigError, _parse_endpoint

# Environment variables the config reads; cleared before each test for isolation.
_ENV_KEYS = [
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
    "EMBEDDINGS_MODEL", "EMBEDDINGS_BASE_URL", "EMBEDDINGS_API_KEY",
    "YDB_ENDPOINT", "YDB_DATABASE", "YDB_SA_KEY_FILE", "YDB_SA_KEY",
    "MEMORY_NAMESPACE", "MEMORY_FACT_INSTRUCTIONS",
    "MEMORY_THRESHOLD", "MEMORY_TIMEOUT", "PROBE_TIMEOUT", "YDB_TIMEOUT",
]

_REQUIRED = {
    "LLM_BASE_URL": "https://api.openai.com/v1",
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_API_KEY": "sk-test",
    "EMBEDDINGS_MODEL": "text-embedding-3-small",
    "YDB_ENDPOINT": "grpcs://ydb.serverless.yandexcloud.net:2135",
    "YDB_DATABASE": "/ru-central1/folder/db",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _set(monkeypatch, **overrides):
    for key, val in {**_REQUIRED, **overrides}.items():
        monkeypatch.setenv(key, val)


# ── endpoint parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint,expected", [
    ("grpcs://host.example:2135", ("host.example", 2135, True)),
    ("grpc://localhost:2136", ("localhost", 2136, False)),
    ("grpcs://host.example", ("host.example", 2135, True)),  # default port
    ("host.example:2135", ("host.example", 2135, False)),    # no scheme
])
def test_parse_endpoint_ok(endpoint, expected):
    assert _parse_endpoint(endpoint) == expected


def test_parse_endpoint_bad_port():
    with pytest.raises(ConfigError, match="port"):
        _parse_endpoint("grpcs://host.example:abc")


# ── required variables ──────────────────────────────────────────────────────────

def test_missing_required_lists_all(monkeypatch):
    # No variables set — error must name every missing required key.
    with pytest.raises(ConfigError) as exc:
        Config.from_env()
    msg = str(exc.value)
    for key in _REQUIRED:
        assert key in msg


def test_minimal_valid_config(monkeypatch):
    _set(monkeypatch)
    cfg = Config.from_env()
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.ydb_host == "ydb.serverless.yandexcloud.net"
    assert cfg.ydb_port == 2135
    assert cfg.ydb_secure is True
    # Embeddings base_url/api_key inherit from the LLM ones.
    assert cfg.emb_base_url == _REQUIRED["LLM_BASE_URL"]
    assert cfg.emb_api_key == _REQUIRED["LLM_API_KEY"]
    assert cfg.emb_is_yandex is False


# ── embeddings inheritance and override ─────────────────────────────────────────

def test_embeddings_override(monkeypatch):
    _set(monkeypatch, EMBEDDINGS_BASE_URL="http://localhost:11434/v1", EMBEDDINGS_API_KEY="ollama")
    cfg = Config.from_env()
    assert cfg.emb_base_url == "http://localhost:11434/v1"
    assert cfg.emb_api_key == "ollama"


# ── Yandex emb:// URI ────────────────────────────────────────────────────────────

def test_yandex_embeddings_uri(monkeypatch):
    _set(monkeypatch, EMBEDDINGS_MODEL="emb://b1gfolderid/text-search-query/latest")
    cfg = Config.from_env()
    assert cfg.emb_is_yandex is True
    assert cfg.emb_folder_id == "b1gfolderid"


def test_yandex_embeddings_uri_missing_folder(monkeypatch):
    _set(monkeypatch, EMBEDDINGS_MODEL="emb://")
    with pytest.raises(ConfigError, match="folder_id"):
        Config.from_env()


# ── numeric parsing ──────────────────────────────────────────────────────────────

def test_timeouts_default_to_memory_timeout(monkeypatch):
    _set(monkeypatch, MEMORY_TIMEOUT="45")
    cfg = Config.from_env()
    assert cfg.memory_timeout == 45.0
    assert cfg.probe_timeout == 45.0
    assert cfg.ydb_timeout == 45.0


def test_timeouts_independent_override(monkeypatch):
    _set(monkeypatch, MEMORY_TIMEOUT="30", PROBE_TIMEOUT="5", YDB_TIMEOUT="10")
    cfg = Config.from_env()
    assert (cfg.memory_timeout, cfg.probe_timeout, cfg.ydb_timeout) == (30.0, 5.0, 10.0)


def test_non_numeric_threshold_fails(monkeypatch):
    _set(monkeypatch, MEMORY_THRESHOLD="high")
    with pytest.raises(ConfigError, match="MEMORY_THRESHOLD"):
        Config.from_env()


# ── SA credentials passthrough ───────────────────────────────────────────────────

def test_sa_key_file_passthrough(monkeypatch):
    _set(monkeypatch, YDB_SA_KEY_FILE="/path/to/key.json")
    cfg = Config.from_env()
    assert cfg.ydb_sa_key_file == "/path/to/key.json"
    assert cfg.ydb_sa_key_json is None


# ── namespace ────────────────────────────────────────────────────────────────────

def test_namespace_defaults(monkeypatch):
    # Unset MEMORY_NAMESPACE falls back to "default" — never the system login.
    _set(monkeypatch)
    assert Config.from_env().namespace == "default"


def test_namespace_override(monkeypatch):
    _set(monkeypatch, MEMORY_NAMESPACE="project-x")
    assert Config.from_env().namespace == "project-x"


# ── fact-extraction instructions ──────────────────────────────────────────────────

def test_fact_instructions_default(monkeypatch):
    # Unset → empty: the server is language-neutral out of the box and leaves
    # mem0's own extraction prompt untouched.
    _set(monkeypatch)
    assert Config.from_env().fact_instructions == DEFAULT_FACT_INSTRUCTIONS == ""


def test_fact_instructions_override(monkeypatch):
    _set(monkeypatch, MEMORY_FACT_INSTRUCTIONS="Store every fact in English.")
    assert Config.from_env().fact_instructions == "Store every fact in English."
