#!/usr/bin/env python3
"""
Runtime prompt assembler for the Car Hire analysis agent.

Given a natural-language question, this:
  1. RETRIEVES the most relevant message / enum files (schema pruning) using a
     hybrid keyword score: English token overlap + Chinese bigram overlap
     (questions are Chinese, field names English, descriptions Chinese).
  2. EXPANDS the selected messages' referenced enums so the agent sees enum
     meanings for any enum field it might use.
  3. ALWAYS loads the small curated knowledge (join keys, funnel, bridge,
     pitfalls, metrics, glossary) — these are always relevant and small.
  4. ASSEMBLES a single prompt string with clear sections and guardrails.

Usage:
  python3 assemble_prompt.py "上个月每个 market 的搜索量和平均报价数"
  python3 assemble_prompt.py --top-k 8 --json "..."      # machine-readable
  python3 assemble_prompt.py --dialect bigquery "..."

Requires PyYAML (see requirements.txt). Run inside .venv:
  .venv/bin/python assemble_prompt.py "..."
"""
import argparse
import glob
import json
import os
import re
import sys

import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
MSG_DIR = os.path.join(BASE, "messages")
ENUM_DIR = os.path.join(BASE, "enums")
INDEX = os.path.join(BASE, "generated", "index.json")

# curated files that are always loaded (small + always relevant)
RELATIONSHIPS = os.path.join(BASE, "relationships.yaml")
METRICS = os.path.join(BASE, "metrics.yaml")
PHYSICAL = os.path.join(BASE, "physical_mapping.yaml")


# ---------------------------------------------------------------------------
# tokenization (hybrid EN token + ZH bigram)
# ---------------------------------------------------------------------------
def en_tokens(text):
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


def zh_bigrams(text):
    chars = re.findall(r"[一-鿿]", text or "")
    return {"".join(pair) for pair in zip(chars, chars[1:])}


def features(text):
    return en_tokens(text) | zh_bigrams(text)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_index():
    with open(INDEX) as f:
        return json.load(f)


def load_message(path):
    with open(path) as f:
        return yaml.safe_load(f)


def message_text_blob(idx_entry, doc, enum_by_key=None):
    """All searchable text for a message: name, description, field names+desc,
    and — crucially — the value meanings of any enums it references, so a
    value-level question ('电动混动车') can retrieve the table whose enum
    ('FuelType') carries those words."""
    parts = [idx_entry["name"], " ".join(idx_entry.get("keywords", []))]
    if doc:
        parts.append(doc.get("description", "") or "")
        for fld in doc.get("fields", []) or []:
            parts.append(fld.get("name", ""))
            parts.append(fld.get("desc", "") or "")
        if enum_by_key:
            for ek in referenced_enums(doc, enum_by_key):
                ed = enum_by_key.get(ek)
                if ed:
                    for v in ed.get("values", []) or []:
                        parts.append(v.get("meaning", "") or "")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def score_messages(question, index, docs, enum_by_key=None):
    qf = features(question)
    if not qf:
        return []
    scored = []
    for e in index["messages"]:
        doc = docs.get(f"{e['package']}.{e['name']}") or docs.get(e["name"])
        # weight matches by where they occur: name/description carry more signal
        # than an incidental field-comment / enum-value match.
        name_f = features(e["name"])
        desc_f = features((doc or {}).get("description", ""))
        field_f = features(message_text_blob(e, doc, enum_by_key)) - name_f - desc_f

        s_name = len(qf & name_f) * 3.0
        s_desc = len(qf & desc_f) * 2.0
        s_field = len(qf & field_f) * 1.0
        score = s_name + s_desc + s_field
        if score == 0:
            continue
        if doc:
            if doc.get("group") == "core":
                score += 0.5
            if "curated" in (doc.get("description_raw") or ""):
                score += 0.5
        scored.append((score, e["name"], e["file"], doc))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def referenced_enums(doc, enum_by_key):
    """Enum keys (package.Name) referenced by a message doc's fields.

    Proto resolves a bare type name within the message's own package first, so
    an unqualified `PickupType` in a car_hire message means car_hire.PickupType,
    NOT car_hire_app.PickupType (which has different numeric values). We resolve
    same-package first, then fall back to any package with that name.
    """
    pkg = doc.get("package", "")
    used = set()

    def resolve(t):
        t = (t or "").replace("repeated ", "").strip()
        if "." in t:                          # already qualified (e.g. commons.X)
            return t if t in enum_by_key else None
        # bare name: prefer same package
        if f"{pkg}.{t}" in enum_by_key:
            return f"{pkg}.{t}"
        # fallback: any package defining this enum name
        for k in enum_by_key:
            if k.rsplit(".", 1)[1] == t:
                return k
        return None

    def walk_fields(fields):
        for fld in fields or []:
            k = resolve(fld.get("type"))
            if k:
                used.add(k)

    walk_fields(doc.get("fields"))
    for nm in doc.get("nested_messages", []) or []:
        walk_fields(nm.get("fields"))
    for ne in doc.get("nested_enums", []) or []:
        # nested enums belong to the message's package
        used.add(f"{pkg}.{ne.get('name')}")
    return used


