# Car Hire Semantic Layer

Storage layer for the **AI Native Data Analysis Agent**. Turns the Skyscanner
car_hire proto schemas into structured knowledge the agent can retrieve and put
into a prompt to generate correct queries.

## Scope

Scope is declared in `scope.yaml` and grouped. Current coverage: **131 messages +
65 enums** across four groups.

| Group | Protos | Mode | What |
|-------|--------|------|------|
| `core` | the original 6 car_hire protos | full | all car-hire events (101 msgs) |
| `b2b` | `b2b_carhire_indicative`, `b2b_carhire_live` | full | B2B API car-hire queries |
| `conversion` | `redirects`, `booking` | messages_only | the funnel tail (redirect → booking) |
| `shared` | `commons`, `clients`, `quote_search`, `unified_search_service`, `vehicle` | subset | only the types car_hire references |

`scope.yaml` controls this. `full` = every type; `subset` = only listed types
(+ enums they use); `messages_only` = only listed messages. This keeps the
multi-vertical protos (redirects/booking) and company-wide protos (commons/
clients) from dumping flights/hotels types into the layer.

Notable shared types pulled in:
- `clients`: `Search`, `CarHireFilterAndSort`, `BookingPanelOption`, `CarHireGroupVisibleProperties`
- `unified_search_service`: `CreateSearchRequest`/`CreateExploreRequest`/`PollSearchRequest` — **the bridge** (see below)

## Layout

```
proto/                 # copies of source + shared protos (read-only reference)
scope.yaml             # CONFIG: which protos/types to model and how (full/subset/messages_only)
overlay.yaml           # CURATED: table/enum descriptions that survive regeneration
messages/*.yaml        # AUTO-GEN: one file per message (fields/types/refs/comments/group)
enums/*.yaml           # AUTO-GEN: one file per enum (values + meanings)
relationships.yaml     # CURATED: join keys, the funnel, the bridge, pitfalls
metrics.yaml           # CURATED: metric口径, glossary, time handling
physical_mapping.yaml  # proto -> Databricks tables (from all_tables.csv); dialect=databricks
test_assembler.py      # test harness (22 checks); see TEST_REPORT.md
generated/
  file_index.json      # which messages/enums live in which proto
  external_refs.yaml    # external types used + where
  index.json           # keyword retrieval index (schema pruning)
generate_yaml.py       # proto + scope.yaml + overlay.yaml -> messages/ + enums/
build_index.py         # YAML -> generated/index.json
assemble_prompt.py     # RUNTIME: NL question -> retrieved context -> agent prompt
requirements.txt       # PyYAML (for assemble_prompt.py)
```

## Overlay: curated descriptions that survive regeneration

`generate_yaml.py` overwrites `messages/` and `enums/` every run, so you must NOT
hand-edit those. Curated table/enum descriptions live in `overlay.yaml` and are
merged at generation time (overriding the proto comment / TODO). Precedence:

```
overlay.yaml (curated)  >  proto inline comment  >  "" # TODO
```

Currently 45 message + 13 enum descriptions are curated (the event tables an
analyst actually queries + shared/bridge/conversion types). The long-tail
building-block messages keep their proto comment or `# TODO` until needed —
deliberately NOT guessed, because a wrong description produces wrong SQL.

## Two kinds of knowledge (why two workflows)

1. **Derivable from proto** → generated, never hand-edited structurally.
   Run `python3 generate_yaml.py` whenever the protos change. Fields, types,
   enum values, nesting, oneof, comments-as-desc.

2. **NOT derivable from proto** → hand-curated in `relationships.yaml` +
   `metrics.yaml`. This is the real value: join keys, the funnel, metric口径.
   A parser can never produce these; they come from the owning squads.

> ⚠️ Generated `desc:` fields fall back to `# TODO 待确认` when the proto had no
> comment. Enum meanings default to the inline comment. Search for `# TODO` and
> `# CONFIRM` — those are the human sign-off points.

## The one thing to know before writing any query

Events live in **two join-key namespaces that do not directly connect**:

