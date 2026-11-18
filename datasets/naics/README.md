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

1. **fetch** — download the Census `2022_NAICS_Descriptions.xlsx` workbook. The 2022 edition is fixed.
2. **load** (`models/load.sql`) — read the workbook (DuckDB's `excel` extension) and build the ladder the code-list contract asks for ([`docs/code-lists.md`](../../docs/code-lists.md)): five levels, sector to national industry, read from the code's shape, each row's `parent_code` one level up (a subsector's parent is the sector whose range covers its first two digits, so 311 sits under 31-33), its `depth`, the code of each level above it, and `description_full`. It strips the trilateral-comparable `T` some titles carry and maps the placeholder `'null'` paragraph to a real NULL, and fails the run on a code of another shape, a repeated code, a missing title, or a row with no parent at the level above. The NAICS index and concordance run this model too, by relative path.
3. **package** (`models/package.sql`) — add the search `corpus` in its place and hand off the terminal table: the contract's ten columns, then `title`, `definition` and the ancestor codes.
4. **export, describe, check, validate** — the zstd Parquet ordered by `code`, its descriptor, the contract check (`scripts/check_code_list.py`) and finetype's drift contract, all on the staged files under `build/stage/`.
5. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. It runs before the stamp, which alone writes the published Parquet and descriptor and keeps the lake's `created` when it writes the object the lake already serves. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
6. **publish** — upsert `census.naics_2022` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `census/naics_2022.parquet` (see *Publish*).

Produces `build/naics.parquet`.

## Tests

`tests.yaml` holds the Protocol's tests, which `arc test` runs from this folder against the cut-down Census workbook in `testdata/` (see `testdata/README.md`): each test runs the real steps in a copy of the Protocol, with the fixture in place of the fetch and the publish left out. One case builds the fixture and holds the table to inline checks (the rows per level, the contract's columns in order, the parents, among them 311 under 31-33 and 441 under 44-45, the ancestor codes, the stripped `T`, `title` equal to `description`, the paragraphs, the edition); six breaks rewrite the fixture with SQL and show `load` refuses it with its own message (a subsector with no sector or two, a missing parent, a code of another shape, a repeated code, a missing title); and one runs the check before publish for real. CI runs them in the `protocol-tests` job. Because the NAICS index and concordance run `models/load.sql` too, a change to it is tested by their `arc test` as well.

## Publish

The last step publishes `census.naics_2022` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/census/naics_2022.parquet>. Each publish upserts the table on `code`: codes the Census adds are inserted, changed codes updated, and codes it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing.

`census.naics_2022` is already in the lake, so before a live publish check that the rebuilt table still has the live table's shape: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table census.naics_2022 --parquet datasets/naics/build/naics.parquet` from the repository root must print `shape ok`. The first publish of the code-list shape is the exception: the contract puts ten columns first, so the check reports the new columns added before existing ones, and the publish step sets `on_schema_change: replace` for that one publish, which replaces the table whole. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary, `duckdb` (with the `excel` extension), `uv` (for the stamp and the checks), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`), plus `curl` for the fetch: `arc run`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 11 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_naics` | `op: http_fetch@1` |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_naics` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `check_code_list` | `op: uv@1` |
| 7 | `validate` | `op: finetype_validate@1` |
| 8 | `emit_descriptors` | `op: uv@1` |
| 9 | `stamp_descriptor` | `op: uv@1` |
| 10 | `check_before_publish` | `op: uv@1` |
| 11 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
