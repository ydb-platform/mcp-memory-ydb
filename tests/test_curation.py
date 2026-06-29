"""Unit tests for the curation scope-guard (no external services — mem0 mocked)."""
from mcp_memory_ydb.server import _delete_owned, _owned_record, _update_owned


class FakeMemory:
    """Minimal stand-in for mem0.Memory (get / delete / update)."""

    def __init__(self, records, raise_on_mutate=False):
        self._records = dict(records)  # memory_id -> record dict (or absent)
        self._raise = raise_on_mutate
        self.deleted = []
        self.updated = []

    def get(self, memory_id):
        return self._records.get(memory_id)

    def delete(self, memory_id):
        if self._raise:
            raise ValueError(f"Memory with id {memory_id} not found")
        self.deleted.append(memory_id)
        self._records.pop(memory_id, None)

    def update(self, memory_id, data):
        if self._raise:
            raise ValueError(f"Memory with id {memory_id} not found")
        self.updated.append((memory_id, data))


def test_owned_record_same_namespace_allows():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns1", "memory": "x"}})
    rec, err = _owned_record(mem, "ns1", "a")
    assert err is None
    assert rec["id"] == "a"


def test_owned_record_foreign_namespace_refused():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns2", "memory": "x"}})
    rec, err = _owned_record(mem, "ns1", "a")
    assert rec is None
    assert err == "wrong_namespace"


def test_owned_record_missing_id():
    mem = FakeMemory({})
    rec, err = _owned_record(mem, "ns1", "does-not-exist")
    assert rec is None
    assert err == "not_found"


def test_owned_record_no_user_id_is_fail_closed():
    # A record without user_id must NOT be treated as owned (refuse, don't allow).
    mem = FakeMemory({"a": {"id": "a", "memory": "x"}})
    rec, err = _owned_record(mem, "ns1", "a")
    assert rec is None
    assert err == "wrong_namespace"


def test_delete_owned_same_namespace():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns1", "memory": "x"}})
    assert _delete_owned(mem, "ns1", "a") == {"deleted": True, "id": "a"}
    assert mem.deleted == ["a"]


def test_delete_owned_foreign_namespace_refused():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns2", "memory": "x"}})
    assert _delete_owned(mem, "ns1", "a") == {"deleted": False, "error": "wrong_namespace", "id": "a"}
    assert mem.deleted == []  # the foreign record was not touched


def test_delete_owned_missing():
    assert _delete_owned(FakeMemory({}), "ns1", "z") == {"deleted": False, "error": "not_found", "id": "z"}


def test_delete_owned_vanished_between_check_and_delete():
    # get() finds it (owned) but delete() raises ValueError → normalized to not_found.
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns1", "memory": "x"}}, raise_on_mutate=True)
    assert _delete_owned(mem, "ns1", "a") == {"deleted": False, "error": "not_found", "id": "a"}


def test_update_owned_same_namespace():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns1", "memory": "x"}})
    assert _update_owned(mem, "ns1", "a", "new text") == {"updated": True, "id": "a"}
    assert mem.updated == [("a", "new text")]


def test_update_owned_foreign_namespace_refused():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns2", "memory": "x"}})
    assert _update_owned(mem, "ns1", "a", "new") == {"updated": False, "error": "wrong_namespace", "id": "a"}
    assert mem.updated == []


def test_update_owned_vanished_between_check_and_update():
    mem = FakeMemory({"a": {"id": "a", "user_id": "ns1", "memory": "x"}}, raise_on_mutate=True)
    assert _update_owned(mem, "ns1", "a", "new") == {"updated": False, "error": "not_found", "id": "a"}
