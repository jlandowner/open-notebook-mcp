import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from open_notebook_mcp import server


@pytest.fixture
def upstream(monkeypatch):
    requests = []
    client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/notebooks":
            return httpx.Response(200, json={"id": "notebook:new"})
        return httpx.Response(200, json={})

    def mock_client(**kwargs):
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", mock_client)
    monkeypatch.setenv("OPEN_NOTEBOOK_URL", "http://open-notebook.test")
    return requests


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (server.create_source, {"type": "link", "url": "https://example.com"}),
        (server.create_note, {"title": "Title", "content": "Text"}),
        (server.update_notebook, {"name": "New name"}),
    ],
)
@pytest.mark.parametrize("notebook_id", ["notebook:abc", "notebook:def", "notebook:notebook:abc"])
def test_protected_writes_do_not_reach_api(upstream, monkeypatch, tool, arguments, notebook_id):
    monkeypatch.setenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", " notebook:abc, ,notebook:def, notebook:abc ")
    with pytest.raises(ToolError) as error:
        asyncio.run(tool(notebook_id, **arguments))
    assert json.loads(str(error.value))["error_code"] == "NOTEBOOK_WRITE_FORBIDDEN"
    assert not upstream


def test_invalid_configuration_blocks_additions(upstream, monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", "notebok:abc")
    with pytest.raises(ToolError, match="NOTEBOOK_WRITE_FORBIDDEN"):
        asyncio.run(server.create_note("notebook:new", "Title", "Text"))
    assert not upstream


@pytest.mark.parametrize("setting", [None, "", " , ", "notebook:newer"])
def test_new_notebook_can_be_used(upstream, monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", raising=False)
    else:
        monkeypatch.setenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", setting)

    async def create_and_populate():
        result = await server.create_notebook("New notebook")
        notebook_id = result["notebook"]["id"]
        await server.create_source(notebook_id, "link", url="https://example.com")
        await server.create_note(notebook_id, "Title", "Text")
        await server.update_notebook(notebook_id, name="Renamed")

    asyncio.run(create_and_populate())
    assert [(r.method, r.url.path) for r in upstream] == [
        ("POST", "/api/notebooks"),
        ("POST", "/api/sources/json"),
        ("POST", "/api/notes"),
        ("PUT", "/api/notebooks/notebook:new"),
    ]
    assert all(json.loads(r.content)["notebook_id"] == "notebook:new" for r in upstream[1:3])


def test_reads_and_post_search_still_work(upstream, monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", "notebook:abc")
    asyncio.run(server.get_notebook("notebook:abc"))
    asyncio.run(server.search("question"))
    assert [(r.method, r.url.path) for r in upstream] == [
        ("GET", "/api/notebooks/notebook:abc"),
        ("POST", "/api/search"),
    ]


def test_only_note_edit_tool_is_exposed():
    tools = asyncio.run(server.mcp.list_tools())
    capabilities = server.search_capabilities("", detail="name", limit=50)
    for names in ({tool.name for tool in tools}, {match["name"] for match in capabilities["matches"]}):
        assert "update_source" not in names
        assert "update_note" in names


@pytest.mark.parametrize(
    ("protected_notes", "open_notes", "allowed"),
    [
        ([], [{"id": "note:example"}], True),
        ([{"id": "note:example"}], [{"id": "note:example"}], False),
        ([{"id": "note:example"}], [], False),
        ([], [], False),
        ({"malformed": True}, [{"id": "note:example"}], False),
        (None, [{"id": "note:example"}], False),
    ],
)
def test_note_edit_checks_notebook_membership(monkeypatch, protected_notes, open_notes, allowed):
    monkeypatch.setenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", "notebook:protected")
    requests = []
    client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "note:example"})
        if request.url.path == "/api/notebooks":
            return httpx.Response(200, json=[{"id": "notebook:open"}, {"id": "notebook:protected"}])
        notes = {"notebook:open": open_notes, "notebook:protected": protected_notes}
        result = notes[request.url.params["notebook_id"]]
        return httpx.Response(503) if result is None else httpx.Response(200, json=result)

    def mock_client(**kwargs):
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", mock_client)
    if allowed:
        result = asyncio.run(server.update_note("note:example", content="New text"))
        assert result["note"]["id"] == "note:example"
        assert requests[-1].url.path == "/api/notes/note:example"
        assert json.loads(requests[-1].content) == {"content": "New text"}
    else:
        with pytest.raises(ToolError, match="NOTEBOOK_WRITE_FORBIDDEN"):
            asyncio.run(server.update_note("note:example", content="New text"))
    assert sum(request.method == "PUT" for request in requests) == int(allowed)
