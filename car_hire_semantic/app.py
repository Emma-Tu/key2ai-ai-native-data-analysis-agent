#!/usr/bin/env python3
"""
Local web UI server for the car_hire semantic layer.

Serves a single-page UI (ui.html) and a small JSON API that reuses
assemble_prompt.py — so the web UI and the CLI share one retrieval codebase.

Run:
  .venv/bin/python app.py            # http://127.0.0.1:8765
  .venv/bin/python app.py --port 9000

Endpoints:
  GET  /                       -> ui.html
  POST /api/assemble           {question, top_k} -> structured retrieval + prompt
  GET  /api/catalog            -> all messages + enums (for the browser tab)
  GET  /api/message?key=..     -> one message's full doc
  GET  /api/enum?key=..        -> one enum's full doc
  GET  /api/knowledge          -> relationships.yaml + metrics.yaml + open_confirmations

Stdlib only (http.server). Requires PyYAML (already in requirements.txt).
"""
import argparse
import glob
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import re

import yaml

import assemble_prompt as A
try:
    import dbx_query
    DBX_OK = True
except Exception:
    DBX_OK = False

BASE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(BASE, "ui.html")
REPORT_HTML = os.path.join(BASE, "report.html")
CHARTS_JS = os.path.join(BASE, "charts.js")

# in-memory cache of the last executed results so the report page can render
# without re-running the (possibly heavy) query. Bounded ring.
_REPORTS = {}
_REPORT_SEQ = [0]
_REPORT_MAX = 40


def _store_report(question, sql, result):
    _REPORT_SEQ[0] += 1
    rid = str(_REPORT_SEQ[0])
    _REPORTS[rid] = {"question": question, "sql": sql, "result": result}
    # evict oldest
    if len(_REPORTS) > _REPORT_MAX:
        for k in sorted(_REPORTS, key=int)[:-_REPORT_MAX]:
            _REPORTS.pop(k, None)
    return rid


def _read_yaml(path):
    return yaml.safe_load(open(path)) if os.path.exists(path) else None


def build_catalog():
    """Lightweight list of all messages + enums for the catalog browser."""
    messages, enums = [], []
    for path in sorted(glob.glob(os.path.join(BASE, "messages", "*.yaml"))):
        d = yaml.safe_load(open(path))
        if d and d.get("message"):
            messages.append({
                "key": f"{d['package']}.{d['message']}",
                "message": d["message"], "package": d["package"],
                "group": d.get("group", ""), "domain": d.get("domain", ""),
                "description": d.get("description", ""),
                "grain": d.get("grain", ""),
                "field_count": len(d.get("fields", []) or []),
            })
    for path in sorted(glob.glob(os.path.join(BASE, "enums", "*.yaml"))):
        d = yaml.safe_load(open(path))
        if d and d.get("enum"):
            enums.append({
                "key": f"{d['package']}.{d['enum']}",
                "name": d["enum"], "package": d["package"],
                "group": d.get("group", ""), "desc": d.get("desc", ""),
                "value_count": len(d.get("values", []) or []),
            })
    return {"messages": messages, "enums": enums}


def get_message(key):
    for path in glob.glob(os.path.join(BASE, "messages", "*.yaml")):
        d = yaml.safe_load(open(path))
        if d and f"{d.get('package')}.{d.get('message')}" == key:
            # attach physical mapping if any
            physical, _ = A.load_physical()
            d["_physical"] = physical.get(key)
            return d
    return None


def get_enum(key):
    for path in glob.glob(os.path.join(BASE, "enums", "*.yaml")):
        d = yaml.safe_load(open(path))
        if d and f"{d.get('package')}.{d.get('enum')}" == key:
            return d
    return None


_WRITE_KW = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|REVOKE|"
    r"COPY|WRITE|SET|USE|REFRESH|OPTIMIZE|VACUUM|MSCK)\b", re.IGNORECASE)


