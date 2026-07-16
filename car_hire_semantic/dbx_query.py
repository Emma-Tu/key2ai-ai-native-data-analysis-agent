#!/usr/bin/env python3
"""
Databricks query bridge — drives the `dba-mcp-proxy` (databricks-mcp-dev) over
JSON-RPC on stdio and exposes execute_query / validate_query / list helpers.

Why this instead of databricks-sql-connector: the proxy already handles OAuth
(via the databricks CLI profile), so we reuse it — no raw token needed in this
process. Same proxy the Claude MCP integration uses.

Usage (CLI):
  .venv/bin/python dbx_query.py --sql "SELECT 1 AS ok"
  .venv/bin/python dbx_query.py --validate --sql "SELECT ..."

Importable:
  from dbx_query import execute_query, validate_query
  res = execute_query("SELECT 1")
"""
import json
import os
import subprocess
import threading
import argparse

# proxy invocation. uvx --refresh re-resolves against the private artifactory
# index (needs auth many shells lack), so prefer an already-built dba-mcp-proxy:
#   1. $DBX_PROXY_BIN override   2. one on PATH   3. one in the uv cache
#   4. fall back to uvx (works where the index is authenticated)
_HOST = os.environ.get("DATABRICKS_HOST", "skyscanner-dev.cloud.databricks.com")
_APP = os.environ.get("DATABRICKS_APP_URL",
                      "https://mcp-skyscanner-databricks-dev-3699024101661884.aws.databricksapps.com")


def _find_proxy_bin():
    import shutil
    import glob as _glob
    env = os.environ.get("DBX_PROXY_BIN")
    if env and os.path.exists(env):
        return env
    on_path = shutil.which("dba-mcp-proxy")
    if on_path:
        return on_path
    for base in (os.path.expanduser("~/.cache/uv/archive-v0"),
                 os.path.expanduser("~/.cache/uv")):
        hits = _glob.glob(os.path.join(base, "*", "bin", "dba-mcp-proxy"))
        if hits:
            return sorted(hits)[-1]
    return None


_BIN = _find_proxy_bin()
if _BIN:
    PROXY_CMD = [_BIN, "--databricks-host", _HOST, "--databricks-app-url", _APP]
else:
    PROXY_CMD = ["uvx", "--python", "3.13", "--from",
                 "git+ssh://git@github.com/Skyscanner/databricks-mcp-server.git",
                 "dba-mcp-proxy", "--databricks-host", _HOST, "--databricks-app-url", _APP]
DEFAULT_WAREHOUSE = os.environ.get("DATABRICKS_WAREHOUSE_ID", "ab5bd9b18887c2ed")  # autobot-warehouse
STARTUP_TIMEOUT = int(os.environ.get("DBX_TIMEOUT", "180"))


class MCPProxyError(RuntimeError):
    pass


def _rpc_session(calls, timeout=STARTUP_TIMEOUT):
    """Spawn the proxy, do the MCP handshake, run `calls` (list of (method,params)),
    return the list of result objects. One short-lived session per invocation."""
    proc = subprocess.Popen(PROXY_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    results = {}
    err_lines = []

    def drain_err():
        for line in proc.stderr:
            err_lines.append(line)
    t = threading.Thread(target=drain_err, daemon=True)
    t.start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read_until(want_id):
        while True:
            line = proc.stdout.readline()
            if not line:
                raise MCPProxyError("proxy closed stdout; stderr:\n" + "".join(err_lines[-20:]))
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg

    try:
        # 1. initialize
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05",
                         "capabilities": {}, "clientInfo": {"name": "dbx_query", "version": "1.0"}}})
        init = read_until(1)
        if "error" in init:
            raise MCPProxyError(f"initialize failed: {init['error']}")
        # 2. initialized notification
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        # 3. the calls
        out = []
        for i, (method, params) in enumerate(calls, start=2):
            send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
            resp = read_until(i)
            if "error" in resp:
                raise MCPProxyError(f"{method} failed: {resp['error']}")
            out.append(resp["result"])
        return out
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _tool_result_json(result):
    """MCP tools return {content:[{type:text,text:"...json..."}]}. Parse the text."""
    content = (result or {}).get("content") or []
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    blob = "\n".join(texts).strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {"raw": blob}


def execute_query(sql, warehouse_id=None, limit=1000):
    warehouse_id = warehouse_id or DEFAULT_WAREHOUSE
    res = _rpc_session([("tools/call", {
        "name": "execute_query",
        "arguments": {"query": sql, "warehouse_id": warehouse_id,
                      "limit": limit, "skip_date_filter": True},
    })])[0]
    return _tool_result_json(res)


def validate_query(sql):
    res = _rpc_session([("tools/call", {
        "name": "validate_query", "arguments": {"query": sql},
    })])[0]
    return _tool_result_json(res)


def list_tools():
    return _rpc_session([("tools/list", {})])[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--warehouse")
    ap.add_argument("--limit", type=int, default=1000)
    a = ap.parse_args()
    if a.tools:
        print(json.dumps(list_tools(), ensure_ascii=False, indent=2)); return
    if not a.sql:
        ap.error("--sql required")
    fn = validate_query if a.validate else (lambda s: execute_query(s, a.warehouse, a.limit))
    print(json.dumps(fn(a.sql), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
