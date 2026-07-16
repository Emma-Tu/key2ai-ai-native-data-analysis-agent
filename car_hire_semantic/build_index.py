#!/usr/bin/env python3
"""
Build a lightweight retrieval index over the semantic-layer YAML files so the
analysis agent can do schema pruning: given a question, pick the few relevant
message/enum files instead of dumping all 150 into the prompt.

This is intentionally dependency-free (no embeddings) — it produces a keyword
index (message name, field names, enum values, comments). Swap in a vector
store later if keyword recall proves insufficient.

Output: generated/index.json
  { "messages": [ {file, name, package, keywords:[...], fields:[...]} ... ],
    "enums":    [ {file, name, package, values:[...]} ... ] }
"""
import glob, json, os, re

BASE = os.path.dirname(__file__)


def tokenize(text):
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


def crude_load(path):
    """Minimal YAML reader for our own generated flat structure (no PyYAML dep)."""
    data = {"fields": [], "values": []}
    for line in open(path):
        s = line.strip()
        m = re.match(r"(message|enum|package|source_file|description|desc):\s*(.*)", s)
        if m:
            data[m.group(1)] = m.group(2).strip().strip('"')
        m = re.match(r"- name:\s*([A-Za-z0-9_]+)", s)
        if m:
            data["fields"].append(m.group(1))
        m = re.match(r"name:\s*([A-Z0-9_]+)\s*$", s)
        if m:
            data["values"].append(m.group(1))
    return data


def main():
    idx = {"messages": [], "enums": []}
    for f in sorted(glob.glob(os.path.join(BASE, "messages", "*.yaml"))):
        d = crude_load(f)
        kw = tokenize(d.get("message", "")) | tokenize(d.get("description", ""))
        for fld in d["fields"]:
            kw |= tokenize(fld)
        idx["messages"].append({
            "file": os.path.relpath(f, BASE),
            "name": d.get("message"),
            "package": d.get("package"),
            "fields": d["fields"],
            "keywords": sorted(kw),
        })
    for f in sorted(glob.glob(os.path.join(BASE, "enums", "*.yaml"))):
        d = crude_load(f)
        idx["enums"].append({
            "file": os.path.relpath(f, BASE),
            "name": d.get("enum"),
            "package": d.get("package"),
            "values": d["values"],
        })
    out = os.path.join(BASE, "generated", "index.json")
    json.dump(idx, open(out, "w"), indent=2, ensure_ascii=False)
    print(f"indexed {len(idx['messages'])} messages, {len(idx['enums'])} enums -> {os.path.relpath(out, BASE)}")


if __name__ == "__main__":
    main()
