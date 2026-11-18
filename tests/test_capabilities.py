"""Tests for the Open Notebook MCP server."""

import asyncio

import pytest

from open_notebook_mcp import server
from open_notebook_mcp.server import CAPABILITIES, mcp, search_capabilities


def test_capabilities_defined():
    """Test that all capabilities are properly defined."""
    assert len(CAPABILITIES) > 0, "No capabilities defined"

    # Check that all capabilities have required fields
    for cap in CAPABILITIES:
        assert cap.name, "Missing name for capability"
        assert cap.summary, f"Missing summary for {cap.name}"
        assert cap.tags, f"Missing tags for {cap.name}"
        assert cap.args is not None, f"Missing args for {cap.name}"
        assert cap.returns, f"Missing returns for {cap.name}"
        assert cap.example is not None, f"Missing example for {cap.name}"
        assert cap.typical_bytes > 0, f"Missing typical_bytes for {cap.name}"


def test_search_capabilities_basic():
    """Test basic search_capabilities functionality."""
    result = search_capabilities(query="", detail="name", limit=50)

    assert "request_id" in result
    assert "count" in result
    assert "matches" in result
    assert result["count"] > 0
    assert len(result["matches"]) == result["count"]


def test_search_capabilities_query():
    """Test search_capabilities with query."""
    # Search for notebook-related tools
    result = search_capabilities(query="notebook", detail="summary", limit=10)
    assert result["count"] > 0

    # All matches should have notebook-related content
    for match in result["matches"]:
        assert "name" in match
        assert "summary" in match
        assert "tags" in match


def test_search_capabilities_detail_levels():
    """Test different detail levels."""
    # Name only
    result = search_capabilities(query="", detail="name", limit=1)
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert "name" in match
    assert "summary" not in match

    # Summary
    result = search_capabilities(query="", detail="summary", limit=1)
    match = result["matches"][0]
    assert "name" in match
    assert "summary" in match
    assert "tags" in match
    assert "args" not in match

    # Full
    result = search_capabilities(query="", detail="full", limit=1)
    match = result["matches"][0]
    assert "name" in match
    assert "summary" in match
    assert "tags" in match
    assert "args" in match
    assert "returns" in match
    assert "example" in match


def test_search_capabilities_limit():
    """Test that limit parameter is respected."""
    result = search_capabilities(query="", detail="name", limit=5)
    assert result["count"] <= 5
    assert len(result["matches"]) <= 5

    result = search_capabilities(query="", detail="name", limit=100)
    # Should be capped at 50
    assert result["count"] <= 50


def test_delete_tools_removed_from_capabilities_and_registry():
    """Delete tools should not be exposed through MCP tool listing."""
    removed_tools = {
        "delete_notebook",
        "delete_source",
        "delete_note",
        "delete_model",
        "delete_chat_session",
    }

    listed_tools = asyncio.run(mcp.list_tools())
    listed_tool_names = {tool.name for tool in listed_tools}
    assert removed_tools.isdisjoint(listed_tool_names)


def test_search_capabilities_delete_query_returns_no_matches():
    """Delete capabilities should not appear in discovery search."""
    result = search_capabilities(query="delete", detail="summary", limit=50)
    assert result["count"] == 0
    assert result["matches"] == []


def test_make_request_rejects_delete_without_sending(monkeypatch):
    """DELETE should be rejected before any HTTP verb method is called."""
    called = {"get": 0, "post": 0, "put": 0, "delete": 0}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            called["get"] += 1
            raise AssertionError("GET should not be called")

        async def post(self, *args, **kwargs):
            called["post"] += 1
            raise AssertionError("POST should not be called")

        async def put(self, *args, **kwargs):
            called["put"] += 1
            raise AssertionError("PUT should not be called")

        async def delete(self, *args, **kwargs):
            called["delete"] += 1
            raise AssertionError("DELETE should not be called")

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(ValueError, match="Unsupported HTTP method: DELETE"):
        asyncio.run(server.make_request("DELETE", "/api/notebooks/notebook:abc123"))

    assert called == {"get": 0, "post": 0, "put": 0, "delete": 0}


if __name__ == "__main__":
    # Run tests
    test_capabilities_defined()
    print("✓ test_capabilities_defined passed")

    test_search_capabilities_basic()
    print("✓ test_search_capabilities_basic passed")

    test_search_capabilities_query()
    print("✓ test_search_capabilities_query passed")

    test_search_capabilities_detail_levels()
    print("✓ test_search_capabilities_detail_levels passed")

    test_search_capabilities_limit()
    print("✓ test_search_capabilities_limit passed")

    print("\n✅ All tests passed!")
