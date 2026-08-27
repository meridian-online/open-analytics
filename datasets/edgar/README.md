# `source.edgar` — SEC EDGAR company tickers

Every company with securities registered with the U.S. Securities and Exchange
Commission — its Central Index Key (CIK), ticker symbol, exchange and legal name, from
the SEC's EDGAR system. A clean **information-retrieval** dataset: the EDGAR ↔ GLEIF
entity-resolution join is *not* done here — it lives in the `crosswalk.edgar_gleif`
Protocol. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `source.edgar`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** — download the SEC's `company_tickers_exchange.json` (the SEC requires a
   User-Agent carrying contact info). Refreshed daily upstream.
2. **load / normalise** (`models/load.sql`) — the file is columnar (a `fields` header
   plus positional `data` rows); flatten it to the 4-field schema (`cik`,
   `company_name`, `ticker`, `exchange`). CIK is cast to an integer: the SEC file
   carries it unpadded already, and the cast refuses anything non-numeric rather
   than letting it through.
3. **package** (`models/package.sql`) — add the search `corpus`, sort by
   `company_name`, write the terminal zstd Parquet.

Produces `build/edgar.parquet`.

### `cik` is an integer, and the served object is one republish behind

**This Protocol now builds `cik` as a Parquet integer, which is what
`datapackage.json` and `descriptor.overrides.json` have declared since 2026-08-12.**
Step 2 used to end `CAST(… AS BIGINT)::VARCHAR` — a cast to integer thrown away
immediately — so it built text while the descriptor declared an integer.

The section this replaces predicted the failure that then happened, and it is worth
keeping the sentence: *"If a future run publishes the text column this Protocol
builds, that pin becomes wrong and `descriptors match their data` will say so."* A
run did, and it does. `scripts/check_descriptors.py` reports two disagreements on
`edgar.cik`, one for this repo's descriptor and one for the copy the object carries
about itself in its own footer — which is why editing the descriptor alone cannot fix
it and would take the count from 2 to 3.

**The object at `openlake.meridian.online/edgar.parquet` still carries the text
column, so both disagreements stand until it is republished from this Protocol.**
Nothing in CI rebuilds a dataset and diffs it against what is served, so no job here
sees this change; it is verified by running the model, which emits `BIGINT` (Parquet
`INT64`).

Republishing has a second step that is not optional. `datasets/edgar_gleif`'s
`x-joins` declares its `coverage` measured against *the exact object* this package
names, and says so: it "moves when they are republished, exactly as `bytes` and
`hash` do". So a run that republishes `edgar` leaves `declared joins run against the
published bytes` red until the crosswalk's coverage is re-derived and republished
too. That is a second slug and a second review, not a larger single act.

Neither representation loses information — no published CIK carries a leading zero
and the text/integer round trip is exact, measured in `datasets/edgar_gleif` — so the
choice is settled by what the descriptor already declares rather than by the data.

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip
— stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
`build/edgar.parquet`; it is not an arcform step.

## Run

Needs the `arc` binary and `duckdb`, plus `curl` for the fetch: `arc run`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

7 steps, of which 1 runs through an opaque `command:`/shell step: `stamp_descriptor`. A shell step is invisible to the engine's staleness model and carries its interpreter and dependency pins inline, so what this Protocol guarantees stops short of it.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_edgar` | `op: http_fetch@1` |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_edgar` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `validate` | `op: finetype_validate@1` |
| 7 | `stamp_descriptor` | `command:` (shell) |

<!-- /protocol-steps -->
