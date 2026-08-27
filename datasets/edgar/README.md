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
   `company_name`, `ticker`, `exchange`) and normalise CIK to an unpadded numeric
   string.
3. **package** (`models/package.sql`) — add the search `corpus`, sort by
   `company_name`, write the terminal zstd Parquet.

Produces `build/edgar.parquet`.

### The served object and this Protocol disagree about `cik` — reconcile before the next publish

The `cik` column of the object at `openlake.meridian.online/edgar.parquet` is a
Parquet **integer**. Step 2 above renders it as **text** (`CAST(… AS BIGINT)::VARCHAR`),
and has done since the file was added, so a run of this Protocol does not reproduce
the column type of the object it is said to produce. The served object predates the
Protocol and has not been rebuilt through it.

`datapackage.json` describes the object we serve — that is its job — so `cik` is
declared `integer` there, and `descriptor.overrides.json` pins that declaration so a
regeneration keeps it. If a future run publishes the text column this Protocol
builds, that pin becomes wrong and `descriptors match their data` will say so. The
fix is to decide which representation `cik` has and make both ends say it; the
measurement that settles the cost is in `datasets/edgar_gleif` — no published CIK
carries a leading zero and the text/integer round trip is exact, so neither
representation loses information. Changing step 2 changes the published bytes and
the crosswalk build that joins to them, which is why it is not done here.

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
