# `cms/hcpcs` — HCPCS Level II

HCPCS Level II, the alpha-numeric codes the Centers for Medicare & Medicaid Services (CMS) maintain for billing supplies, drugs, equipment and services that CPT does not cover: one row per code in CMS's quarterly file, current and terminated, with its long and short description, its dates, and CMS's pricing, coverage, BETOS and type-of-service fields. Each snapshot records CMS's quarter and the date of its file. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, the code-list declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `cms/hcpcs`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — CMS's alpha-numeric HCPCS zip for the quarter, from the HCPCS quarterly update page.
2. **extract** — the fixed-width codes file from the zip (`HCPC2026_OCT_ANWEB_09232026.txt` for October 2026). The zip's Excel copy, record layout, transaction report, corrections, not-otherwise-classified list and processing notes are not read.
3. **load** (`models/load.sql`) — read CMS's 320-character records by its record layout: one row per code from its first record, with the long description's continuation records joined with a space. Modifier records are not read. Drops the D series and every CPT code (see below), and stamps `release` from the file's name.
4. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `code`.
6. **check_licence** (`models/check_licence.sql`) — fail the run if the exported file holds a D code, a code that is not a letter and four digits, a cross-reference that is a CPT or D code, or text holding a CPT-shaped code or naming a D code.
7. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
8. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
9. **validate** — gate the Parquet against `schema.finetype.json`.
10. **stamp_descriptor** — write the published `build/hcpcs.parquet` with the finished descriptor in its footer, and `datapackage.json` beside it.
11. **publish** — upsert `cms.hcpcs` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `cms/hcpcs.parquet` (see *Publish*).

Produces `build/hcpcs.parquet`.

## What is left out, and why

HCPCS Level II's codes and descriptors are a US Government work, with two exceptions that bind this dataset.

- **Dental codes.** The D series is the American Dental Association's Current Dental Terminology (CDT), which the ADA holds copyright in. CMS's alpha-numeric file does not carry the D series today; `load` drops any D code it finds, and `check_licence` fails the run if one reaches the file.
- **CPT.** HCPCS Level I is the American Medical Association's CPT. CMS's file carries no CPT code as a row, but it cites CPT codes in two places, and neither is published here. Its cross-reference field points at a CPT code for 42 codes in October 2026; those cross-references are dropped and only Level II codes are kept. Fifteen of its long descriptions (G, M, S and V codes) cite CPT codes by number, such as "list separately in addition to cpt codes 99306, 99310"; each five-digit number, and each four digits followed by F or T (CPT's category II and III forms), is replaced by `[CPT code]`, and `text_source` is `cms_cpt_redacted` on those rows. The words "cpt code" in CMS's own text, without a number, are kept.

Modifiers (CMS's record types 7 and 8) are not included: they are not codes on this list and would need a table of their own.

## Levels and text

The list is flat: every row is level `code` at depth 0 with no parent, so `description_full` is `description`. CMS publishes no titles for the letter a code starts with, and the best-asset rule fills a gap only from a source cleared for redistribution, so the letter is not made a level. `text_source` is `cms` or `cms_cpt_redacted`.

## The release

`release` is CMS's quarter and the date in the file's name, `<year>-<month>/<file date>`: `HCPC2026_OCT_ANWEB_09232026.txt` is `2026-10/2026-09-23`. `load` reads it from the extracted file's name, so it cannot disagree with the file the rows came from, and fails the run if the directory holds other than one such file or the name does not parse.

CMS publishes each quarter at its own URL, and arc does not substitute a param into an operator's `with:`, so a new quarter changes two literals in `arcform.yaml`: `fetch_hcpcs`'s URL and `extract_hcpcs`'s member. Update the October 2026 source in `descriptor.overrides.json` at the same time.

## The code column

finetype labels `code` `identity.medical.hcpcs` from its values, with no nomination. Its canonical pattern, `^[A-V]\d{4}$`, admits the D series; `descriptor.overrides.json` declares the shape the column holds, `^[A-CE-V][0-9]{4}$`, and `schema.finetype.json` enforces the same.

## Publish

The last step publishes `cms.hcpcs` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/cms/hcpcs.parquet>. Each publish upserts the table on `code`: codes CMS adds are inserted, changed codes updated, and codes it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing.

`cms.hcpcs` is already in the lake, so before a live publish check that the rebuilt table still has the live table's shape: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table cms.hcpcs --parquet datasets/hcpcs/build/hcpcs.parquet` from the repository root must print `shape ok`. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for `check_code_list` and the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. The full run over CMS's 3.1 MB file takes a few seconds.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 11 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_hcpcs` | `op: http_fetch@1` |
| 2 | `extract_hcpcs` | `op: archive_extract@1` |
| 3 | `load` | `sql: models/load.sql` |
| 4 | `package` | `sql: models/package.sql` |
| 5 | `export_hcpcs` | `op: parquet_export@1` |
| 6 | `check_licence` | `sql: models/check_licence.sql` |
| 7 | `describe` | `op: datapackage_describe@1` |
| 8 | `check_code_list` | `op: uv@1` |
| 9 | `validate` | `op: finetype_validate@1` |
| 10 | `stamp_descriptor` | `op: uv@1` |
| 11 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
