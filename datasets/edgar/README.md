# `sec.company_tickers_exchange` — SEC EDGAR company tickers

The U.S. Securities and Exchange Commission's company-tickers file from its EDGAR
system: one row per ticker symbol, with the Central Index Key (CIK), EDGAR conformed
company name and exchange it is associated with. A company with several listed
securities has several rows — 1RT Acquisition Corp. (CIK 2054272) has ONCH, ONCHU and
ONCHW — so the key is (`cik`, `ticker`), not `cik`. The SEC updates the file to support
EDGAR searches and [does not guarantee its accuracy or
scope](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data):
it is a ticker list, not a register of every company with securities registered with
the SEC. A clean **information-retrieval** dataset: the EDGAR ↔ GLEIF
entity-resolution join is *not* done here — it lives in the `library.edgar_lei`
Protocol. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `sec.company_tickers_exchange` (alias `source.edgar`), manifest pointer |

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
4. **publish** — upsert `sec.company_tickers_exchange` into the open lake on (`cik`, `ticker`), with its descriptor in the lake's `meta` schema and the readable copy at `sec/company_tickers_exchange.parquet` (see *Publish*).

Produces `build/edgar.parquet`.

### `cik` is an integer, in the build and in the served object

**`cik` is a Parquet integer (`INT64`, DuckDB `BIGINT`) in what this Protocol builds,
in the object served at `openlake.meridian.online/sec/company_tickers_exchange.parquet`, and in
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

## Publish

The last step publishes `sec.company_tickers_exchange` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/sec/company_tickers_exchange.parquet>. Each publish upserts the table on (`cik`, `ticker`): tickers the SEC adds are inserted, changed rows updated, and tickers it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing.

`sec.company_tickers_exchange` is already in the lake, so before a live publish check that the rebuilt table still has the live table's shape: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table sec.company_tickers_exchange --parquet datasets/edgar/build/edgar.parquet` from the repository root must print `shape ok`. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

Do not publish from `main` yet: the DuckDB pins in `arcform.yaml` (`engine_version` and the stamp step's `duckdb==`) still name 1.x, and they move to 2.0 with the descriptor in the next change.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`), plus `curl` for the fetch: `arc run`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 8 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_edgar` | `op: http_fetch@1` |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_edgar` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `validate` | `op: finetype_validate@1` |
| 7 | `stamp_descriptor` | `op: uv@1` |
| 8 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
