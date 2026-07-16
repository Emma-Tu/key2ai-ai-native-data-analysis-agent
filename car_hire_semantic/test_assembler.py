#!/usr/bin/env python3
"""
Test harness for the car_hire semantic layer + prompt assembler.

Checks, in order:
  A. Structural   — all YAML parses; index/mapping load.
  B. Physical map — every table referenced in physical_mapping.yaml actually
                    exists in all_tables.csv (no hallucinated tables).
  C. Retrieval    — a battery of NL questions each retrieves the expected table.
  D. Injection    — assembled prompts contain the right physical tables, the
                    correct dialect, curated-preference, and guardrails.

Run:  .venv/bin/python test_assembler.py
Exits non-zero if any check fails. Prints a report to stdout.

all_tables.csv is expected one directory up (repo root); override with
  CARHIRE_TABLES_CSV=/path/to/all_tables.csv
"""
import csv
import os
import sys

import yaml

import assemble_prompt as A

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.environ.get("CARHIRE_TABLES_CSV", os.path.join(BASE, "..", "all_tables.csv"))

results = []   # (section, name, ok, detail)


def check(section, name, ok, detail=""):
    results.append((section, name, bool(ok), detail))


# ---------------------------------------------------------------------------
# A. structural
# ---------------------------------------------------------------------------
def test_structural():
    import glob
    bad = []
    for f in (glob.glob(os.path.join(BASE, "messages", "*.yaml"))
              + glob.glob(os.path.join(BASE, "enums", "*.yaml"))
              + [os.path.join(BASE, x) for x in
                 ("relationships.yaml", "metrics.yaml", "scope.yaml",
                  "overlay.yaml", "physical_mapping.yaml")]):
        try:
            yaml.safe_load(open(f))
        except Exception as e:
            bad.append(f"{os.path.basename(f)}: {e}")
    check("A.structural", "all YAML parses", not bad, "; ".join(bad))

    idx = A.load_index()
    check("A.structural", "index loads",
          len(idx["messages"]) > 0 and len(idx["enums"]) > 0,
          f"{len(idx['messages'])} msgs / {len(idx['enums'])} enums")

    phys, meta = A.load_physical()
    check("A.structural", "physical_mapping loads",
          len(phys) > 0 and meta.get("dialect") == "databricks",
          f"{len(phys)} mapped, dialect={meta.get('dialect')}")


# ---------------------------------------------------------------------------
# B. physical map integrity
# ---------------------------------------------------------------------------
def test_physical_integrity():
    if not os.path.exists(CSV):
        check("B.physical", "all_tables.csv present", False, f"not found: {CSV}")
        return
    real = set()
    for r in csv.DictReader(open(CSV)):
        real.add(f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}")

    pm = yaml.safe_load(open(os.path.join(BASE, "physical_mapping.yaml")))
    refs = set()

    def collect(v):
        if isinstance(v, str) and v.startswith(("prod_", "dev_")) and v.count(".") >= 2:
            refs.add(v.strip())
        elif isinstance(v, list):
            for x in v:
                collect(x)
        elif isinstance(v, dict):
            for x in v.values():
                collect(x)

    for m in pm.get("messages", []):
        for k in ("bronze", "also_bronze", "related_bronze", "curated"):
            collect(m.get(k))
    collect(pm.get("gold_entrypoints"))

    missing = sorted(t for t in refs if t not in real)
    check("B.physical", "all mapped tables exist in warehouse",
          not missing, f"{len(refs)} refs, {len(missing)} missing: {missing[:5]}")

    # the 6 core protos' primary events must be mapped
    must_map = [
        "car_hire_quote_search.CarHireQuoteSearchEvent",
        "car_hire_indicative_price.CarHireIndicativePriceSearchEvent",
        "car_hire_no_quote_request_blocking.CarHireNoQuoteRequestBlocking",
        "car_hire.CarHireQuoteSelected",
        "car_hire_app.UserAction",
        "unified_search_service.CreateSearchRequest",
    ]
    mapped = {m.get("proto") for m in pm.get("messages", [])}
    miss = [p for p in must_map if p not in mapped]
    check("B.physical", "6-proto key events all mapped", not miss, f"unmapped: {miss}")


# ---------------------------------------------------------------------------
# C. retrieval quality
# ---------------------------------------------------------------------------
RETRIEVAL_CASES = [
    ("上个月每个 market 的搜索量和平均报价数", "CarHireQuoteSearchEvent"),
    ("各 market 的无报价拦截率，排除 holdout", "CarHireNoQuoteRequestBlocking"),
    ("分组卡片曝光到报价点选的转化率", "CarGroupCardEvent"),
    ("电动混动车占比", "Quote"),                       # via metric injection
    ("房车搜索", "CampervanSearch"),
    ("SEO 指示性价格", "CarHireIndicativePriceSearchEvent"),
    ("USS 桥接 uss_search_id 和 search_guid", "CreateSearchRequest"),
    ("跳转到成交的营收", "Redirect"),
]


def test_retrieval():
    for q, expect in RETRIEVAL_CASES:
        _, meta = A.assemble(q, top_k=5)
        names = [m["name"].split(".")[-1] for m in meta["selected_messages"]]
        check("C.retrieval", f"'{q[:20]}' -> {expect}",
              expect in names, f"got top: {names[:4]}")


# ---------------------------------------------------------------------------
# D. prompt injection correctness
# ---------------------------------------------------------------------------
def test_injection():
    p, meta = A.assemble("各 market 的无报价拦截率，排除 holdout", top_k=3)
    check("D.injection", "dialect = databricks", meta["dialect"] == "databricks")
    check("D.injection", "real bronze table in prompt",
          "prod_trusted_bronze.internal.car_hire_no_quote_request_blocking" in p)
    check("D.injection", "guardrail present", "只使用上面列出的表" in p)
    check("D.injection", "USS bridge rule present", "USS bridge" in p)

    # curated preference shown for card events
    p2, _ = A.assemble("分组卡片曝光", top_k=2)
    check("D.injection", "curated (silver) table surfaced",
          "prod_trusted_silver.user_behaviour.v_car_hire_inventory" in p2)

    # gold entrypoints section
    check("D.injection", "gold entrypoints present",
          "v_search_car_hire" in p)

    # nested strategy for quotes
    p3, _ = A.assemble("平均报价数", top_k=3)
    check("D.injection", "nested explode hint for quotes",
          "explode" in p3.lower())

    # out-of-scope question: no tables, but still emits guardrails
    p4, meta4 = A.assemble("今天天气怎么样", top_k=5)
    check("D.injection", "out-of-scope -> no tables",
          len(meta4["selected_messages"]) == 0, f"got {len(meta4['selected_messages'])}")
    check("D.injection", "out-of-scope still has guardrails",
          "只使用上面列出的表" in p4)


def main():
    test_structural()
    test_physical_integrity()
    test_retrieval()
    test_injection()

    # report
    print("=" * 64)
    print("CAR HIRE SEMANTIC LAYER — TEST REPORT")
    print("=" * 64)
    sec = None
    passed = failed = 0
    for s, name, ok, detail in results:
        if s != sec:
            print(f"\n[{s}]")
            sec = s
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        line = f"  {mark}  {name}"
        if detail and (not ok or "OK" not in detail):
            line += f"   ({detail})"
        print(line)
    print("\n" + "=" * 64)
    print(f"TOTAL: {passed} passed, {failed} failed, {passed + failed} checks")
    print("=" * 64)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
