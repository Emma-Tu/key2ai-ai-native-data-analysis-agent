# Test Report — Physical Mapping + Prompt Assembler

**Date:** 2026-07-16
**Scope:** wiring `physical_mapping.yaml` (Databricks tables from `all_tables.csv`)
into `assemble_prompt.py`, and validating the end-to-end runtime prompt.
**Harness:** `test_assembler.py` (run: `.venv/bin/python test_assembler.py`)

## Result: 22 / 22 checks pass ✅

| Section | Checks | Pass |
|---|---|---|
| A. Structural (YAML parses, index/mapping load) | 3 | 3 |
| B. Physical-map integrity (tables really exist) | 2 | 2 |
| C. Retrieval quality (NL question → right table) | 8 | 8 |
| D. Prompt injection (dialect/tables/guardrails) | 9 | 9 |

---

## What was built

`physical_mapping.yaml` now maps proto messages to **real Databricks tables**,
derived from the warehouse inventory `all_tables.csv` (22,306 tables):

- **dialect** = `databricks`; **nested_strategy** = `STRUCT`; enum storage flagged `UNCONFIRMED`
- **34 messages mapped**, referencing **62 distinct physical tables**
- Three layers surfaced per message where available:
  - `bronze` — raw proto events in `prod_trusted_bronze.internal.*` (~1:1 with messages)
  - `curated` — cleaned `prod_trusted_silver.*` tables
  - `gold_entrypoints` — analyst-facing `prod_trusted_gold.*` views
- **Platform-split** frontend events flagged (android_/ios_/public_ → UNION needed)

The assembler (`assemble_prompt.py`) was extended to:
- auto-read the dialect from `physical_mapping.yaml` (no more hardcoded `TBD`)
- inject per-table physical mapping (curated-first, then bronze) into each
  retrieved table block
- add a "gold entrypoints" shortcut section
- upgrade the instruction block (table-tier preference, platform UNION, STRUCT
  dot-access vs ARRAY explode, `timestamp_millis()` time handling, `dt` partition pruning)

---

## B. Physical-map integrity (the critical check)

> **Every one of the 62 tables referenced in `physical_mapping.yaml` exists in
> `all_tables.csv`. Zero hallucinated tables.**

This is the check that matters most: a mapping that points at a non-existent
table would make the agent emit SQL that cannot run. Verified by set-membership
against the warehouse inventory. Also confirmed all 6 core protos' key events
are mapped (quote_search, indicative_price, no_quote_blocking, quote_selected,
app UserAction, USS bridge).

---

## C. Retrieval quality (8 representative questions)

| Question (zh) | Expected table | Rank | Note |
|---|---|---|---|
| 搜索量和平均报价数 | CarHireQuoteSearchEvent | #3 | ⚠ see note below |
| 无报价拦截率，排除 holdout | CarHireNoQuoteRequestBlocking | #1 | |
| 分组卡片曝光到点选转化率 | CarGroupCardEvent | #1 | funnel |
| 电动混动车占比 | Quote | #3 | via metric-table injection (EN/ZH gap) |
| 房车搜索 | CampervanSearch | #1 | |
| SEO 指示性价格 | CarHireIndicativePriceSearchEvent | #1 | |
| USS 桥接 uss_search_id/search_guid | CreateSearchRequest | #2 | all 3 USS tables top-3 |
| 跳转到成交的营收 | Redirect | #1 | conversion tail |

All expected tables land within top-5 (the default retrieval window), so the
agent always sees them.

**Known weakness (not a failure):** for "搜索量和平均报价数",
`CarHireQuoteSearchEvent` ranks #3, behind `ViewHistoryOpenPageInformation` and
`CarHireNoQuoteSample`, which share the bigrams 搜索/报价 in field text. It's
still retrieved, and the matched metric (`搜索量`) injects the correct table +
its `tables:` hint, so the assembled prompt is correct. Keyword retrieval over
EN field names + ZH descriptions is inherently fuzzy; a future embedding-based
retriever would sharpen ranking. Tracked, not blocking.

---

## D. Prompt-injection correctness

Verified in the assembled prompt text:
- dialect line reads `databricks` (auto-detected from mapping)
- real bronze table `prod_trusted_bronze.internal.car_hire_no_quote_request_blocking` appears
- curated silver view `...user_behaviour.v_car_hire_inventory_*` surfaced for card events
- gold entrypoints section present (`v_search_car_hire` etc.)
- `explode` hint present for the repeated `quotes` array
- guardrails present ("只使用上面列出的表", USS-bridge rule)
- out-of-scope question ("今天天气") → 0 tables but still emits guardrails

Sample assembled prompt (blocking-rate question, top-2): **308 lines / ~16 KB** —
comfortably within a single LLM context.

---

## Open confirmations (carried in `physical_mapping.yaml#open_confirmations`)

These need a data-owner to confirm; they do not block the pipeline:
1. `enum_storage`: int vs string per bronze enum column
2. `car_hire_indicative_price_search_event` vs `indicative_carhire_search_event` — canonical source
3. `car_hire_quotes` / `car_hire_response_quote` — whether these are exploded-quote tables
4. Actual join column names between redirect/booking and `search_guid`
5. Whether backend `search_request_id` == USS `uss_search_id` (unlocks end-to-end funnel)

---

## How to re-run

```bash
cd car_hire_semantic
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # first time
.venv/bin/python test_assembler.py        # expects all_tables.csv at repo root
```