- **backend**: `search_request_id` (quote_search, indicative_price, blocking)
- **frontend**: `search_guid` (+ `mini_search_guid`, `*_option_guid`)

End-to-end funnels crossing the two need a **bridge**. Investigation across the
wider repo found it: **`unified_search_service` (USS)** carries `uss_search_id`,
`session_id` and `search_guid` in the same row — it's the join hub. Full chain:

```
quote_search(search_request_id) --USS--> search_guid
  --> CarGroupCardEvent / CarHireQuoteSelected (search_guid)
  --> Redirect(search_guid, redirect_id, revenue)
  --> Booking(redirect_id -> booking_id)
```

Still needs confirmation that car_hire's `search_request_id` == USS `uss_search_id`
(Tiger + Delorean squads). See `relationships.yaml#bridge_backend_to_frontend`.
This is the #1 source of wrong queries; it's documented first for that reason.

## How the agent uses this at query time

`assemble_prompt.py` is the runtime prompt assembler. Given a NL question it:

```
question
  -> hybrid keyword match (EN token + ZH bigram) over messages/enums  (schema pruning)
  -> select top-K messages; expand their referenced enums (package-aware)
  -> match metric口径 + glossary (relative-score cutoff to drop the long tail)
  -> ALWAYS load relationships.yaml (join keys, funnel, bridge, pitfalls)
  -> assemble one prompt with guardrails:
       [dialect]
       [pruned message schemas + curated descriptions]
       [enum value meanings — resolved to the correct package]
       [matched metric definitions + glossary]
       [join keys / funnel / bridge / pitfalls]
       [instructions: only use listed tables/fields; respect the two join-key
        namespaces; enums stored as ints; UNNEST repeated; ask if unsure]
       [user question]
```

Run it:

```bash
.venv/bin/python assemble_prompt.py "上个月每个 market 的搜索量和平均报价数"
.venv/bin/python assemble_prompt.py --top-k 8 --dialect bigquery "..."
.venv/bin/python assemble_prompt.py --json "..."     # + retrieval meta to stderr
```

The output string is what you hand to the LLM. `--json` also prints which tables/
enums/metrics were retrieved (for debugging recall). Setup: `python3 -m venv .venv
&& .venv/bin/pip install -r requirements.txt`.

Design notes:
- Name/description matches are weighted 3×/2× over incidental field-comment
  matches, so the canonical table for a metric ranks near the top.
- Message and enum names collide across packages (e.g. `CarGroupCardEvent`,
  `PickupMethod` exist in both `car_hire` and `car_hire_app` with DIFFERENT
  fields/values). The assembler keys everything by `package.Name` and resolves
  bare enum refs same-package-first — critical, since the numeric values differ.
- Curated relationships are always loaded (small + always relevant); metrics are
  matched and cut off relative to the top hit so only relevant口径 appear.
- Each metric in `metrics.yaml` declares `tables:` (which table computes it).
  When a metric matches, its tables are injected into the selection even if
  keyword retrieval missed them — this closes the EN/ZH vocabulary gap (e.g.
  "电动混动车占比" matches the metric by synonym, and the metric points at
  `CarHireQuoteSearchEvent`/`Quote` whose `FuelType` values are English-only).

## Open items (need squad confirmation)

- Is car_hire `search_request_id` == USS `uss_search_id`? (Tiger + Delorean) — unlocks end-to-end funnel
- Is there a materialised USS search dim table in the warehouse for the join?
- Does backend `request_id` == `search_request_id`? (Tiger)
- Current car-hire booking source table (booking.car_hire_extra is deprecated)?
- Enum storage in the warehouse: int vs string?
- `VIEWED` vs `RENDERED` as the impression definition?
- Warehouse + dialect → fill `physical_mapping.yaml`.

## Regenerating

```bash
python3 generate_yaml.py   # after any proto / scope.yaml / overlay.yaml change
python3 build_index.py     # rebuild retrieval index
```

Never hand-edit `messages/` or `enums/` — edit `overlay.yaml` (descriptions),
`scope.yaml` (coverage), or the proto. `relationships.yaml`, `metrics.yaml`,
`physical_mapping.yaml` are pure hand-authored and never touched by the generator.