def match_metrics(question, metrics_doc, max_metrics=4):
    """Return metric/glossary entries scored by overlap, strongest first.

    Metric NAME + synonyms are weighted higher than the definition body so a
    single incidental term (e.g. a shared word like '报价') in a long SQL
    definition doesn't pull in every metric.
    """
    qf = features(question)
    scored = []
    for m in metrics_doc.get("metrics", []) or []:
        name_f = features(m.get("metric", "")) | features(" ".join(m.get("synonyms", []) or []))
        body_f = features(str(m.get("desc", ""))) | features(str(m.get("definition", "")))
        score = len(qf & name_f) * 2.0 + len(qf & (body_f - name_f)) * 1.0
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    # relative cutoff: drop metrics scoring < 40% of the top hit (kills the
    # "one shared bigram" long tail) but always keep at least the top hit.
    if scored:
        top = scored[0][0]
        hits = [m for s, m in scored[:max_metrics] if s >= 0.4 * top]
    else:
        hits = []

    gloss = []
    for g in metrics_doc.get("glossary", []) or []:
        blob = f"{g.get('term','')} {g.get('maps_to','')} {g.get('note','')}"
        if qf & features(blob):
            gloss.append(g)
    return hits, gloss


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def load_physical():
    """Load physical_mapping.yaml -> {'proto.Msg': entry}, plus top-level meta."""
    if not os.path.exists(PHYSICAL):
        return {}, {}
    doc = yaml.safe_load(open(PHYSICAL)) or {}
    by_proto = {}
    for m in doc.get("messages", []) or []:
        key = m.get("proto")
        if key:
            by_proto[key] = m
    meta = {
        "dialect": doc.get("dialect"),
        "enum_storage": doc.get("enum_storage"),
        "nested_strategy": doc.get("nested_strategy"),
        "time_handling": doc.get("time_handling", {}),
        "gold_entrypoints": doc.get("gold_entrypoints", {}),
    }
    return by_proto, meta


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def render_physical(entry):
    """Render the physical-table block for one message's mapping entry."""
    if not entry:
        return "    - 物理表: （未在 physical_mapping.yaml 中映射；按逻辑层推理并声明假设）"
    lines = []
    bronze = _as_list(entry.get("bronze"))
    curated = _as_list(entry.get("curated"))
    if curated:
        lines.append(f"    - 推荐(curated): {', '.join(curated)}")
    if bronze:
        lines.append(f"    - 原始(bronze): {', '.join(bronze)}")
    if len(bronze) > 1:
        lines.append("    - ⚠ 多张 bronze 表（按平台拆分）：跨平台分析需 UNION")
    if entry.get("nested"):
        lines.append(f"    - 嵌套: {entry['nested']}")
    if entry.get("note"):
        lines.append(f"    - 备注: {entry['note'].strip()}")
    return "\n".join(lines)


