# Container deployment

The container image includes Python dependencies installed at build time. No
runtime package installation or persistent volume is required.

## Build and release

A pull request and push to `main` run Python tests, build the image, and run
container E2E tests without publishing. GHCR publication is intentionally
separated into a dedicated workflow and runs only for tags matching `v*` or a
manual workflow dispatch.

Published images keep semver release tags (for `v*` releases, e.g. `1.2.3`) for deploy/rollback. A weekly
cleanup workflow keeps only the newest 10 non-release image versions and removes
older non-release versions automatically.

Use the immutable
`ghcr.io/jlandowner/open-notebook-mcp@sha256:<actual-digest>` output by the publish
job for deployments. The existing MCP Registry/PyPI workflow is unchanged.

## Run with Docker

Build locally with `docker build -t open-notebook-mcp:local .`, or use a published
image pinned to its digest. Supply credentials through your secret manager or
runtime environment. Generate a strong random value for `MCP_AUTH_TOKEN`, for
example using `openssl rand -hex 32`. Keep it separate from
`OPEN_NOTEBOOK_PASSWORD`, which authenticates requests to the Open Notebook API.
Do not put credentials in Git, logs, or container build arguments.

With `MCP_AUTH_TOKEN`, `OPEN_NOTEBOOK_URL`, and optionally
`OPEN_NOTEBOOK_PASSWORD` already set in your environment:

```bash
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 \
  -e MCP_AUTH_TOKEN -e OPEN_NOTEBOOK_URL -e OPEN_NOTEBOOK_PASSWORD \
  -e MCP_ALLOWED_HOSTS=localhost,127.0.0.1 \
  open-notebook-mcp:local
```

`OPEN_NOTEBOOK_URL` must be reachable from the container. The HTTP endpoint in
this example is `http://127.0.0.1:8000/mcp`. Configure your MCP client with that
URL and an `Authorization: Bearer <token>` header, loading the token securely at
runtime. For another hostname, update `MCP_ALLOWED_HOSTS` accordingly.

## Runtime configuration

| Environment variable | Behavior |
| --- | --- |
| `MCP_TRANSPORT` | `stdio` (default outside Docker) or `streamable-http` |
| `MCP_AUTH_TOKEN` | Required and nonempty for HTTP; fails before binding when absent |
| `HOST`, `PORT`, `MCP_PATH` | HTTP bind and Streamable HTTP path; Docker defaults: `0.0.0.0`, `8000`, `/mcp` |
| `STATELESS_HTTP`, `JSON_RESPONSE` | FastMCP mode; Docker defaults to `1` |
| `MCP_ALLOWED_HOSTS` | Explicit Host allowlist (CSV); include the DNS names clients use |
| `MCP_ALLOWED_ORIGINS` | Optional exact `Origin` allowlist (CSV); by default all requests with an Origin header are rejected |
| `OPEN_NOTEBOOK_URL` | Open Notebook REST API endpoint |
| `OPEN_NOTEBOOK_PASSWORD` | Open Notebook REST API credential; separate from the MCP Bearer token |

Static Bearer authentication is **not** an implementation of MCP OAuth 2.1. HTTP plus Bearer does not encrypt credentials or content on the wire. Use TLS when transport encryption is required.

For verification, make an unauthenticated MCP request and confirm HTTP 401, then initialize and list tools with a valid Authorization header. The tests also check malformed tokens, Host/Origin constraints, session DELETE, and a real MCP `initialize` → `tools/list` → `tools/call` sequence.

### Container E2E verification

Run `docker build -t open-notebook-mcp:test .` followed by
`python scripts/container_e2e.py` (Python standard library only). The GHCR
workflow runs both commands on every PR. The test starts the actual image with
UID 10001, a read-only root filesystem, a temporary `/tmp`, dropped capabilities,
and no privilege escalation. A separate `--network none` startup check proves
that the entrypoint needs no runtime downloads. Host-side HTTP checks cover
Bearer rejection on GET/POST/DELETE, initialize/discovery/tool calls, DNS Host
with a port, Origin restrictions, and stateful session termination in
addition to the default stateless configuration. Containers and test networks
are removed even on failure; generated test credentials are not logged.

This does not exercise a live Open Notebook API or GHCR publication/pull.
Validate those separately in the deployment environment.
