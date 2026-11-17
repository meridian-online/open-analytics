# `census/naics_2017_to_2022` — the 2017 to 2022 NAICS concordance

The U.S. Census Bureau's own concordance from the 2017 North American Industry Classification System to the 2022 edition, as an edition crosswalk: one row per pairing of a 2017 national industry with a 2022 national industry, as Census publishes it. Much federal data is still coded in NAICS 2017 (County Business Patterns through 2023 codes industries as `NAICS2017`), and an analyst moves it onto [`census/naics_2022`](../naics/) by joining on `from_code`. Census's files are U.S. Government works in the public domain. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `build/datapackage.json` | **Descriptor** — what it is (built by `arc run`, not committed: the open lake holds the published copy) | schema, finetype labels, the crosswalk declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `census/naics_2017_to_2022`, manifest pointer |

`census/naics_2017_to_2022` was first published on 2026-11-17, after the Protocol merged, as FIPS, OurAirports and the NAICS index were: snapshot 17 of the open lake, 1,150 rows at release 2022. The descriptor, registry entry, the repository README's Datasets row and the reasons `datasets/label-agreement.json` gives for this descriptor's fields were committed together after that publish, since CI holds every committed descriptor to the object the lake serves.

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — Census's 2017 to 2022 NAICS concordance, and its 2022 NAICS descriptions workbook, the file `census/naics_2022` is built from.
2. **load_naics** (`../naics/models/load.sql`) — build the NAICS code list with `census/naics_2022`'s own model, so the crosswalk is checked against the codes of that list in the same Run (see *The code list*).
3. **load** (`models/load.sql`) — check the concordance's title and header, refuse a value beyond its four columns, read every pairing and refuse a malformed one.
4. **crosswalk** (`models/crosswalk.sql`) — build the crosswalk in the edition crosswalk contract's columns.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `from_code, to_code`.
6. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_inputs**, **check_crosswalk** — hold the crosswalk to the shared edition crosswalk contract (`../../contracts/check_crosswalk.sql`, see `docs/code-lists.md`): every 2022 code a national industry of the code list, every 2017 code six digits, every pair present once, one release.
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the published `build/naics_2017_to_2022.parquet` with the finished descriptor in its footer, and `build/datapackage.json` beside it.
10. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. It runs before the stamp, which keeps the lake's `created` when it writes the object the lake already serves. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
11. **publish** — write `census.naics_2017_to_2022` into the open lake, with its descriptor in the lake's `meta` schema and the readable copy at `census/naics_2017_to_2022.parquet` (see *Publish*).

