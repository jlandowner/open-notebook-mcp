"""ASGI authentication for the internal Streamable HTTP MCP endpoint.

This is a static shared-token mechanism for a private Kubernetes service,
not an OAuth 2.1 authorization-server implementation.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterable

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def require_auth_token() -> str:
    """Require a nonempty token before binding the HTTP listener."""
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if not token or not token.strip():
        raise RuntimeError("MCP_AUTH_TOKEN must be set for Streamable HTTP")
    if "\n" in token or "\r" in token:
        raise RuntimeError("MCP_AUTH_TOKEN must be a single line")
    return token


def _comma_separated(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class BearerTokenMiddleware:
    """Authenticate every HTTP method, including MCP session DELETE."""

    def __init__(self, app: ASGIApp, token: str):
        if not token or not token.strip():
            raise ValueError("Bearer token must not be empty")
        self.app = app
        self.expected = b"Bearer " + token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0], self.expected):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class OriginMiddleware:
    """Reject browser-originated requests unless the Origin is explicitly allowed."""

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]):
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origins = [value for key, value in scope.get("headers", []) if key.lower() == b"origin"]
            if origins and (len(origins) != 1 or origins[0].decode("latin-1") not in self.allowed):
                await PlainTextResponse("Forbidden origin", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def authenticated_mcp_app(
    mcp_app: ASGIApp,
    token: str,
    *,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    """Enforce Host, Origin, and Bearer checks on the entire HTTP ASGI app.

    The original lifespan scope is forwarded to FastMCP unchanged.
    """
    if allowed_hosts is None:
        allowed_hosts = _comma_separated(
            os.getenv("MCP_ALLOWED_HOSTS", "localhost,127.0.0.1,open-notebook-mcp")
        )
    if allowed_origins is None:
        allowed_origins = _comma_separated(os.getenv("MCP_ALLOWED_ORIGINS", ""))
    if not allowed_hosts or "*" in allowed_hosts:
        raise ValueError("MCP_ALLOWED_HOSTS must list explicit hosts (wildcard '*' is forbidden)")
    app: ASGIApp = BearerTokenMiddleware(mcp_app, token)
    app = OriginMiddleware(app, allowed_origins)
    app = TrustedHostMiddleware(app, allowed_hosts=allowed_hosts)
    return app