def render_message(doc, physical=None):
    lines = [f"### {doc['package']}.{doc['message']}  (group={doc.get('group','?')})"]
    if doc.get("description"):
        lines.append(f"- 含义: {doc['description']}")
    if doc.get("grain"):
        lines.append(f"- 粒度: {doc['grain']}")
    # physical table mapping (from physical_mapping.yaml)
    if physical is not None:
        key = f"{doc['package']}.{doc['message']}"
        lines.append("- 物理映射:")
        lines.append(render_physical(physical.get(key)))
    lines.append("- 字段:")
    for fld in doc.get("fields", []) or []:
        t = fld.get("type", "")
        d = fld.get("desc", "") or ""
        extra = []
        if fld.get("oneof"):
            extra.append(f"oneof={fld['oneof']}")
        tag = f" [{', '.join(extra)}]" if extra else ""
        lines.append(f"    - {fld['name']}: {t}{tag}  # {d}".rstrip(" #"))
    for nm in doc.get("nested_messages", []) or []:
        lines.append(f"  - nested {nm['name']}:")
        for fld in nm.get("fields", []) or []:
            lines.append(f"      - {fld['name']}: {fld.get('type','')}  # {fld.get('desc','') or ''}".rstrip(" #"))
    for ne in doc.get("nested_enums", []) or []:
        vals = ne.get("values", {})
        lines.append(f"  - nested enum {ne['name']}: {vals}")
    return "\n".join(lines)


def render_enum(doc):
    lines = [f"### enum {doc['package']}.{doc['enum']}"]
    if doc.get("desc"):
        lines.append(f"- 含义: {doc['desc']}")
    for v in doc.get("values", []) or []:
        m = v.get("meaning", "") or ""
        dep = " (deprecated)" if v.get("deprecated") else ""
        lines.append(f"    - {v['num']} = {v['name']}{dep}  # {m}".rstrip(" #"))
    return "\n".join(lines)


def yaml_section(path, title):
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f"## {title}\n```yaml\n{f.read().rstrip()}\n```"


