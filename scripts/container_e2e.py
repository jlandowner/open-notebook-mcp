"""Real-image HTTP checks; stdlib only. Run after docker build -t open-notebook-mcp:test ."""
import http.client
import json
import secrets
import subprocess
import time
import uuid
from contextlib import contextmanager

IMAGE = "open-notebook-mcp:test"
TOKEN = secrets.token_hex(32)
SERVICE = "mcp.example.test"
ORIGIN = "https://client.example.test"


def docker(*args):
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed (details suppressed to protect test credentials)")
    return result.stdout.strip()


@contextmanager
def container(network, *env):
    cid = docker("run", "-d", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                 "--cap-drop=ALL", "--security-opt=no-new-privileges", "--network", network,
                 "-p", "127.0.0.1::8000", *env, IMAGE)
    try:
        yield cid
    finally:
        # Never print application logs or environment values (including the test token).
        docker("rm", "-f", cid)


def check(network, stateful=False):
    with container(network, "-e", f"MCP_AUTH_TOKEN={TOKEN}",
                   "-e", f"MCP_ALLOWED_HOSTS=127.0.0.1,{SERVICE}",
                   "-e", f"MCP_ALLOWED_ORIGINS={ORIGIN}",
                   "-e", f"STATELESS_HTTP={0 if stateful else 1}") as cid:
        port = int(docker("port", cid, "8000/tcp").rsplit(":", 1)[1])
        def request(method="POST", payload=None, auth=f"Bearer {TOKEN}", **extra):
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            if auth is not None:
                headers["Authorization"] = auth
            headers.update(extra)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request(method, "/mcp", json.dumps(payload) if payload else None, headers)
                response = conn.getresponse()
                # GET can be a long-lived SSE stream; headers suffice for these checks.
                body = response.read() if method != "GET" else b""
                return response.status, dict(response.getheaders()), body
            finally:
                conn.close()
        for _ in range(100):
            try:
                if request("GET", auth=None)[0] == 401:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.1)
        else:
            raise AssertionError("container HTTP readiness timed out")
        assert docker("exec", cid, "id", "-u") == "10001"
        for method in ("GET", "POST", "DELETE"):
            for auth in (None, "Bearer wrong", "Basic wrong", "Bearer", f"bearer {TOKEN}"):
                status, headers, _ = request(method, auth=auth)
                assert status == 401, (method, status)
                assert headers.get("www-authenticate") == "Bearer"
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "container-e2e", "version": "1"}}}
        status, headers, body = request(payload=init, Host=f"{SERVICE}:8000", Origin=ORIGIN)
        assert status == 200, (status, body)
        protocol = json.loads(body)["result"]["protocolVersion"]
        session = headers.get("mcp-session-id")
        extra = {"MCP-Protocol-Version": protocol}
        if stateful:
            assert session
            extra["Mcp-Session-Id"] = session
        assert request(payload={"jsonrpc": "2.0", "method": "notifications/initialized"}, **extra)[0] == 202
        for method, params in (("tools/list", {}), ("tools/call", {"name": "search_capabilities", "arguments": {"query": "notebook", "detail": "name", "limit": 1}})):
            status, _, body = request(payload={"jsonrpc": "2.0", "id": 2, "method": method, "params": params}, **extra)
            assert status == 200, (status, body)
            result = json.loads(body)["result"]
            if method == "tools/list":
                assert any(t["name"] == "search_capabilities" for t in result["tools"])
            else:
                assert not result.get("isError", False)
                content = json.loads(result["content"][0]["text"])
                assert content["count"] == 1 and content["matches"]
        assert request(payload=init, Host="evil.invalid:8000")[0] == 400
        assert request(payload=init, Origin="https://evil.invalid")[0] == 403
        assert request("GET", **extra)[0] == 200
        assert request("DELETE", **extra)[0] == (200 if stateful else 405)
        if stateful:
            assert request(payload={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, **extra)[0] == 404


def main():
    network = "mcp-e2e-" + uuid.uuid4().hex
    docker("network", "create", "--opt", "com.docker.network.bridge.enable_ip_masquerade=false", network)
    try:
        # Disable outbound masquerading: containers have no Internet route via NAT.
        # Unlike --internal, this still supports host-side published-port checks.
        for env in ((), ("-e", "MCP_AUTH_TOKEN=")):
            with container(network, *env) as cid:
                for _ in range(100):
                    state = json.loads(docker("inspect", "--format", "{{json .State}}", cid))
                    if not state["Running"]:
                        assert state["ExitCode"] != 0
                        break
                    time.sleep(.1)
                else:
                    raise AssertionError("server started without a token")
        # With networking completely disabled, the installed entrypoint must still
        # start and answer HTTP; no package registry or upstream API is reachable.
        with container("none", "-e", f"MCP_AUTH_TOKEN={TOKEN}") as cid:
            probe = "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/mcp'))"
            for _ in range(100):
                result = subprocess.run(["docker", "exec", cid, "/app/.venv/bin/python", "-c", probe],
                                        capture_output=True, timeout=10)
                if b"401" in result.stderr:
                    break
                time.sleep(.1)
            else:
                raise AssertionError("offline container did not start")
        check(network)
        check(network, stateful=True)
    finally:
        docker("network", "rm", network)
    print("Container E2E passed: fail-closed startup, auth, MCP, Host/Origin, sessions, non-root/read-only/offline runtime")


if __name__ == "__main__":
    main()
