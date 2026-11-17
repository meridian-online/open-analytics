# `library/ourairports` — OurAirports' airports with ICAO codes

OurAirports' airports that carry an ICAO code, the four-letter code air traffic control, flight plans and airspace data use for an airport: one row per ICAO code, with OurAirports' name for the airport, its type, coordinates and elevation, its country and region codes and names, the place it serves, whether it has scheduled airline service, its GPS and local codes, and links to its website and Wikipedia article. An analyst holding flight, airspace or operations data keyed by ICAO code joins it here to turn the code into an airport, a place and a country. OurAirports (<https://ourairports.com/>) releases its data to the public domain, with no guarantee of accuracy; it is one project rather than an issuing institution, so the list is filed in `library` under the dataset's own name. Each snapshot records the date of the OurAirports commit it was built from. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `build/datapackage.json` | **Descriptor** — what it is (built by `arc run`, not committed: the open lake holds the published copy) | schema, finetype labels, the code-list declaration |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `library/ourairports`, manifest pointer |

`library/ourairports` was first published on 2026-11-17, after the Protocol merged, as FIPS was: snapshot 13 of the open lake, 10,734 rows at release 2026-11-16. The descriptor, registry entry, the repository README's Datasets row and the reasons `datasets/label-agreement.json` gives for this descriptor's fields were committed together after that publish, since CI then held every committed descriptor to the object the lake serves and read only committed descriptors. The repository no longer commits a descriptor: the open lake holds the published copy.

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version. The step DAG:

1. **fetch** — the latest commit on the `main` branch of OurAirports' data repository, from GitHub's commits API; the git tree of the repository's top folder at `main`, from its git trees API; OurAirports' `airports.csv`, `countries.csv` and `regions.csv` from `raw.githubusercontent.com`; and the latest commit again (see *The release*).
2. **verify** (`models/verify.sql`) — check that both commit fetches name the same commit, that the tree is that commit's and that each CSV is the file at that commit, and write the commit and its UTC date to `build/ourairports/release.csv`.
3. **load** (`models/load.sql`) — read each CSV with every field as text, check its header and every row, and build one row per airport that carries an ICAO code, with its country and region names from OurAirports' own lookup files. Stamps `release` from `release.csv`.
4. **package** (`models/package.sql`) — add the search `corpus`, hand off the terminal table.
5. **export** — write the zstd Parquet to `build/stage/`, ordered by `code`.
6. **describe** — emit the descriptor from the Parquet, typed by finetype with `nominations.finetype.json` and `descriptor.overrides.json` applied.
7. **check_code_list** — hold the Parquet to the shared code-list contract (`scripts/check_code_list.py`, see `docs/code-lists.md`).
8. **validate** — gate the Parquet against `schema.finetype.json`.
9. **stamp_descriptor** — write the published `build/ourairports.parquet` with the finished descriptor in its footer, and `build/datapackage.json` beside it.
10. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`. It runs before the stamp, which keeps the lake's `created` when it writes the object the lake already serves. `check_before_publish` then holds the built descriptor to label agreement with them, to the frictionless reference implementation, and to `scripts/check_descriptors.py`'s rules read against the local Parquet. A failing check stops the run before publish.
11. **publish** — upsert `library.ourairports` into the open lake on `code`, with its descriptor in the lake's `meta` schema and the readable copy at `library/ourairports.parquet` (see *Publish*).

Produces `build/ourairports.parquet`.

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 17 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_release_before` | `op: http_fetch@1` |
| 2 | `fetch_tree` | `op: http_fetch@1` |
| 3 | `fetch_airports` | `op: http_fetch@1` |
| 4 | `fetch_countries` | `op: http_fetch@1` |
| 5 | `fetch_regions` | `op: http_fetch@1` |
| 6 | `fetch_release_after` | `op: http_fetch@1` |
| 7 | `verify` | `sql: models/verify.sql` |
| 8 | `load` | `sql: models/load.sql` |
| 9 | `package` | `sql: models/package.sql` |
| 10 | `export_ourairports` | `op: parquet_export@1` |
| 11 | `describe` | `op: datapackage_describe@1` |
| 12 | `check_code_list` | `op: uv@1` |
| 13 | `validate` | `op: finetype_validate@1` |
| 14 | `emit_descriptors` | `op: uv@1` |
| 15 | `stamp_descriptor` | `op: uv@1` |
| 16 | `check_before_publish` | `op: uv@1` |
| 17 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->

## The table

A flat list of one level, `airport`, as `cms/hcpcs` is: every row is at depth 0 with a NULL `parent_code`, and `description_full` is `description`. `code` and `code_display` are OurAirports' `icao_code`, four capital letters. They are not read from `ident`: OurAirports' data dictionary defines `ident` as the ICAO code if available and otherwise a local or internally generated code, and on 2026-11-16, 845 of the 10,734 airports with an ICAO code have an `ident` that differs from it. `ident` is kept as a column of its own, as is `ourairports_id`, OurAirports' record id, which joins back to its runways, frequencies and other files.

After the core columns come OurAirports' fields: `ourairports_id`, `ident`, `type` (large, medium and small airports, heliports and seaplane bases; no closed airport or balloonport carries an ICAO code), `latitude_deg`, `longitude_deg`, `elevation_ft`, `continent`, `iso_country` and `country_name`, `iso_region` and `region_name`, `municipality`, `scheduled_service`, `gps_code`, `local_code`, `home_link` and `wikipedia_link`. `country_name` and `region_name` come from OurAirports' `countries.csv` and `regions.csv`. `iso_region` is the ISO 3166-2 code where one is assigned, and OurAirports' own code where it is not, such as `BO-U-A`, which it names "(unassigned)".

Airports with no ICAO code (75,634 of OurAirports' 86,368 on 2026-11-16) are not included: they have no code other data carries in ICAO form. OurAirports' runways, frequencies and navaids files are not read.

## IATA codes are left out

IATA's airport codes are proprietary, so IATA's code list is not republished. OurAirports' `iata_code` column is read only so the header can be checked, and is not in the table. Its `keywords` column is dropped too: it is free text in which IATA codes appear (on 58 airports it holds the airport's own IATA code as a word), alongside city codes and former codes that cannot be told apart from other words.

The rule does not blank another issuer's identifier whose letters happen to equal an IATA code. `local_code` is the code a country's own aviation authority gives the airport: in the US the FAA's location identifier, which IATA adopts for US airports, so JFK's `local_code` is `JFK`. On 2026-11-16, of the 7,823 ICAO airports with an IATA code, `local_code` equals it on 1,712 (1,525 in the US), `ident` on 29 (where `ident` fell back to the local code), and `gps_code` on none. Those values are the FAA's and other national authorities' public identifiers, recorded by OurAirports as such, and the field's description says `local_code` is not an IATA code.

## What `load` refuses

The run fails on any of these, each with an `error()` naming what it found:

- a header that is not OurAirports', in any of the three files;
- an ICAO code that is not four capital letters, or one that repeats;
- fewer than 5% of the airports file's rows carrying an ICAO code (12.4% on 2026-11-16, 10,734 of 86,368), a guard against OurAirports dropping most of its ICAO codes, which every per-row check would pass;
- an OurAirports `id` that is not an integer;
- a country code `countries.csv` repeats, or a region code `regions.csv` repeats, checked before the join, so a repeated lookup row is not reported as a repeated ICAO code;
- an airport whose country is not in `countries.csv`, whose region is not in `regions.csv`, or whose region is of another country;
- a type outside the five `load` knows, or a `scheduled_service` other than `yes` or `no`;
- a latitude or longitude that is not a number in range, or an elevation that is not an integer;
- an empty name.

## The release

OurAirports' files carry no date. OurAirports commits them to the `main` branch of its data repository on GitHub (`davidmegginson/ourairports-data`), and `release` is the UTC date of the commit OurAirports last made there before the run, the commit the files were read at (`2026-11-16`). GitHub records a commit only when the files change; in practice `airports.csv` changes nightly.

Two fetches of the branch's latest commit from GitHub's commits API bracket a fetch of the git tree of the repository's top folder at `main` from its git trees API, which gives each file's git blob sha, and the three CSV fetches from `raw.githubusercontent.com`. `verify` refuses the run unless both commit fetches name the same commit, each commit date parses, the tree names each file, each CSV's git blob sha, computed over the bytes fetched, equals the tree's, and the tree, hashed as git does from its entries, is the tree the commit names (`commit.tree.sha`). GitHub's API answers are themselves cached for a minute, so the commit and the tree can come from different moments; the tree check ties the one to the other. A bracket on the commit alone is not enough: `raw.githubusercontent.com` caches each file for up to five minutes, so a run just after OurAirports' nightly commit could be served the previous night's bytes under the new commit, and publish yesterday's data with today's date. A run refused for that says GitHub's cache may still hold the previous version, publishes nothing, and is rerun later.

The files are fetched from `raw.githubusercontent.com` rather than from the GitHub Pages site OurAirports' data page links, because the raw files are the branch's own and the blob check can hold them to it; the Pages site redeploys after each commit, on its own schedule. The URLs do not change, so a refresh changes nothing in the Protocol. OurAirports updates nightly and Meridian refreshes fortnightly, so the descriptor states that the data is as of `release`.

## The code column

`code` and `code_display` are nominated as finetype's `geography.transportation.icao_code`, and `descriptor.overrides.json` declares the shape they hold, four capital letters, which `schema.finetype.json` enforces. `gps_code` carries the same label, as finetype infers it: OurAirports' data dictionary says it is the ICAO code where one exists, but on 2026-11-16 it differs from `code` on 17 airports, and one of those (`VA1G`, at `VERW`) has a digit, so it declares four capital letters or digits. Join on `code`.

## Tests

`tests.yaml` holds the Protocol's tests, which `arc test` runs from this folder against the cut-down OurAirports files in `testdata/` (see `testdata/README.md`): each test runs the real steps in a copy of the Protocol, with the fixtures in place of the fetches and the publish left out. One case builds the fixture and holds the table to inline checks; one shows a commit late in the UTC day takes its UTC date, whatever the machine's time zone; one shows a tree with a folder, whose mode GitHub writes as `040000` and git hashes as `40000`, hashes as git hashes it; seven breaks show `verify` refuses a changed commit, an empty commits answer, a file that is not the tree's, a tree without one of the files, a tree that is not the commit's (two ways) and a commit date that does not parse; and eighteen breaks rewrite a fixture with SQL and show `load` refuses it with its own message. CI's `protocol-tests` job runs them on every pull request. `check_code_list`'s own refusals are not tested here, since arc matches only `step 'check_code_list' failed (exit code n)` for a `uv` step; `scripts/test_check_code_list.py` tests the contract.

## Publish

The last step publishes `library.ourairports` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/library/ourairports.parquet>. Each publish upserts the table on `code`: ICAO codes OurAirports adds are inserted, changed rows updated, and codes it no longer lists deleted, all in one snapshot a reader can time-travel back through. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary at a revision with `arc test` (arcform main from `c57f7bf`, the `ARC_REV` in `.github/workflows/descriptors.yml`, since every Protocol sets `on_descriptor_change`; no tagged release has it yet), `duckdb`, `uv` (for `check_code_list` and the stamp), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`, and `arc test` for the tests. The full run over OurAirports' three files takes under a minute; the tests take about five minutes.