Produces `build/naics_2017_to_2022.parquet`.

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 14 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_concordance` | `op: http_fetch@1` |
| 2 | `fetch_naics` | `op: http_fetch@1` |
| 3 | `load_naics` | `sql: ../naics/models/load.sql` |
| 4 | `load` | `sql: models/load.sql` |
| 5 | `crosswalk` | `sql: models/crosswalk.sql` |
| 6 | `export_naics_2017_to_2022` | `op: parquet_export@1` |
| 7 | `describe` | `op: datapackage_describe@1` |
| 8 | `check_inputs` | `sql: models/check_inputs.sql` |
| 9 | `check_crosswalk` | `sql: ../../contracts/check_crosswalk.sql` |
| 10 | `validate` | `op: finetype_validate@1` |
| 11 | `emit_descriptors` | `op: uv@1` |
| 12 | `stamp_descriptor` | `op: uv@1` |
| 13 | `check_before_publish` | `op: uv@1` |
| 14 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->

## The table

The seven columns of the edition crosswalk contract, and nothing after them: `from_code`, `from_description`, `to_code`, `to_description`, `from_edition`, `to_edition` and `release`. `(from_code, to_code)` is the key.

- `from_code` and `to_code` are six-digit national industries, as text.
- `from_description` is Census's 2017 title in the concordance, trimmed at its ends. Where Census split a 2017 industry, the title is followed by ` - ` and the piece that went to this 2022 industry (`Anthracite Mining - anthracite surface mining`), on 21 rows.
- `to_description` is Census's 2022 title in the concordance, trimmed at its ends. It is kept as printed, so it can differ from the code's title in `census/naics_2022`.
- `from_edition` is `2017`, `to_edition` and `release` are `2022` (see *The release*).

On Census's file of 2026-11-17 the crosswalk has 1,150 pairings, of 1,057 2017 industries and all 1,012 national industries of 2022. Nine 2017 industries split across several 2022 ones, and 79 2022 industries take pieces of several 2017 ones; Census marks these in bold and italics, which the table does not carry, since each is a count over it. Census gives no weights for apportioning a split industry's counts, and the table carries none.

Texts are kept as Census prints them. Three end in spaces (334614's 2017 title and both of 721310's), which are trimmed. 531120's titles read `excpet` for `except`, in both columns. Census's 2022 to 2017 file has the same pairings; its texts differ from this file's only in correcting that word and in 721310's trailing spaces.

## What `load` refuses

The run fails on any of these, each with an `error()` naming what it found:

- a title in A1 other than `2017 NAICS U.S. Matched to 2022 NAICS U.S. (Full Concordance)`;
- a header row in A3:D3 other than `2017 NAICS Code`, `2017 NAICS Title`, `2022 NAICS Code`, `2022 NAICS Title` (the second cell's first line);
- a value in columns E to Z, where Census's file holds whitespace (in E to I) and nothing else, so a column Census adds is not dropped unseen;
- a pairing with no 2017 or 2022 code, or no 2017 or 2022 title;
- a code that is not six digits.

The contract check then fails the run on a 2022 code that is not a national industry of the code list built in the Run, and on a repeated pair.

## The code list

The contract holds every 2022 code to the code list built in the same Run. `census/naics_2022` is built by a Protocol of its own, `../naics`, so this Protocol fetches the same Census descriptions workbook and runs that Protocol's model, `../naics/models/load.sql`, as the NAICS index does. The crosswalk is then checked without reading the lake, against exactly the codes `census/naics_2022` publishes from the same file.

The 2017 edition is not hosted. Its codes are checked by their shape, six digits, as SOC's 2010 codes are, and its titles are the ones Census prints in the concordance. A 2017 code list would let the run check each 2017 code against the edition, and is a dataset of its own to add when an analyst needs one.

## The release

Census's concordance names no release. Its title names the two editions and no date, and the only date it carries is the save time Excel wrote into the workbook's properties (2021-11-16), which is not Census's statement of a revision. The concordance bridges to the fixed 2022 edition, so `release` is that edition, `2022`, as the NAICS index's is. Census's next edition, NAICS 2027, will come with a concordance of its own and a new table.

## Tests

`tests.yaml` holds the Protocol's tests, which `arc test` runs from this folder against the cut-down Census workbooks in `testdata/` (see `testdata/README.md`): each test runs the real steps in a copy of the Protocol, with the fixtures in place of the fetches and the publish left out. One case builds the fixture and holds the table to inline checks (the pairings, the split's pieces, the editions and release, each 2022 code a national industry, trimmed text, the typo kept); ten breaks rewrite the concordance fixture with SQL and show `load` refuses it with its own message; and two show the contract check refuses a 2022 code the code list lacks and a repeated pair. CI runs them in the `protocol-tests` job.

## Publish

The last step publishes `census.naics_2017_to_2022` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/census/naics_2017_to_2022.parquet>. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary at a revision with `arc test` (arcform main from `c57f7bf`, the `ARC_REV` in `.github/workflows/descriptors.yml`, since every Protocol sets `on_descriptor_change`; no tagged release has it yet), `duckdb`, `uv` (for the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`, and `arc test` for the tests. The full run over Census's two workbooks takes under a minute; the tests take under three minutes.