def _strip_sql_comments(sql):
    """Remove -- line comments and /* */ block comments (for the guard check only)."""
    no_block = re.sub(r"/\*.*?\*/", " ", sql or "", flags=re.DOTALL)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return no_line.strip()


def guard_sql(sql):
    """Read-only guardrail. Returns (ok, reason). Allows a single SELECT/WITH/SHOW/DESCRIBE.
    Leading SQL comments are stripped before the keyword check (the starter
    scaffold begins with a -- comment)."""
    s = _strip_sql_comments(sql).rstrip(";").strip()
    if not s:
        return False, "空 SQL"
    if ";" in s:
        return False, "只允许单条语句（不要用分号分隔多条）"
    head = s.lstrip("( ").split(None, 1)[0].upper() if s.lstrip("( ") else ""
    if head not in ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
        return False, f"只允许只读查询（SELECT/WITH/SHOW/DESCRIBE），检测到起始关键字：{head or '?'}"
    if _WRITE_KW.search(s):
        return False, "检测到写操作关键字，已拒绝（本工具只读）"
    return True, ""


def build_knowledge():
    rel = _read_yaml(os.path.join(BASE, "relationships.yaml")) or {}
    met = _read_yaml(os.path.join(BASE, "metrics.yaml")) or {}
    phys = _read_yaml(os.path.join(BASE, "physical_mapping.yaml")) or {}
    return {
        "join_keys": rel.get("join_keys", []),
        "bridge": rel.get("bridge_backend_to_frontend", {}),
        "funnel": rel.get("funnel", []),
        "pitfalls": rel.get("pitfalls", []),
        "metrics": met.get("metrics", []),
        "glossary": met.get("glossary", []),
        "open_confirmations": phys.get("open_confirmations", []),
        "gold_entrypoints": phys.get("gold_entrypoints", {}),
        "dialect": phys.get("dialect", ""),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(UI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/report":
                with open(REPORT_HTML, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/charts.js":
                with open(CHARTS_JS, "rb") as f:
                    return self._send(200, f.read(), "application/javascript; charset=utf-8")
            if u.path == "/api/report":
                rid = q.get("id", [""])[0]
                rep = _REPORTS.get(rid)
                return self._json(rep or {"error": "报告不存在或已过期"}, 200 if rep else 404)
            if u.path == "/api/catalog":
                return self._json(build_catalog())
            if u.path == "/api/knowledge":
                return self._json(build_knowledge())
            if u.path == "/api/message":
                d = get_message(q.get("key", [""])[0])
                return self._json(d or {"error": "not found"}, 200 if d else 404)
            if u.path == "/api/enum":
                d = get_enum(q.get("key", [""])[0])
                return self._json(d or {"error": "not found"}, 200 if d else 404)
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/api/assemble":
                question = (payload.get("question") or "").strip()
                top_k = int(payload.get("top_k", 6))
                if not question:
                    return self._json({"error": "empty question"}, 400)
                result = A.assemble_structured(question, top_k=top_k)
                return self._json(result)
            if u.path == "/api/run-sql":
                if not DBX_OK:
                    return self._json({"error": "dbx_query 不可用（Databricks 桥接未就绪）"}, 503)
                sql = payload.get("sql") or ""
                ok, reason = guard_sql(sql)
                if not ok:
                    return self._json({"error": "护栏拒绝：" + reason}, 400)
                limit = int(payload.get("limit", 200))
                res = dbx_query.execute_query(sql, limit=limit)
                rid = _store_report(payload.get("question", ""), sql, res)
                if isinstance(res, dict):
                    res["report_id"] = rid
                return self._json(res)
            if u.path == "/api/validate-sql":
                if not DBX_OK:
                    return self._json({"error": "dbx_query 不可用"}, 503)
                sql = payload.get("sql") or ""
                ok, reason = guard_sql(sql)
                if not ok:
                    return self._json({"ok": False, "guardrail": reason}, 200)
                res = dbx_query.validate_query(sql)
                return self._json({"ok": True, "validate": res}, 200)
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Car Hire Semantic Layer UI  ->  http://{args.host}:{args.port}")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
