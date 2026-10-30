# `source.edgar` — SEC EDGAR company tickers

The U.S. Securities and Exchange Commission's company-tickers file from its EDGAR
system: one row per ticker symbol, with the Central Index Key (CIK), EDGAR conformed
company name and exchange it is associated with. A company with several listed
securities has several rows — 1RT Acquisition Corp. (CIK 2054272) has ONCH, ONCHU and
ONCHW — so the key is (`cik`, `ticker`), not `cik`. The SEC updates the file to support
EDGAR searches and [does not guarantee its accuracy or
scope](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data):
it is a ticker list, not a register of every company with securities registered with
the SEC. A clean **information-retrieval** dataset: the EDGAR ↔ GLEIF
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

### `cik` is an integer, in the build and in the served object

**`cik` is a Parquet integer (`INT64`, DuckDB `BIGINT`) in what this Protocol builds,
in the object served at `openlake.meridian.online/edgar.parquet`, and in
`datapackage.json` and `descriptor.overrides.json`.** Step 2 once ended
`CAST(… AS BIGINT)::VARCHAR` — a cast to integer thrown away immediately — so it built
text while the descriptor declared an integer, and the served object carried that text
column until it was republished from the corrected Protocol. `scripts/check_descriptors.py`
holds both this repo's descriptor and the copy the object carries in its own footer to
the served column type, so a regression to text shows up there.

Republishing this package does not have to republish the crosswalk.
`datasets/edgar_gleif`'s `x-joins` records, as `measuredAgainst`, the hash of the
`edgar` object its `coverage` was measured on. Once `edgar` declares a different
hash, `declared joins run against the published bytes` reports that join as due a
rebuild rather than failing it, and the crosswalk catches up at its own next run.
A crosswalk descriptor that records no `measuredAgainst` is still measured against
the served `edgar`, so until the crosswalk's next build writes one, republishing
`edgar` reddens that check as it did before.

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

7 steps, of which 1 runs through an opaque `command:`/shell step: `stamp_descriptor`. A shell step is invisible to the engine's staleness model and carries its interpreter and dependency pins inline, so what this Protocol guarantees stops short of it. Each shell step's row carries a fingerprint of its command line, so an edit to the line changes this block without the line being printed here.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_edgar` | `op: http_fetch@1` |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_edgar` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `validate` | `op: finetype_validate@1` |
| 7 | `stamp_descriptor` | `command:` (shell, sha256 `7a51148f8f93`) |

<!-- /protocol-steps -->
