# `census.naics_2022` — NAICS Industry Classification (2022)

The 2022 North American Industry Classification System — the standard taxonomy for
classifying business establishments by industry, from broad sectors down to specific
national industries. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `build/datapackage.json` | **Descriptor** — what it is (built by `arc run`, not committed: the open lake holds the published copy) | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `census.naics_2022` (alias `source.naics`), manifest pointer |

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
4. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
5. **publish** — upsert `census.naics_2022` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `census/naics_2022.parquet` (see *Publish*).

Produces `build/naics.parquet`.

## Publish

The last step publishes `census.naics_2022` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/census/naics_2022.parquet>. Each publish upserts the table on `code`: codes the Census adds are inserted, changed codes updated, and codes it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing.

`census.naics_2022` is already in the lake, so before a live publish check that the rebuilt table still has the live table's shape: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table census.naics_2022 --parquet datasets/naics/build/naics.parquet` from the repository root must print `shape ok`. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary, `duckdb` (with the `excel` extension), `uv` (for the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`), plus `curl` for the fetch: `arc run`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 10 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_naics` | `op: http_fetch@1` |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_naics` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `validate` | `op: finetype_validate@1` |
| 7 | `stamp_descriptor` | `op: uv@1` |
| 8 | `emit_descriptors` | `op: uv@1` |
| 9 | `check_before_publish` | `op: uv@1` |
| 10 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
