"""Contract tests for the Streamable HTTP authentication boundary."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from open_notebook_mcp.http_auth import authenticated_mcp_app, require_auth_token


async def handler(request):
    return JSONResponse({"ok": True})


def client_for_auth(*, allowed_origins=None):
    backend = Starlette(routes=[Route("/mcp", handler, methods=["GET", "POST", "DELETE"])])
    return TestClient(
        authenticated_mcp_app(
            backend,
            "secret-token",
            allowed_hosts=["testserver"],
            allowed_origins=allowed_origins or [],
        )
    )


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic secret-token", "bearer secret-token", "Bearer"])
def test_http_requests_require_bearer_token(method, authorization):
    with client_for_auth() as client:
        headers = {} if authorization is None else {"Authorization": authorization}
        result = client.request(method, "/mcp", headers=headers)
        assert result.status_code == 401
        assert result.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_all_http_methods_are_allowed_with_valid_token(method):
    with client_for_auth() as client:
        result = client.request(method, "/mcp", headers={"Authorization": "Bearer secret-token"})
        assert result.status_code == 200
        assert result.json() == {"ok": True}


def test_duplicate_authorization_headers_are_rejected():
    with client_for_auth() as client:
        result = client.get(
            "/mcp",
            headers=[("Authorization", "Bearer secret-token"), ("Authorization", "Bearer secret-token")],
        )
        assert result.status_code == 401


def test_origin_is_denied_by_default_and_allowlisted_exactly():
    with client_for_auth() as client:
        assert client.get("/mcp", headers={"Authorization": "Bearer secret-token", "Origin": "https://example.org"}).status_code == 403
    with client_for_auth(allowed_origins=["https://example.org"]) as client:
        assert client.get("/mcp", headers={"Authorization": "Bearer secret-token", "Origin": "https://example.org"}).status_code == 200
        assert client.get("/mcp", headers={"Authorization": "Bearer secret-token", "Origin": "https://other.org"}).status_code == 403


def test_unknown_host_is_rejected():
    with client_for_auth() as client:
        assert client.get("/mcp", headers={"Host": "example.org", "Authorization": "Bearer secret-token"}).status_code == 400


@pytest.mark.parametrize("value", [None, "", "   ", "a\nb"])
def test_missing_or_invalid_token_causes_startup_failure(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MCP_AUTH_TOKEN", value)
    with pytest.raises(RuntimeError, match="MCP_AUTH_TOKEN"):
        require_auth_token()


def test_token_can_be_read_from_environment(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "a-valid-token")
    assert require_auth_token() == "a-valid-token"


def test_no_wildcard_hosts():
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
        authenticated_mcp_app(Starlette(), "secret-token", allowed_hosts=["*"])


def test_actual_mcp_initialize_list_and_call():
    """Exercise the real stateless SDK endpoint, not just a fake Starlette route."""
    from open_notebook_mcp.server import mcp

    app = authenticated_mcp_app(
        mcp.streamable_http_app(),
        "secret-token",
        allowed_hosts=["127.0.0.1"],
    )
    headers = {
        "Authorization": "Bearer secret-token",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    # The MCP SDK's localhost DNS-rebinding protection requires Host to include a port.
    # Starlette TrustedHostMiddleware validates the hostname independently.
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        def call(method, request_id, params=None):
            result = client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            )
            assert result.status_code == 200, result.text
            return result.json()

        init = call("initialize", 1, {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}})
        assert "result" in init
        tools = call("tools/list", 2)
        assert any(tool["name"] == "search_capabilities" for tool in tools["result"]["tools"])
        response = call("tools/call", 3, {"name": "search_capabilities", "arguments": {"query": "notebook", "detail": "name", "limit": 1}})
        assert "result" in response
