# `census/naics_2022_titles` — the 2022 NAICS index

The U.S. Census Bureau's 2022 NAICS index file as a title index: one row per index entry, a short description of a kind of business (Soybean farming, field and seed production; Paramedics' offices; Solar farms), with the six-digit national industry Census codes it to and that industry's title in [`census/naics_2022`](../naics/). An analyst coding free-text business descriptions, from company profiles, permit and licence records or survey answers, searches or joins the descriptions here to find the NAICS code, as SOC's job-title indexes do for occupations. Census's files are U.S. Government works in the public domain. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `build/datapackage.json` | **Descriptor** — what it is (built by `arc run`, not committed: the open lake holds the published copy) | schema, finetype labels, the title-index declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `census/naics_2022_titles`, manifest pointer |

`census/naics_2022_titles` was first published on 2026-11-17, after the Protocol merged, as FIPS and OurAirports were: snapshot 18 of the open lake, 20,373 entries at release 2022. The descriptor, registry entry, the repository README's Datasets row and the reasons `datasets/label-agreement.json` gives for this descriptor's fields were committed together after that publish, since CI holds every committed descriptor to the object the lake serves.

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — Census's 2022 NAICS index file, and its 2022 NAICS descriptions workbook, the file `census/naics_2022` is built from.
2. **load_naics** (`../naics/models/load.sql`) — build the NAICS code list with `census/naics_2022`'s own model, so the index is checked against the codes of that list in the same Run (see *The code list*).
3. **load** (`models/load.sql`) — check the index file's header, read every entry, refuse a malformed one, and leave out Census's cross-references.
4. **titles** (`models/titles.sql`) — build the index in the title-index contract's columns, with each code's title from the code list.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `entry_id`.
6. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_inputs**, **check_titles** — hold the index to the shared job-title index contract (`../../contracts/check_titles.sql`, see `docs/code-lists.md`): every code a national industry of the code list, every key present and unique, one release.
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the published `build/naics_2022_titles.parquet` with the finished descriptor in its footer, and `build/datapackage.json` beside it.
10. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. It runs before the stamp, which keeps the lake's `created` when it writes the object the lake already serves. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
11. **publish** — write `census.naics_2022_titles` into the open lake, with its descriptor in the lake's `meta` schema and the readable copy at `census/naics_2022_titles.parquet` (see *Publish*).

Produces `build/naics_2022_titles.parquet`.

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 14 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_index` | `op: http_fetch@1` |
| 2 | `fetch_naics` | `op: http_fetch@1` |
| 3 | `load_naics` | `sql: ../naics/models/load.sql` |
| 4 | `load` | `sql: models/load.sql` |
| 5 | `titles` | `sql: models/titles.sql` |
| 6 | `export_naics_2022_titles` | `op: parquet_export@1` |
| 7 | `describe` | `op: datapackage_describe@1` |
| 8 | `check_inputs` | `sql: models/check_inputs.sql` |
| 9 | `check_titles` | `sql: ../../contracts/check_titles.sql` |
| 10 | `validate` | `op: finetype_validate@1` |
| 11 | `emit_descriptors` | `op: uv@1` |
| 12 | `stamp_descriptor` | `op: uv@1` |
| 13 | `check_before_publish` | `op: uv@1` |
| 14 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->

## The table

The seven columns of the job-title index contract, and nothing after them: `entry_id`, `title`, `code`, `code_description`, `release`, `text_source` and `corpus`.

- `entry_id` is `<code>|<title>` (`111110|Soybean farming, field and seed production`), since Census gives its entries no id. No description appears under two codes in Census's file, so the description alone is unique as well; the key carries the code anyway, as BLS's does.
- `title` is Census's description with spaces trimmed from its ends (41 entries end in one or more spaces). Runs of spaces inside it (13 entries, such as `Veterans'  membership organizations`) and Census's curly apostrophes (`nurses’`) are kept as Census prints them; `corpus` collapses the runs.
- `code` is the six-digit national industry, as text. `code_description` is that industry's `title` in `census/naics_2022` (`Soybean Farming`), not its `description`, which there is Census's paragraph about the industry.
- `release` is `2022`, the edition (see *The release*). `text_source` is `census`.

On Census's file of 2026-11-17 the index has 20,373 entries under 1,010 of the 1,012 national industries. Two have none: 112130 (Dual-Purpose Cattle Ranching and Farming) and 541120 (Offices of Notaries). Census's 25 cross-references come last in its file, with `******` in the code cell and text that points elsewhere (`Dealers -- see type`). They name no code, so they are not entries and are left out, as ONS's cross-references are left out of its coding index.

## What `load` refuses

The run fails on any of these, each with an `error()` naming what it found:

- a header row that is not `NAICS22`, `INDEX ITEM DESCRIPTION`;
- an entry with no code, or with no description;
- a code that is neither six digits nor Census's `******`;
- a `******` row whose text does not read as a cross-reference (`… -- see …`), so an entry whose code Census blanked is not dropped as one.

The contract check then fails the run on a code that is not a national industry of the code list built in the Run, and on a repeated `entry_id`.

## The code list

The contract holds every entry's code to the code list built in the same Run. `census/naics_2022` is built by a Protocol of its own, `../naics`, so this Protocol fetches the same Census descriptions workbook and runs that Protocol's model, `../naics/models/load.sql`, as Schedule B runs HTS's outline model by relative path. The index is then checked without reading the lake, against exactly the codes `census/naics_2022` publishes from the same file, and a change to how NAICS reads Census's workbook reaches both.

## The release

Census's index file names no release. It has no title block, and the only date it carries is the save time Excel wrote into the workbook's properties (2022-02-04), which is not Census's statement of a revision. NAICS 2022 is a fixed edition, and the file sits under Census's `2022NAICS` folder, so `release` is the edition, `2022`. Census's next edition, NAICS 2027, will come as a new file and a new table.

## Tests

`tests.yaml` holds the Protocol's tests, which `arc test` runs from this folder against the cut-down Census workbooks in `testdata/` (see `testdata/README.md`): each test runs the real steps in a copy of the Protocol, with the fixtures in place of the fetches and the publish left out. One case builds the fixture and holds the table to inline checks (the entries per code, no cross-reference, the key, the code titles, the trimmed and collapsed text, the curly apostrophe); five breaks rewrite the index fixture with SQL and show `load` refuses it with its own message; and two show the contract check refuses a code the code list lacks and a repeated entry. CI runs them in the `protocol-tests` job.

## Publish

The last step publishes `census.naics_2022_titles` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/census/naics_2022_titles.parquet>. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary at a revision with `arc test` (arcform main from `f57ff3c`; no tagged release has it yet), `duckdb`, `uv` (for the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`, and `arc test` for the tests. The full run over Census's two workbooks takes under a minute; the tests take under two minutes.
