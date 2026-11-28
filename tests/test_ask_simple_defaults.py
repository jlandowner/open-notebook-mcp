"""Contracts for resolving ask_simple's model from Open Notebook defaults."""

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from open_notebook_mcp import server


@pytest.fixture
def upstream(monkeypatch):
    requests = []
    settings = {
        "defaults": {
            "default_tools_model": "model:tools",
            "default_chat_model": "model:chat",
        },
        "defaults_status": 200,
        "defaults_body": None,
    }
    real_async_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/models/defaults":
            if settings["defaults_status"] != 200:
                return httpx.Response(
                    settings["defaults_status"],
                    json=settings["defaults_body"] or {"detail": "Request failed"},
                )
            return httpx.Response(200, json=settings["defaults"])
        if request.url.path == "/api/search/ask/simple":
            return httpx.Response(200, json={"answer": "ok"})
        return httpx.Response(404)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", mock_client)
    monkeypatch.setenv("OPEN_NOTEBOOK_URL", "http://open-notebook.test")
    return requests, settings


def test_ask_simple_uses_tools_model_and_preserves_notebook_filter(upstream, monkeypatch):
    requests, _settings = upstream
    monkeypatch.setenv("OPEN_NOTEBOOK_PASSWORD", "api-secret")

    result = asyncio.run(server.ask_simple("What is in this notebook?", "notebook:abc"))

    assert result["result"] == {"answer": "ok"}
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/models/defaults"),
        ("POST", "/api/search/ask/simple"),
    ]
    assert requests[0].headers["Authorization"] == "Bearer api-secret"
    payload = json.loads(requests[1].content)
    assert payload == {
        "question": "What is in this notebook?",
        "strategy_model": "model:tools",
        "answer_model": "model:tools",
        "final_answer_model": "model:tools",
        "notebook_id": "notebook:abc",
    }


def test_ask_simple_falls_back_to_chat_model_and_reads_changes_each_call(upstream):
    requests, settings = upstream
    settings["defaults"] = {
        "default_tools_model": "",
        "default_chat_model": "model:chat-v1",
    }
    asyncio.run(server.ask_simple("First question"))
    settings["defaults"] = {
        "default_tools_model": None,
        "default_chat_model": "model:chat-v2",
    }
    asyncio.run(server.ask_simple("Second question"))

    posts = [request for request in requests if request.method == "POST"]
    assert len([request for request in requests if request.url.path == "/api/models/defaults"]) == 2
    for request, model_id in zip(posts, ("model:chat-v1", "model:chat-v2")):
        payload = json.loads(request.content)
        assert payload["strategy_model"] == model_id
        assert payload["answer_model"] == model_id
        assert payload["final_answer_model"] == model_id


def test_defaults_api_failure_does_not_send_ask_or_expose_password(upstream, monkeypatch):
    requests, settings = upstream
    settings["defaults_status"] = 401
    settings["defaults_body"] = {"detail": "api-secret"}
    monkeypatch.setenv("OPEN_NOTEBOOK_PASSWORD", "api-secret")

    with pytest.raises(ToolError) as error:
        asyncio.run(server.ask_simple("Question"))

    payload = json.loads(str(error.value))
    assert payload["error_code"] == "DEFAULT_MODELS_FETCH_FAILED"
    assert "api-secret" not in str(error.value)
    assert [request.url.path for request in requests] == ["/api/models/defaults"]


@pytest.mark.parametrize(
    ("defaults", "error_code"),
    [
        ({"default_chat_model": "model:chat"}, "DEFAULT_MODELS_INVALID_RESPONSE"),
        (
            {"default_tools_model": None, "default_chat_model": None},
            "DEFAULT_MODEL_NOT_CONFIGURED",
        ),
        (
            {"default_tools_model": None, "default_chat_model": ""},
            "DEFAULT_MODEL_NOT_CONFIGURED",
        ),
        (
            {"default_tools_model": None, "default_chat_model": "   "},
            "DEFAULT_MODEL_NOT_CONFIGURED",
        ),
        (
            {"default_tools_model": "  ", "default_chat_model": "  "},
            "DEFAULT_MODEL_NOT_CONFIGURED",
        ),
        (
            {"default_tools_model": 123, "default_chat_model": "model:chat"},
            "DEFAULT_MODELS_INVALID_RESPONSE",
        ),
    ],
)
def test_invalid_or_unconfigured_defaults_do_not_send_ask(upstream, defaults, error_code):
    requests, settings = upstream
    settings["defaults"] = defaults

    with pytest.raises(ToolError) as error:
        asyncio.run(server.ask_simple("Question"))

    assert json.loads(str(error.value))["error_code"] == error_code
    assert [request.url.path for request in requests] == ["/api/models/defaults"]


def test_ask_simple_schema_has_no_model_arguments_or_public_model_tools():
    tools = asyncio.run(server.mcp.list_tools())
    listed = {tool.name: tool for tool in tools}
    schema = listed["ask_simple"].model_dump(by_alias=True)["inputSchema"]
    properties = schema["properties"]
    assert {"question", "notebook_id"}.issubset(properties)
    assert not {
        "strategy_model",
        "answer_model",
        "final_answer_model",
    } & properties.keys()

    model_management_tools = {
        "get_default_models",
        "list_models",
        "get_model",
        "create_model",
        "update_model",
        "delete_model",
    }
    assert model_management_tools.isdisjoint(listed)
    assert model_management_tools.isdisjoint({cap.name for cap in server.CAPABILITIES})