# ---------------------------------------------------------------------------
# main assembly
# ---------------------------------------------------------------------------
def assemble(question, top_k=6, dialect=None):
    index = load_index()
    physical, phys_meta = load_physical()
    # dialect precedence: explicit arg > physical_mapping.yaml > "TBD"
    if not dialect:
        dialect = phys_meta.get("dialect") or "TBD"

    # load all message docs (small enough; ~131 files)
    docs = {}
    for path in glob.glob(os.path.join(MSG_DIR, "*.yaml")):
        d = load_message(path)
        if d and d.get("message"):
            # detect curated flag from the raw text (comment stripped by yaml)
            with open(path) as f:
                raw = f.read()
            d["description_raw"] = "curated" if "curated (overlay.yaml)" in raw else ""
            # key by package.name to avoid collisions (e.g. CarGroupCardEvent
            # exists in both car_hire and car_hire_app). Keep bare-name key too
            # as a fallback for any lookups that don't know the package.
            docs[f"{d['package']}.{d['message']}"] = d
            docs.setdefault(d["message"], d)

    enum_by_key = {}   # package.Name -> doc
    for path in glob.glob(os.path.join(ENUM_DIR, "*.yaml")):
        d = load_message(path)
        if d and d.get("enum"):
            enum_by_key[f"{d['package']}.{d['enum']}"] = d

    # 1. retrieve messages (enum value meanings folded into searchable text)
    scored = score_messages(question, index, docs, enum_by_key)
    selected = scored[:top_k]

    # 2. match metrics/glossary
    metrics_doc = {}
    if os.path.exists(METRICS):
        metrics_doc = yaml.safe_load(open(METRICS)) or {}
    hit_metrics, hit_gloss = match_metrics(question, metrics_doc)

    # 2b. inject tables named by matched metrics (closes the EN/ZH gap: a
    # question like '电动混动车占比' matches the metric by synonym even when the
    # table's enum values are English-only). Metric `tables:` encodes which
    # table computes it — authoritative, so we add any not already selected.
    already = {f"{(d or {}).get('package','')}.{n}" for _, n, _, d in selected}
    for m in hit_metrics:
        for tk in m.get("tables", []) or []:
            if tk not in already and tk in docs:
                selected.append((0.0, docs[tk]["message"], docs[tk].get("source_file", ""), docs[tk]))
                already.add(tk)

    # 3. expand referenced enums (package-aware) over the final selection
    wanted_enums = set()
    for _, name, _, doc in selected:
        if doc:
            wanted_enums |= referenced_enums(doc, enum_by_key)

    # ---- build prompt ----
    out = []
    out.append("你是 Skyscanner 租车(car hire)数据分析 Agent。根据下面提供的语义层信息，"
               "把用户问题转成一个正确的分析查询。")
    out.append(f"\n[SQL 方言] {dialect}")
    if phys_meta.get("nested_strategy"):
        out.append(f"[嵌套存储] {phys_meta['nested_strategy']}（proto 嵌套落为 STRUCT/ARRAY，用点号/explode 展开）")
    if phys_meta.get("enum_storage"):
        out.append(f"[枚举存储] {phys_meta['enum_storage']}")
    th = phys_meta.get("time_handling") or {}
    if th.get("to_timestamp"):
        out.append(f"[时间转换] {th['to_timestamp']}；{th.get('partition_hint','')}")

    out.append("\n" + "=" * 60)
    out.append("## 相关表结构（已按问题裁剪；含 proto→物理表映射）")
    if selected:
        for score, name, file, doc in selected:
            if doc:
                out.append("\n" + render_message(doc, physical))
            else:
                out.append(f"\n### {name}  (无详细文档，见 {file})")
    else:
        out.append("（未命中任何表——问题可能超出 car_hire 范围，或需换用关键词）")

    if wanted_enums:
        out.append("\n" + "=" * 60)
        out.append("## 相关枚举取值含义")
        for en in sorted(wanted_enums):
            if en in enum_by_key:
                out.append("\n" + render_enum(enum_by_key[en]))

    if hit_metrics or hit_gloss:
        out.append("\n" + "=" * 60)
        out.append("## 命中的指标口径 / 术语（必须采用这些定义，勿自行发明）")
        for m in hit_metrics:
            out.append(f"\n### 指标: {m.get('metric')}")
            out.append(f"- 定义: {m.get('definition')}")
            if m.get("caveat"):
                out.append(f"- 注意: {m.get('caveat')}")
            if m.get("unit"):
                out.append(f"- 单位: {m.get('unit')}")
        for g in hit_gloss:
            out.append(f"\n- 术语 {g.get('term')} -> {g.get('maps_to')}  {g.get('note','')}")

    # 3c. gold analyst entrypoints (convenience views to start from)
    ge = phys_meta.get("gold_entrypoints") or {}
    if ge:
        out.append("\n" + "=" * 60)
        out.append("## 分析师常用 gold 视图（常见问题可直接从这些起步，已清洗）")
        for k, v in ge.items():
            out.append(f"    - {k}: {v}")

    # 4. always-load curated knowledge
    out.append("\n" + "=" * 60)
    rel = yaml_section(RELATIONSHIPS, "关系 / Join Key / 漏斗 / 陷阱（务必遵守）")
    if rel:
        out.append(rel)

    out.append("\n" + "=" * 60)
    out.append("## 指令")
    out.append(
        "1. 只使用上面列出的表和字段；未出现的表/字段一律不要用。\n"
        "2. 表选择优先级：能用 gold 视图/curated(silver) 就用，缺字段再降到 bronze 原始表。\n"
        "3. 严格区分两个 join-key 命名空间：backend=search_request_id，"
        "frontend=search_guid；跨命名空间必须走 relationships.yaml 里的 USS bridge。\n"
        "4. 前端事件按平台拆表(android_/ios_/public_)，跨平台需 UNION 各物理表。\n"
        "5. 枚举字段默认按数字值存储，除非物理层另有说明（enum_storage）。\n"
        "6. 嵌套(STRUCT)用点号访问；repeated(ARRAY)需 explode 后再按元素聚合。\n"
        "7. oneof 字段每行只有一个分支被填充，按判别枚举过滤。\n"
        "8. 时间：用 timestamp_millis(header.*.unix_time_millis)；bronze 表按 dt 分区，"
        "过滤 dt 做分区裁剪；grappler_receive_timestamp 仅用于管道延迟。\n"
        "9. 若信息不足以确定表/口径，先提出澄清问题，不要臆测。"
    )

    out.append("\n" + "=" * 60)
    out.append(f"## 用户问题\n{question}")

    meta = {
        "dialect": dialect,
        "selected_messages": [
            {"name": f"{(d or {}).get('package','?')}.{n}", "score": round(s, 2),
             "group": (d or {}).get("group"),
             "physical": bool(physical.get(f"{(d or {}).get('package','')}.{n}"))}
            for s, n, f, d in selected
        ],
        "expanded_enums": sorted(wanted_enums),
        "matched_metrics": [m.get("metric") for m in hit_metrics],
        "matched_glossary": [g.get("term") for g in hit_gloss],
    }
    return "\n".join(out), meta


