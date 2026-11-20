from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("open-notebook-mcp")

mcp = FastMCP(
    "open-notebook-mcp",
    host=os.getenv("HOST", "127.0.0.1"),
    port=int(os.getenv("PORT", "8000")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
    stateless_http=os.getenv("STATELESS_HTTP", "1") == "1",
    json_response=os.getenv("JSON_RESPONSE", "1") == "1",
)

# -----------------------------
# Configuration and Constants
# -----------------------------

# Maximum number of items to return in list operations
MAX_LIMIT = 100

# Default timeout for HTTP requests (seconds)
DEFAULT_TIMEOUT_S = 30.0

def get_base_url() -> str:
    """Get the Open Notebook API base URL from environment."""
    return os.getenv("OPEN_NOTEBOOK_URL", "http://localhost:5055")

def get_auth_token() -> Optional[str]:
    """Get the authentication token from environment."""
    return os.getenv("OPEN_NOTEBOOK_PASSWORD")

def generate_request_id() -> str:
    """Generate a unique request ID for tracking."""
    return str(uuid.uuid4())

# -----------------------------
# Capability index (source of truth)
# -----------------------------
# Note: CAPABILITIES is a tuple to ensure immutability and prevent accidental modification.
# This is the single source of truth for all available tools in the server.

Detail = Literal["name", "summary", "full"]

@dataclass(frozen=True)
class Capability:
    name: str                 # tool name
    summary: str              # one-liner
    tags: tuple[str, ...]     # searchable tags
    args: dict[str, str]      # param -> type (stringified)
    returns: str              # return type (stringified)
    example: dict[str, Any]   # example call args
    typical_bytes: int        # typical response size (rough)

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="search_capabilities",
        summary="Search tools exposed by this server with progressive detail levels.",
        tags=("meta", "discovery", "progressive-disclosure"),
        args={"query": "str", "detail": "Literal['name','summary','full']", "limit": "int"},
        returns="dict[str, Any]",
        example={"query": "notebook", "detail": "summary", "limit": 10},
        typical_bytes=1200,
    ),
    # Notebooks API
    Capability(
        name="list_notebooks",
        summary="Get all notebooks with optional filtering and ordering.",
        tags=("notebooks", "list", "query"),
        args={"archived": "Optional[bool]", "order_by": "str", "limit": "int"},
        returns="dict[str, Any]",
        example={"archived": False, "order_by": "updated desc", "limit": 20},
        typical_bytes=2000,
    ),
    Capability(
        name="get_notebook",
        summary="Get a specific notebook by ID.",
        tags=("notebooks", "get", "read"),
        args={"notebook_id": "str"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123"},
        typical_bytes=500,
    ),
    Capability(
        name="create_notebook",
        summary="Create a new notebook.",
        tags=("notebooks", "create", "write"),
        args={"name": "str", "description": "Optional[str]"},
        returns="dict[str, Any]",
        example={"name": "My Research", "description": "AI research notebook"},
        typical_bytes=500,
    ),
    Capability(
        name="update_notebook",
        summary="Update an unprotected notebook.",
        tags=("notebooks", "update", "write"),
        args={"notebook_id": "str", "name": "Optional[str]", "description": "Optional[str]", "archived": "Optional[bool]"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123", "name": "Updated Name"},
        typical_bytes=500,
    ),
    # Sources API
    Capability(
        name="list_sources",
        summary="Get all sources with optional filtering.",
        tags=("sources", "list", "query"),
        args={"notebook_id": "Optional[str]", "limit": "int", "offset": "int"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123", "limit": 20, "offset": 0},
        typical_bytes=3000,
    ),
    Capability(
        name="get_source",
        summary="Get a specific source by ID.",
        tags=("sources", "get", "read"),
        args={"source_id": "str"},
        returns="dict[str, Any]",
        example={"source_id": "source:abc123"},
        typical_bytes=2000,
    ),
    Capability(
        name="create_source",
        summary="Add a source to an unprotected notebook.",
        tags=("sources", "create", "write"),
        args={"notebook_id": "str", "type": "str", "url": "Optional[str]", "title": "Optional[str]", "embed": "bool"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123", "type": "link", "url": "https://example.com", "embed": True},
        typical_bytes=2000,
    ),
    # Notes API
    Capability(
        name="list_notes",
        summary="Get all notes with optional filtering.",
        tags=("notes", "list", "query"),
        args={"notebook_id": "Optional[str]", "limit": "int", "offset": "int"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123", "limit": 20, "offset": 0},
        typical_bytes=2000,
    ),
    Capability(
        name="get_note",
        summary="Get a specific note by ID.",
        tags=("notes", "get", "read"),
        args={"note_id": "str"},
        returns="dict[str, Any]",
        example={"note_id": "note:abc123"},
        typical_bytes=1500,
    ),
    Capability(
        name="create_note",
        summary="Add a note to an unprotected notebook.",
        tags=("notes", "create", "write"),
        args={"notebook_id": "str", "title": "str", "content": "str", "topics": "Optional[list[str]]"},
        returns="dict[str, Any]",
        example={"notebook_id": "notebook:abc123", "title": "My Note", "content": "Note content"},
        typical_bytes=1500,
    ),
    Capability(
        name="update_note",
        summary="Update a note linked only to unprotected notebooks.",
        tags=("notes", "update", "write"),
        args={"note_id": "str", "title": "Optional[str]", "content": "Optional[str]", "topics": "Optional[list[str]]"},
        returns="dict[str, Any]",
        example={"note_id": "note:abc123", "title": "Updated Title"},
        typical_bytes=1500,
    ),
    # Search API
    Capability(
        name="search",
        summary="Search content using vector or text search.",
        tags=("search", "query", "vector"),
        args={"query": "str", "type": "str", "notebook_id": "Optional[str]", "limit": "int"},
        returns="dict[str, Any]",
        example={"query": "AI research", "type": "vector", "limit": 10},
        typical_bytes=3000,
    ),
    Capability(
        name="ask_question",
        summary="Ask a question about your content with detailed control.",
        tags=("search", "ask", "ai", "question"),
        args={"question": "str", "strategy_model": "str", "answer_model": "str", "final_answer_model": "str"},
        returns="dict[str, Any]",
        example={"question": "What are the main AI applications?", "strategy_model": "model:abc", "answer_model": "model:abc", "final_answer_model": "model:abc"},
        typical_bytes=5000,
    ),
    Capability(
        name="ask_simple",
        summary="Ask a question about your content with simplified interface.",
        tags=("search", "ask", "ai", "question", "simple"),
        args={"question": "str", "strategy_model": "str", "answer_model": "str", "final_answer_model": "str"},
        returns="dict[str, Any]",
        example={"question": "Summarize my AI research", "strategy_model": "model:abc", "answer_model": "model:abc", "final_answer_model": "model:abc"},
        typical_bytes=4000,
    ),
    # Search API remains the AI-assisted content retrieval surface; models/chat/settings are intentionally excluded from the public MCP tool list.
)

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def _match_score(q: str, cap: Capability) -> int:
    if not q:
        return 0
    qn = _normalize(q)
    hay = " ".join([cap.name, cap.summary, " ".join(cap.tags)])
    hn = _normalize(hay)
    score = 0
    for token in qn.split():
        if token in hn:
            score += 1
    return score

@mcp.tool()
def search_capabilities(query: str = "", detail: Detail = "summary", limit: int = 20) -> dict[str, Any]:
    """Search tools exposed by this server with progressive detail levels.
    
    Args:
        query: Search query to filter tools
        detail: Level of detail - 'name' (minimal), 'summary' (default), or 'full' (complete)
        limit: Maximum number of results (1-50)
    
    Returns:
        Dictionary with request_id, query, detail, count, matches, and hint fields
    """
    request_id = generate_request_id()
    limit = max(1, min(int(limit), 50))

    scored = [(c, _match_score(query, c)) for c in CAPABILITIES]
    if query.strip():
        scored = [x for x in scored if x[1] > 0]
        scored.sort(key=lambda t: (-t[1], t[0].name))
    else:
        scored.sort(key=lambda t: t[0].name)

    matches = []
    for cap, _score in scored[:limit]:
        if detail == "name":
            matches.append({"name": cap.name})
        elif detail == "summary":
            matches.append({"name": cap.name, "summary": cap.summary, "tags": list(cap.tags)})
        else:
            matches.append({
                "name": cap.name,
                "summary": cap.summary,
                "tags": list(cap.tags),
                "args": cap.args,
                "returns": cap.returns,
                "example": cap.example,
                "typical_bytes": cap.typical_bytes,
            })

    return {
        "request_id": request_id,
        "query": query,
        "detail": detail,
        "count": len(matches),
        "matches": matches,
        "hint": "Use detail='name' for minimal context; detail='full' only when implementing a call.",
    }

# -----------------------------
# HTTP helper functions
# -----------------------------
# HTTP helper functions
# -----------------------------

async def make_request(
    method: str,
    endpoint: str,
    *,
    json_data: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Make an HTTP request to the Open Notebook API.
    
    Args:
        method: HTTP method (GET, POST, PUT)
        endpoint: API endpoint path (e.g., '/api/notebooks')
        json_data: Optional JSON body for POST/PUT requests
        params: Optional query parameters
    
    Returns:
        Response JSON as dictionary
    
    Raises:
        Exception: If request fails with formatted error message
    """
    base_url = get_base_url()
    url = f"{base_url}{endpoint}"
    
    headers = {"Content-Type": "application/json"}
    auth_token = get_auth_token()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=DEFAULT_TIMEOUT_S) as client:
        try:
            if method == "GET":
                r = await client.get(url, headers=headers, params=params)
            elif method == "POST":
                r = await client.post(url, headers=headers, json=json_data, params=params)
            elif method == "PUT":
                r = await client.put(url, headers=headers, json=json_data, params=params)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            r.raise_for_status()
            
            # Handle empty responses
            if not r.text:
                return {"message": "Success"}
            
            try:
                return r.json()
            except (ValueError, Exception) as json_err:
                # If response is not valid JSON, return text content
                log.warning(f"Non-JSON response from {endpoint}: {json_err}")
                return {"message": "Success", "content": r.text}
            
        except httpx.HTTPError as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    error_msg = error_detail.get("detail", error_msg)
                except (ValueError, AttributeError):
                    # If error response is not JSON, use text or default message
                    error_msg = e.response.text or error_msg
            
            raise Exception(f"API request failed: {error_msg}")

# -----------------------------
# Notebooks API Tools
# -----------------------------

NOTEBOOK_ID_PATTERN = re.compile(r"notebook:[A-Za-z0-9_-]+")


def get_protected_notebook_ids() -> set[str]:
    return {
        value.strip()
        for value in os.getenv("OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS", "").split(",")
        if value.strip()
    }


def require_notebook_write_access(notebook_id: str) -> None:
    protected_ids = get_protected_notebook_ids()
    target = None
    if not NOTEBOOK_ID_PATTERN.fullmatch(notebook_id):
        message = "Use a full notebook ID in the form notebook:<id>."
        hint = "Copy the notebook ID returned by list_notebooks."
    elif any(not NOTEBOOK_ID_PATTERN.fullmatch(value) for value in protected_ids):
        target = notebook_id
        message = "The protected notebook ID configuration is invalid."
        hint = "Ask the operator to check OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS."
    elif notebook_id in protected_ids:
        target = notebook_id
        message = "This notebook is protected against writes."
        hint = "Use an unprotected notebook or create a new one."
    else:
        return

    raise ToolError(json.dumps({
        "error_code": "NOTEBOOK_WRITE_FORBIDDEN",
        "notebook_id": target,
        "message": message,
        "hint": hint,
        "request_id": generate_request_id(),
    }))


@mcp.tool()
async def list_notebooks(
    archived: Optional[bool] = None,
    order_by: str = "updated desc",
    limit: int = 20,
) -> dict[str, Any]:
    """Get all notebooks with optional filtering and ordering.
    
    Args:
        archived: Filter by archived status (None = all, True = archived only, False = active only)
        order_by: Order by field and direction (e.g., 'created desc', 'name asc')
        limit: Maximum number of results (1-100)
    
    Returns:
        Dictionary with notebooks list and metadata
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params = {"order_by": order_by}
    if archived is not None:
        params["archived"] = archived
    
    notebooks = await make_request("GET", "/api/notebooks", params=params)
    
    # Limit results
    if isinstance(notebooks, list):
        notebooks = notebooks[:limit]
    
    return {
        "request_id": generate_request_id(),
        "count": len(notebooks) if isinstance(notebooks, list) else 0,
        "notebooks": notebooks,
    }

@mcp.tool()
async def get_notebook(notebook_id: str) -> dict[str, Any]:
    """Get a specific notebook by ID.
    
    Args:
        notebook_id: Notebook ID (e.g., 'notebook:abc123')
    
    Returns:
        Notebook details
    """
    notebook = await make_request("GET", f"/api/notebooks/{notebook_id}")
    return {
        "request_id": generate_request_id(),
        "notebook": notebook,
    }

@mcp.tool()
async def create_notebook(name: str, description: Optional[str] = None) -> dict[str, Any]:
    """Create a new notebook.
    
    Args:
        name: Notebook name
        description: Optional notebook description
    
    Returns:
        Created notebook details
    """
    data = {"name": name}
    if description is not None:
        data["description"] = description
    
    notebook = await make_request("POST", "/api/notebooks", json_data=data)
    return {
        "request_id": generate_request_id(),
        "notebook": notebook,
    }

@mcp.tool()
async def update_notebook(
    notebook_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    archived: Optional[bool] = None,
) -> dict[str, Any]:
    """Update a notebook.
    
    Args:
        notebook_id: Notebook ID
        name: Optional new name
        description: Optional new description
        archived: Optional archived status
    
    Returns:
        Updated notebook details
    """
    require_notebook_write_access(notebook_id)
    data = {}
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if archived is not None:
        data["archived"] = archived
    
    notebook = await make_request("PUT", f"/api/notebooks/{notebook_id}", json_data=data)
    return {
        "request_id": generate_request_id(),
        "notebook": notebook,
    }

# -----------------------------
# Sources API Tools
# -----------------------------

@mcp.tool()
async def list_sources(
    notebook_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Get all sources with optional filtering.
    
    Args:
        notebook_id: Optional notebook ID to filter by
        limit: Maximum number of results (1-100)
        offset: Pagination offset
    
    Returns:
        Dictionary with sources list and metadata
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params = {"limit": limit, "offset": offset}
    if notebook_id is not None:
        params["notebook_id"] = notebook_id
    
    sources = await make_request("GET", "/api/sources", params=params)
    return {
        "request_id": generate_request_id(),
        "count": len(sources) if isinstance(sources, list) else 0,
        "sources": sources,
    }

@mcp.tool()
async def get_source(source_id: str) -> dict[str, Any]:
    """Get a specific source by ID.
    
    Args:
        source_id: Source ID (e.g., 'source:abc123')
    
    Returns:
        Source details
    """
    source = await make_request("GET", f"/api/sources/{source_id}")
    return {
        "request_id": generate_request_id(),
        "source": source,
    }

@mcp.tool()
async def create_source(
    notebook_id: str,
    type: str,
    url: Optional[str] = None,
    title: Optional[str] = None,
    embed: bool = True,
) -> dict[str, Any]:
    """Create a new source (link, upload, or text).
    
    Args:
        notebook_id: Notebook ID to add source to
        type: Source type ('link', 'upload', or 'text')
        url: URL for link type sources
        title: Optional title
        embed: Whether to generate embeddings (default: True)
    
    Returns:
        Created source details
    """
    require_notebook_write_access(notebook_id)
    data = {
        "notebook_id": notebook_id,
        "type": type,
        "embed": embed,
    }
    if url is not None:
        data["url"] = url
    if title is not None:
        data["title"] = title
    
    source = await make_request("POST", "/api/sources/json", json_data=data)
    return {
        "request_id": generate_request_id(),
        "source": source,
    }

# -----------------------------
# Notes API Tools
# -----------------------------

@mcp.tool()
async def list_notes(
    notebook_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Get all notes with optional filtering.
    
    Args:
        notebook_id: Optional notebook ID to filter by
        limit: Maximum number of results (1-100)
        offset: Pagination offset
    
    Returns:
        Dictionary with notes list and metadata
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params = {"limit": limit, "offset": offset}
    if notebook_id is not None:
        params["notebook_id"] = notebook_id
    
    notes = await make_request("GET", "/api/notes", params=params)
    return {
        "request_id": generate_request_id(),
        "count": len(notes) if isinstance(notes, list) else 0,
        "notes": notes,
    }

@mcp.tool()
async def get_note(note_id: str) -> dict[str, Any]:
    """Get a specific note by ID.
    
    Args:
        note_id: Note ID (e.g., 'note:abc123')
    
    Returns:
        Note details
    """
    note = await make_request("GET", f"/api/notes/{note_id}")
    return {
        "request_id": generate_request_id(),
        "note": note,
    }

@mcp.tool()
async def create_note(
    notebook_id: str,
    title: str,
    content: str,
    topics: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a new note.
    
    Args:
        notebook_id: Notebook ID to add note to
        title: Note title
        content: Note content
        topics: Optional list of topics
    
    Returns:
        Created note details
    """
    require_notebook_write_access(notebook_id)
    data = {
        "notebook_id": notebook_id,
        "title": title,
        "content": content,
    }
    if topics is not None:
        data["topics"] = topics
    
    note = await make_request("POST", "/api/notes", json_data=data)
    return {
        "request_id": generate_request_id(),
        "note": note,
    }

async def require_note_write_access(note_id: str) -> None:
    protected_ids = get_protected_notebook_ids()

    def forbidden(message: str, hint: str) -> ToolError:
        return ToolError(json.dumps({
            "error_code": "NOTEBOOK_WRITE_FORBIDDEN",
            "message": message,
            "hint": hint,
            "request_id": generate_request_id(),
        }))

    if not re.fullmatch(r"note:[A-Za-z0-9_-]+", note_id):
        raise forbidden("Use a full note ID.", "Copy the ID returned by list_notes.")
    if any(not NOTEBOOK_ID_PATTERN.fullmatch(value) for value in protected_ids):
        raise forbidden(
            "The protected notebook ID configuration is invalid.",
            "Ask the operator to check OPEN_NOTEBOOK_PROTECTED_NOTEBOOK_IDS.",
        )

    lookup_error = forbidden(
        "Could not verify the note's notebook membership.",
        "Check the note ID and Open Notebook API availability.",
    )

    async def list_ids(endpoint: str, *, params: Optional[dict[str, Any]] = None) -> set[str]:
        try:
            rows = await make_request("GET", endpoint, params=params)
        except Exception:
            raise lookup_error from None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str)
            for row in rows
        ):
            raise lookup_error
        return {row["id"] for row in rows}

    # The notes API exposes membership through the notebook-scoped list only.
    notebook_ids = await list_ids("/api/notebooks")
    if any(not NOTEBOOK_ID_PATTERN.fullmatch(value) for value in notebook_ids):
        raise lookup_error
    for notebook_id in sorted(protected_ids & notebook_ids):
        if note_id in await list_ids("/api/notes", params={"notebook_id": notebook_id}):
            raise forbidden(
                f"This note belongs to protected notebook {notebook_id}.",
                "Create a separate note in an unprotected notebook.",
            )
    for notebook_id in sorted(notebook_ids - protected_ids):
        if note_id in await list_ids("/api/notes", params={"notebook_id": notebook_id}):
            return
    raise lookup_error


@mcp.tool()
async def update_note(
    note_id: str,
    title: Optional[str] = None,
    content: Optional[str] = None,
    topics: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Update a note linked only to unprotected notebooks.

    Args:
        note_id: Note ID
        title: Optional new title
        content: Optional new content
        topics: Optional list of topics

    Returns:
        Updated note details
    """
    await require_note_write_access(note_id)
    data = {}
    if title is not None:
        data["title"] = title
    if content is not None:
        data["content"] = content
    if topics is not None:
        data["topics"] = topics

    note = await make_request("PUT", f"/api/notes/{note_id}", json_data=data)
    return {
        "request_id": generate_request_id(),
        "note": note,
    }

# -----------------------------
# Search API Tools
# -----------------------------

@mcp.tool()
async def search(
    query: str,
    type: str = "vector",
    notebook_id: Optional[str] = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search content using vector or text search.
    
    Args:
        query: Search query
        type: Search type ('vector' or 'text')
        notebook_id: Optional notebook ID to limit search
        limit: Maximum number of results (1-50)
    
    Returns:
        Search results
    """
    limit = max(1, min(limit, 50))
    data = {
        "query": query,
        "type": type,
        "limit": limit,
    }
    if notebook_id is not None:
        data["notebook_id"] = notebook_id
    
    results = await make_request("POST", "/api/search", json_data=data)
    return {
        "request_id": generate_request_id(),
        "results": results,
    }

@mcp.tool()
async def ask_question(
    question: str,
    strategy_model: str,
    answer_model: str,
    final_answer_model: str,
    notebook_id: Optional[str] = None,
) -> dict[str, Any]:
    """Ask a question about your content with detailed control.
    
    Args:
        question: Question to ask
        strategy_model: Model ID for strategy generation
        answer_model: Model ID for answering
        final_answer_model: Model ID for final answer synthesis
        notebook_id: Optional notebook ID to limit context
    
    Returns:
        Answer with sources and reasoning
    """
    data = {
        "question": question,
        "strategy_model": strategy_model,
        "answer_model": answer_model,
        "final_answer_model": final_answer_model,
    }
    if notebook_id is not None:
        data["notebook_id"] = notebook_id
    
    result = await make_request("POST", "/api/search/ask", json_data=data)
    return {
        "request_id": generate_request_id(),
        "result": result,
    }

@mcp.tool()
async def ask_simple(
    question: str,
    strategy_model: str,
    answer_model: str,
    final_answer_model: str,
    notebook_id: Optional[str] = None,
) -> dict[str, Any]:
    """Ask a question about your content with simplified interface.
    
    Args:
        question: Question to ask
        strategy_model: Model ID for strategy generation
        answer_model: Model ID for answering
        final_answer_model: Model ID for final answer synthesis
        notebook_id: Optional notebook ID to limit context
    
    Returns:
        Simple answer
    """
    data = {
        "question": question,
        "strategy_model": strategy_model,
        "answer_model": answer_model,
        "final_answer_model": final_answer_model,
    }
    if notebook_id is not None:
        data["notebook_id"] = notebook_id
    
    result = await make_request("POST", "/api/search/ask/simple", json_data=data)
    return {
        "request_id": generate_request_id(),
        "result": result,
    }

# -----------------------------
# Dual transport entrypoint
# -----------------------------

def main() -> None:
    """Run the stdio server or the authenticated HTTP ASGI application."""
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise ValueError(f"Unsupported MCP_TRANSPORT: {transport}")

    # Fail closed before creating/binding a listener; also covers MCP DELETE.
    from .http_auth import authenticated_mcp_app, require_auth_token
    import uvicorn

    token = require_auth_token()
    # The outer middleware enforces the configured Host and Origin allowlists.
    # FastMCP's loopback defaults would also reject requests allowed by that policy.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    app = authenticated_mcp_app(mcp.streamable_http_app(), token)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port, log_level="info")


if __name__ == "__main__":
    main()
