# Hermes + Open Notebook MCP on Kubernetes

Issue #5 deploys the MCP server in its own Pod, with Python dependencies already installed in the GHCR image. No runtime package installation or persistent volume is required.

## Build and release

A pull request runs the Python tests and a Docker build without pushing. A push to `main` runs tests then publishes `ghcr.io/jlandowner/open-notebook-mcp:main` and a `sha-...` tag. Tags matching `v*` publish a semver tag. Use the immutable `ghcr.io/jlandowner/open-notebook-mcp@sha256:<actual-digest>` output by the publish job for deployments. The existing MCP Registry/PyPI workflow is unchanged.

## Secrets

Create a randomly generated shared credential and distribute it **only** to Hermes and the MCP Pod:

```bash
TOKEN="$(openssl rand -hex 32)"
kubectl -n <namespace> create secret generic open-notebook-mcp-auth --from-literal="token=$TOKEN"
unset TOKEN
```

Hermes must have the same `OPEN_NOTEBOOK_MCP_TOKEN` injected from this Secret. The Open Notebook API credential is a **separate Secret** (`open-notebook-auth`, key `password`), mounted only in the MCP Pod. Do not put tokens in Helm values, manifests, Git, logs, or container build arguments.

Use a Secret-management solution such as SOPS/External Secrets instead of the imperative command where appropriate. For multi-namespace deployments, create the appropriate Secret in each namespace. If your Open Notebook instance has no password, remove the `OPEN_NOTEBOOK_PASSWORD` environment entry from the example Deployment.

## Deploy

1. Replace the digest placeholder in [`k8s/deployment.yaml`](k8s/deployment.yaml) with a real published SHA256 digest.
2. Edit `OPEN_NOTEBOOK_URL`, the Secret names, and labels for your environment.
3. Apply `kubectl -n <namespace> apply -f k8s/deployment.yaml`.
4. Adapt and apply `k8s/cilium-network-policy.yaml` for your namespace and Pod labels. The policy selects the MCP Pod, not Hermes; the **existing** Hermes egress policy must prevent direct Hermes → Open Notebook connectivity. Adding a Cilium allow rule cannot override another policy's broader allowance.

There is no Ingress. The MCP endpoint is `http://open-notebook-mcp:8000/mcp` for a caller in the same namespace. Kubernetes TCP probes test process connectivity; they do **not** perform MCP initialization or authorization checks. Use an authenticated smoke test for those.

## Hermes config

```yaml
mcp_servers:
  open_notebook:
    url: http://open-notebook-mcp:8000/mcp
    headers:
      Authorization: "Bearer ${OPEN_NOTEBOOK_MCP_TOKEN}"
    timeout: 60
```

Inject `OPEN_NOTEBOOK_MCP_TOKEN` in the Hermes Pod with `valueFrom.secretKeyRef`. Verify that the installed Hermes version interpolates environment variables in header values; if it does not, use a configuration-injection mechanism that reads the Secret at Pod start without persisting the credential to Git.

## Runtime configuration

| Environment variable | Behavior |
| --- | --- |
| `MCP_TRANSPORT` | `stdio` (default outside Docker) or `streamable-http` |
| `MCP_AUTH_TOKEN` | Required and nonempty for HTTP; fails before binding when absent |
| `HOST`, `PORT`, `MCP_PATH` | HTTP bind and Streamable HTTP path; Docker defaults: `0.0.0.0`, `8000`, `/mcp` |
| `STATELESS_HTTP`, `JSON_RESPONSE` | FastMCP mode; Docker defaults to `1` |
| `MCP_ALLOWED_HOSTS` | Required explicit Host allowlist (CSV); sample includes Kubernetes Service DNS names |
| `MCP_ALLOWED_ORIGINS` | Optional exact `Origin` allowlist (CSV); by default all requests with an Origin header are rejected |
| `OPEN_NOTEBOOK_URL` | Open Notebook REST API endpoint |
| `OPEN_NOTEBOOK_PASSWORD` | Open Notebook REST API credential; never shared with Hermes |

The static Bearer Token is intended only for a restricted, internal Kubernetes service. It is **not** an implementation of MCP OAuth 2.1. HTTP plus Bearer does not encrypt credentials or content on the wire. If Pod-network interception is in scope, enforce TLS/mTLS or Cilium's transparent traffic encryption. Expose neither MCP nor Open Notebook to the public Internet.

For verification, make an unauthenticated MCP request and confirm HTTP 401, then initialize and list tools with a valid Authorization header. The tests also check malformed tokens, Host/Origin constraints, session DELETE, and a real MCP `initialize` → `tools/list` → `tools/call` sequence.