# ---------------------------------------------------------------------------
# structured output for the web UI (rich JSON, reuses the same retrieval)
# ---------------------------------------------------------------------------
def _load_docs_and_enums():
    docs = {}
    for path in glob.glob(os.path.join(MSG_DIR, "*.yaml")):
        d = load_message(path)
        if d and d.get("message"):
            with open(path) as f:
                raw = f.read()
            d["description_raw"] = "curated" if "curated (overlay.yaml)" in raw else ""
            docs[f"{d['package']}.{d['message']}"] = d
            docs.setdefault(d["message"], d)
    enum_by_key = {}
    for path in glob.glob(os.path.join(ENUM_DIR, "*.yaml")):
        d = load_message(path)
        if d and d.get("enum"):
            enum_by_key[f"{d['package']}.{d['enum']}"] = d
    return docs, enum_by_key


def assemble_structured(question, top_k=6, dialect=None):
    """Rich JSON-ready result for the web UI. Reuses retrieval + physical map.
    Also returns the assembled prompt so the UI can show/copy it."""
    prompt, meta = assemble(question, top_k=top_k, dialect=dialect)
    index = load_index()
    physical, phys_meta = load_physical()
    docs, enum_by_key = _load_docs_and_enums()
    metrics_doc = yaml.safe_load(open(METRICS)) or {} if os.path.exists(METRICS) else {}

    # rebuild the selection deterministically (same as assemble)
    scored = score_messages(question, index, docs, enum_by_key)
    selected = scored[:top_k]
    hit_metrics, hit_gloss = match_metrics(question, metrics_doc)
    already = {f"{(d or {}).get('package','')}.{n}" for _, n, _, d in selected}
    for m in hit_metrics:
        for tk in m.get("tables", []) or []:
            if tk not in already and tk in docs:
                selected.append((0.0, docs[tk]["message"], "", docs[tk]))
                already.add(tk)

    def field_view(fld):
        return {"name": fld.get("name"), "type": fld.get("type"),
                "desc": fld.get("desc", "") or "", "oneof": fld.get("oneof"),
                "ref": fld.get("ref")}

    tables = []
    wanted_enums = set()
    for score, name, _f, doc in selected:
        if not doc:
            continue
        key = f"{doc['package']}.{doc['message']}"
        wanted_enums |= referenced_enums(doc, enum_by_key)
        pm = physical.get(key) or {}
        tables.append({
            "key": key, "message": doc["message"], "package": doc["package"],
            "group": doc.get("group"), "score": round(score, 2),
            "description": doc.get("description", ""), "grain": doc.get("grain", ""),
            "fields": [field_view(f) for f in doc.get("fields", []) or []],
            "physical": {
                "bronze": _as_list(pm.get("bronze")),
                "curated": _as_list(pm.get("curated")),
                "note": pm.get("note", ""),
                "nested": pm.get("nested"),
                "platform_split": len(_as_list(pm.get("bronze"))) > 1,
                "mapped": bool(pm),
            },
        })

    enums = []
    for en in sorted(wanted_enums):
        ed = enum_by_key.get(en)
        if ed:
            enums.append({"key": en, "name": ed["enum"], "package": ed["package"],
                          "desc": ed.get("desc", ""),
                          "values": ed.get("values", [])})

    # runnable starter SQL scaffold: real table + a real recent dt so it executes
    # immediately (user edits into their aggregate). NOT full NL2SQL.
    # Prefer a table referenced by a matched metric (authoritative for the
    # question, and a populated event table) over whatever ranked first — some
    # retrieved tables (e.g. sampling tables) are sparse and return 0 rows.
    import datetime
    recent = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    metric_tbls = set()
    for m in hit_metrics:
        for tk in m.get("tables", []) or []:
            metric_tbls.add(tk)

    def _phys_tbl(t):
        p = t["physical"]
        return (p["curated"] or p["bronze"] or [None])[0]

    chosen = None
    for t in tables:                                  # 1st choice: metric-referenced + mapped
        if t["key"] in metric_tbls and _phys_tbl(t):
            chosen = t; break
    if not chosen:
        for t in tables:                              # fallback: first mapped table
            if _phys_tbl(t):
                chosen = t; break

    # A default GROUP BY dimension per event table so the scaffold is a real
    # AGGREGATE (charts nicely) rather than a raw SELECT * dump. COUNT(*) only —
    # cheap (no big-array read), fast, and business-meaningful (by market).
    DEFAULT_GROUP_BY = {
        "car_hire_quote_search.CarHireQuoteSearchEvent": "traveller_context.market",
        "car_hire_indicative_price.CarHireIndicativePriceSearchEvent": "traveller_context.market",
        "car_hire_no_quote_request_blocking.CarHireNoQuoteRequestBlocking": "market",
        "car_hire.CarHireNoQuoteSample": "market",
        "car_hire.CarHireDroppedQuotes": "pick_up_location",
        "car_hire.CarHireMcpcQueryEvent": "dimension_source_values.market",
    }
    starter_sql = ""
    if chosen:
        tbl = _phys_tbl(chosen)
        gb = DEFAULT_GROUP_BY.get(chosen["key"])
        if gb:
            starter_sql = (
                f"-- 起始脚手架：按维度聚合（可直接执行→出图表）。改成你要的口径即可\n"
                f"-- {chosen['package']}.{chosen['message']}\n"
                f"SELECT {gb} AS dim,\n"
                f"       COUNT(*) AS cnt\n"
                f"FROM {tbl}\n"
                f"WHERE dt = '{recent}'\n"
                f"GROUP BY {gb}\n"
                f"ORDER BY cnt DESC\n"
                f"LIMIT 20"
            )
        else:
            starter_sql = (
                f"-- 起始脚手架（真实表+近期分区，可直接执行）\n"
                f"-- {chosen['package']}.{chosen['message']}\n"
                f"SELECT *\nFROM {tbl}\nWHERE dt = '{recent}'\nLIMIT 100"
            )

    return {
        "question": question,
        "dialect": meta["dialect"],
        "phys_meta": {k: phys_meta.get(k) for k in
                      ("enum_storage", "nested_strategy", "gold_entrypoints")},
        "starter_sql": starter_sql,
        "tables": tables,
        "enums": enums,
        "metrics": [{"metric": m.get("metric"), "definition": m.get("definition"),
                     "caveat": m.get("caveat"), "unit": m.get("unit"),
                     "tables": m.get("tables", [])} for m in hit_metrics],
        "glossary": [{"term": g.get("term"), "maps_to": g.get("maps_to"),
                      "note": g.get("note", "")} for g in hit_gloss],
        "prompt": prompt,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", help="自然语言分析问题")
    ap.add_argument("--top-k", type=int, default=6, help="检索多少张表 (default 6)")
    ap.add_argument("--dialect", default="TBD", help="SQL 方言，如 bigquery")
    ap.add_argument("--json", action="store_true", help="额外输出检索元数据(JSON)")
    args = ap.parse_args()

    prompt, meta = assemble(args.question, top_k=args.top_k, dialect=args.dialect)
    print(prompt)
    if args.json:
        print("\n\n----- RETRIEVAL META -----", file=sys.stderr)
        print(json.dumps(meta, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
