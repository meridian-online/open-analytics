# `source.naics` — NAICS Industry Classification (2022)

The 2022 North American Industry Classification System — the standard taxonomy for
classifying business establishments by industry, from broad sectors down to specific
national industries. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `source.naics`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** — download the Census `2022_NAICS_Descriptions.xlsx` workbook. The 2022
   vintage is effectively frozen.
2. **load / normalise** (`models/load.sql`) — read the workbook (DuckDB's `excel`
   extension) and project the published 4-field schema (`code`, `title`, `level`,
   `description`). Strip the trilateral-comparable `T` marker some titles carry, map
   the placeholder `'null'` description to a real NULL, and derive `level` from the
   code shape.
3. **package** (`models/package.sql`) — add the search `corpus`, sort by `code`, write
   the terminal zstd Parquet.

Produces `build/naics.parquet`.

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip
— stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
`build/naics.parquet`; it is not an arcform step.

## Run

Needs the `arc` binary and `duckdb` (with the `excel` extension), plus `curl` for the
fetch: `arc run`.
