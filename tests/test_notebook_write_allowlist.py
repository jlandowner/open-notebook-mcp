"""Tests for notebook write allowlist enforcement."""

import asyncio

from open_notebook_mcp import server


def test_get_writable_notebook_ids_parses_and_deduplicates(monkeypatch):
    monkeypatch.setenv(
        "OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS",
        " notebook:abc123, ,notebook:def456,notebook:abc123 ",
    )
    assert server.get_writable_notebook_ids() == {"notebook:abc123", "notebook:def456"}


def test_get_writable_notebook_ids_unset_is_empty(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", raising=False)
    assert server.get_writable_notebook_ids() == set()


def test_create_notebook_is_always_forbidden():
    result = asyncio.run(server.create_notebook(name="n1", description="d1"))
    assert result["error"]["code"] == "NOTEBOOK_WRITE_FORBIDDEN"
    assert result["error"]["operation"] == "create_notebook"


def test_create_source_denied_when_allowlist_empty(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", "")

    called = False

    async def fake_make_request(*args, **kwargs):  # pragma: no cover - should never run
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(server, "make_request", fake_make_request)

    result = asyncio.run(
        server.create_source(
            notebook_id="notebook:abc123",
            type="link",
            url="https://example.com",
        )
    )
    assert result["error"]["code"] == "NOTEBOOK_WRITE_FORBIDDEN"
    assert called is False


def test_update_notebook_allows_allowlisted_id(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", "notebook:abc123")
    calls = []

    async def fake_make_request(method, endpoint, *, json_data=None, params=None):
        calls.append((method, endpoint, json_data, params))
        return {"id": "notebook:abc123", "name": "updated"}

    monkeypatch.setattr(server, "make_request", fake_make_request)

    result = asyncio.run(server.update_notebook(notebook_id="notebook:abc123", name="updated"))
    assert "error" not in result
    assert calls == [("PUT", "/api/notebooks/notebook:abc123", {"name": "updated"}, None)]


def test_update_source_resolves_notebook_and_blocks_not_allowlisted(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", "notebook:allowed")
    calls = []

    async def fake_make_request(method, endpoint, *, json_data=None, params=None):
        calls.append((method, endpoint))
        if method == "GET":
            return {"id": "source:abc123", "notebook_id": "notebook:blocked"}
        raise AssertionError("PUT should not be called when source notebook is not allowlisted")

    monkeypatch.setattr(server, "make_request", fake_make_request)

    result = asyncio.run(server.update_source(source_id="source:abc123", title="new"))
    assert result["error"]["code"] == "NOTEBOOK_WRITE_FORBIDDEN"
    assert result["error"]["operation"] == "update_source"
    assert result["error"]["notebook_id"] == "notebook:blocked"
    assert calls == [("GET", "/api/sources/source:abc123")]


def test_update_source_denies_when_notebook_cannot_be_resolved(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", "notebook:allowed")

    async def fake_make_request(method, endpoint, *, json_data=None, params=None):
        if method == "GET":
            return {"id": "source:abc123"}
        raise AssertionError("PUT should not be called when notebook ID is unknown")

    monkeypatch.setattr(server, "make_request", fake_make_request)

    result = asyncio.run(server.update_source(source_id="source:abc123", title="new"))
    assert result["error"]["code"] == "NOTEBOOK_WRITE_FORBIDDEN"
    assert result["error"]["reason"] == "notebook_id_unknown"


def test_update_note_allows_when_child_resource_notebook_is_allowlisted(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_WRITABLE_NOTEBOOK_IDS", "notebook:abc123")
    calls = []

    async def fake_make_request(method, endpoint, *, json_data=None, params=None):
        calls.append((method, endpoint, json_data))
        if method == "GET":
            return {"id": "note:abc123", "notebook": {"id": "notebook:abc123"}}
        if method == "PUT":
            return {"id": "note:abc123", "title": "updated"}
        raise AssertionError(f"Unexpected method: {method}")

    monkeypatch.setattr(server, "make_request", fake_make_request)

    result = asyncio.run(server.update_note(note_id="note:abc123", title="updated"))
    assert "error" not in result
    assert calls[0] == ("GET", "/api/notes/note:abc123", None)
    assert calls[1] == ("PUT", "/api/notes/note:abc123", {"title": "updated"})

