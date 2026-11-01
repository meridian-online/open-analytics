# `census/schedule_b` — Schedule B, U.S. export classification codes

Schedule B, the U.S. Census Bureau's classification of exported goods: every ten-digit commodity Census publishes, with a row for each chapter, heading and subheading its codes imply, so a ten-digit export code rolls up to its chapter without a second source. Each commodity carries Census's units of quantity and its SITC, end-use, agricultural, NAICS and high-technology codes. Heading and subheading text comes from the U.S. International Trade Commission's HTS, which Schedule B shares down to six digits. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `scripts/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, the code-list declaration, the NAICS join |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `census/schedule_b`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch and extract** — Census's bulk file `sbsdf.zip` for the year, and `econcord.txt`, the export codes, out of it.
2. **date** (`scripts/member_date.py`) — the date the zip records for `econcord.txt`, written to `build/econcord_date.json`. `archive_extract` does not set a member's date on the file it writes, so the date is read from the zip's own directory.
3. **fetch the index** — the year's `index.html`, which holds the chapter titles and sections.
4. **fetch USITC** — `currentRelease`, the whole HTS as one JSON export, and `currentRelease` again, as the HTS Protocol does, so `load` can fail a run whose revision changed mid-download.
5. **outline** (`../hts/models/outline.sql`) — the HTS Protocol's own file: USITC's rows as an outline, with the text for every 2, 4, 6, 8 and 10-digit prefix.
6. **load** (`models/load.sql`) — read `econcord.txt` by fixed positions, the chapter titles and sections from the index, and build the ladder, its text and `description_full`. It holds the edition guard (below).
7. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
8. **export** — write the unstamped zstd Parquet to `build/stage/`, ordered by `code`.
9. **describe** — emit the staged descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
10. **fetch_naics and derive_join_coverage** — fetch the published `census/naics_2022` object and measure the NAICS join against it (below).
11. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`): the core columns, a parent one level up for every row, `depth` as the level's position.
12. **validate** — gate the Parquet against `schema.finetype.json`.
13. **stamp_descriptor** — write the finished descriptor into the Parquet's footer, and write the published `build/schedule_b.parquet` and `datapackage.json`. It is the only writer of both.

Produces `build/schedule_b.parquet`.

## The year

Census publishes each year's schedule in its own folder (`/schedules/b/2026/`). The Protocol's `year` param (default `2026`) is what `load` stamps into `release` and checks against USITC's revision. arc does not substitute a param into an operator's `with:`, so the two Census URLs carry the year as well: a new January folder changes the param and both URLs together.

## Levels and text

`chapter` (2 digits), `heading` (4), `subheading` (6) and `commodity` (10, the Schedule B number). Schedule B has no eight-digit level. `text_source` says whose text a row's `description` is:

- `census` — a commodity's long description (the short one is in `description_short`), or a chapter title from Census's index page.
- `usitc` — USITC's HTS text for the heading or subheading, from the revision in `hts_release`.
- `census_derived` — a heading or subheading with exactly one Census line under it, taking that line's text. This covers chapter 98, where USITC's provisions are import ones and Schedule B's are export ones, and any prefix USITC gives no text.
- `meridian_label` — such a level with several Census lines under it, labelled by its level and code ("Heading 9802"), since no one line's text stands for the others.

## The edition guard

USITC serves only its current HTS revision, so around a new HS edition (the next on 1 January 2027) the Schedule B year and the USITC revision can belong to different editions. `load` fails the run when the revision's year is not the Schedule B year, or when USITC names fewer than 99% of the headings, or of the subheadings, Schedule B implies outside chapter 98. A failed run publishes nothing; rerun once both issuers are on the same edition.

## The release

`release` is the year folder and the date `sbsdf.zip` records for `econcord.txt`, such as `2026/2026-07-27`. A zip records a member's date as a local date and time with no zone; the date is taken as recorded. On 2026-11-01 Census's file records 2026-07-27 12:38, and 16:38 UTC in its extended timestamp, so the two agree on the day. A tool that shows the extended timestamp in local time far enough east, such as `unzip -l` in Australia, shows 2026-07-28.

## The NAICS join

`naics` is declared as a join to `census/naics_2022`'s `code` in the descriptor's `x-joins`, on commodity rows, not as a `schema.foreignKeys` entry: some of Census's codes are not 2022 NAICS codes. Census writes X for digits it does not specify (`33641X`) and uses its own `910000`, `930000` and `990000` on lines such as waste and scrap, used goods and chapter 98's provisions. `derive_join_coverage` runs `../edgar_gleif/scripts/crosswalk_join.py --write` over the staged build and the fetched NAICS object, and writes `coverage` (commodity rows, and how many match), `measuredAgainst` (the NAICS object's SHA-256) and the first sentence of `cardinality` into the descriptor. CI's `joins-run-against-their-data` job runs the same script without `--write` against the published objects. On 2026-11-01, 9,464 of 9,779 commodity lines match.

The NAICS object is fetched by the Protocol rather than by the script, because openlake's edge refuses Python's default User-Agent.

## The agricultural flag

Census's layout calls the field "Agriculture or non-agriculture product code" and prints `0` or `1` without saying which is which. `agricultural` is true for `0`. The reading is from the data: every line of chapters 1, 2 and 4 is `0`, and every line of chapter 3, fish, which USDA's definition of agricultural products leaves out, is `1`. `load` fails the run on any third value.

## The code column

`code` and `code_display` are nominated as finetype's `geography.transportation.hs_code`; inferred from the values they are `unknown`. The label's canonical pattern, `^\d{4}\.?\d{2}(\.?\d{2}){0,2}$`, admits the 6 and 10-digit forms, dotted or not, but not the 2-digit chapter or 4-digit heading; `describe` widens it with `|^[0-9]{2}$|^[0-9]{4}$` (pattern-fit `widened`), and `schema.finetype.json` does the same, as on `usitc/hts`. `nominations.finetype.json` gives the reason for each nomination.

## Licence and credit

Census's works are in the public domain. The data is credited to the U.S. Census Bureau; Census's bulk file carries no endorsement notice.

## Boundary (deliberate)

**Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer flip — stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output `build/schedule_b.parquet` and the emitted `datapackage.json`; it is not an arcform step.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for the four Python steps) and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. A cold run, with USITC's 12.6 MB export, takes about a minute and a half.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 17 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_schedule_b` | `op: http_fetch@1` |
| 2 | `extract_schedule_b` | `op: archive_extract@1` |
| 3 | `date_schedule_b` | `op: uv@1` |
| 4 | `fetch_index` | `op: http_fetch@1` |
| 5 | `fetch_release_before` | `op: http_fetch@1` |
| 6 | `fetch_hts` | `op: http_fetch@1` |
| 7 | `fetch_release_after` | `op: http_fetch@1` |
| 8 | `outline` | `sql: ../hts/models/outline.sql` |
| 9 | `load` | `sql: models/load.sql` |
| 10 | `package` | `sql: models/package.sql` |
| 11 | `export_schedule_b` | `op: parquet_export@1` |
| 12 | `describe` | `op: datapackage_describe@1` |
| 13 | `fetch_naics` | `op: http_fetch@1` |
| 14 | `derive_join_coverage` | `op: uv@1` |
| 15 | `check_code_list` | `op: uv@1` |
| 16 | `validate` | `op: finetype_validate@1` |
| 17 | `stamp_descriptor` | `op: uv@1` |

<!-- /protocol-steps -->
