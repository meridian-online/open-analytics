# `source.gleif` — GLEIF Legal Entity Identifiers

The Global Legal Entity Identifier Foundation's public register of legal entities —
every entity with its 20-character LEI, legal name, country, jurisdiction and
registration status. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `scripts/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `source.gleif`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** (`scripts/fetch_gleif.sh`) — resolve the latest GLEIF golden-copy full
   CSV from the publishes API, download (~470 MB zip) and unzip it. The golden copy is
   republished daily.
2. **load / normalise** (`models/load.sql`) — read the ~200-column golden copy as
   text and project the published 10-field schema (`lei`, `legal_name`, `country`,
   `city`, `jurisdiction`, `legal_form`, `entity_status`, `registration_status`,
   `initial_registration`, `next_renewal`); slice the two ISO timestamps to plain
   dates. The register's own values pass through verbatim.
3. **package** (`models/package.sql`) — add the search `corpus`, sort by
   `country, legal_name` for row-group pruning, write the terminal zstd Parquet.

Produces `build/gleif.parquet`.

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip
— stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
`build/gleif.parquet`; it is not an arcform step.

## Run

Needs the `arc` binary and `duckdb`, plus `curl`, `jq` and `unzip` for the fetch:
`arc run`.
