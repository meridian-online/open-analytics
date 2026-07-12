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

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip
— stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
`build/edgar.parquet`; it is not an arcform step.

## Run

Needs the `arc` binary and `duckdb`, plus `curl` for the fetch: `arc run`.
