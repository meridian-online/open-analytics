# OurAirports ICAO list Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Protocol `datasets/ourairports/` that builds `library/ourairports`, one row per airport with an ICAO code from OurAirports' files, meeting the code-list contract, tested by `arc test`, and ready to publish after merge.

**Architecture:** A copy of the FIPS Protocol's shape (`datasets/fips/`): typed `op:` fetches, a `load.sql` that reads and checks the files and builds the rows, `package.sql` adding `corpus`, export, describe, `check_code_list`, validate, stamp and `publish`. The release comes from a before/after bracket of GitHub's latest-commit API, as HTS brackets USITC's export.

**Tech Stack:** arc built at arcform `f57ff3c`, DuckDB 2.0 CLI, finetype 0.6.67 with its DuckDB extension (`FINETYPE_DUCKDB_EXT`), uv.

**Spec:** `docs/superpowers/specs/2026-11-16-ourairports-design.md`

## Global Constraints

- Address `library/ourairports`; lake table `library.ourairports`; readable copy `library/ourairports.parquet`.
- `engine_version: ">=2.0"`; uv steps pin `duckdb==2.0.0`; the `check_code_list` and `stamp_descriptor` sha256 values are FIPS's (same scripts).
- Every fetch sends `User-Agent: "Meridian Protocol (open-analytics; research@meridian.online)"`.
- Data URLs: `https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/{airports,countries,regions}.csv`. Release URL: `https://api.github.com/repos/davidmegginson/ourairports-data/commits?sha=main&per_page=1`.
- `iata_code` and `keywords` never appear in any built table after `load`.
- One writer per file: export and describe write `build/stage/`; `stamp_descriptor` alone writes `build/ourairports.parquet` and `datapackage.json`.
- `datapackage.json` is gitignored in the Protocol until the first publish (after merge). No registry entry or README Datasets row on this branch.
- Public repo: no Kata refs, personal repo names or local paths in tracked files, commits or PR text.

## Review Focus

1. A commit landing between the two release fetches: the run must fail, not publish under either date. (Task 1 break test.)
2. An airport whose region belongs to another country (OurAirports data error): refused, not published with a wrong `region_name`. (Task 1 break test.)
3. A quoted name containing a comma or quote (42 such rows today): read intact. (Task 1 fixture row and pass check.)
4. A `-U-A` placeholder region: kept, with its region name, and not refused as malformed. (Task 1 fixture row and pass check.)
5. A row with an IATA code also in `keywords`: neither value survives anywhere in the output. (Task 1 pass check over every VARCHAR column.)

---

### Task 1: The Protocol builds and refuses, under `arc test`

**Files:**
- Create: `datasets/ourairports/arcform.yaml`, `models/load.sql`, `models/package.sql`, `tests.yaml`, `testdata/{airports.csv,countries.csv,regions.csv,commit.json,README.md}`, `.gitignore` (`build/`, `datapackage.json`), `.env.example` (as FIPS's)
- Create (minimal, so the run reaches the end): `descriptor.overrides.json`, `nominations.finetype.json`, `schema.finetype.json` — completed in Task 2.

**Interfaces:**
- Produces: steps `fetch_release_before`, `fetch_airports`, `fetch_countries`, `fetch_regions`, `fetch_release_after` (outs `build/ourairports/release_before.json`, `airports.csv`, `countries.csv`, `regions.csv`, `release_after.json`), `load` → table `ourairports`, `package` → `ourairports_out`, `export_ourairports` → `build/stage/ourairports.parquet`, `describe` → `build/stage/described.json`, `check_code_list` (`--levels airport`), `validate`, `stamp_descriptor`, `publish` (table `library.ourairports`).
- `ourairports_out` columns, in order: `code, code_display, level, depth, parent_code, description, description_full, release, text_source, corpus, ourairports_id, ident, type, latitude_deg, longitude_deg, elevation_ft, continent, iso_country, country_name, iso_region, region_name, municipality, scheduled_service, gps_code, local_code, home_link, wikipedia_link`. Types: `depth` INTEGER, `ourairports_id` BIGINT, lat/long DOUBLE, `elevation_ft` INTEGER, `scheduled_service` BOOLEAN, rest VARCHAR.

- [ ] **Step 1: Cut fixtures** from the 2026-11-16 files with DuckDB, keeping OurAirports' header and quoting. Airports: about 25 rows including EGLL, KJFK, KLAX (IATA in `local_code`), a row whose `keywords` holds its IATA code, a row with `ident <> icao_code`, a heliport and a seaplane base with ICAO codes, an accented name, a name with a comma or quote, a `-U-A` region, a row with NULL `elevation_ft` and `municipality`, and three rows with no `icao_code` (one `closed`). Countries and regions: only those the kept rows use. `commit.json`: the API response trimmed to `[{"sha": …, "commit": {"committer": {"date": "2026-11-16T02:53:14Z"}}}]`. README lists every kept row and why.

- [ ] **Step 2: Write `tests.yaml`**: pass case `the fixture builds the code list` (fixtures for all five fetches; `fetch_release_before` and `fetch_release_after` both `testdata/commit.json`) with checks, each returning no rows on success: row count equals the ICAO rows in the fixture; no row lacks an ICAO code; the column list equals the Interfaces list exactly (from `information_schema.columns`); `release` is `2026-11-16` on every row; EGLL's name, country and region names, `scheduled_service`; KLAX keeps `local_code` `LAX`; no VARCHAR column of any row equals the fixture row's IATA code from `keywords`, and no value contains its keywords text; the accented and quoted names read intact; the `-U-A` row has its region name; a row with `ident <> icao_code` has `code` = its ICAO code. Breaks, each `fails_with` load's own text:
  - `fetch_release_after`: `UPDATE fixture SET sha = 'x'` → `"OurAirports changed during the fetch"`
  - `fetch_airports`: rename column `icao_code` → `"OurAirports airports header has changed"`
  - `fetch_countries` / `fetch_regions` header renames → `"countries header has changed"` / `"regions header has changed"`
  - duplicate ICAO (set one row's `icao_code` to `EGLL`) → `"repeats an ICAO code: EGLL"`
  - malformed ICAO (`'EGL1'`) → `"ICAO code is not four capital letters"`
  - unknown country → `"country not in OurAirports' countries file"`
  - region of another country (set EGLL's `iso_region` to a kept US region) → `"region is not in the airport's country"`
  - `type = 'spaceport'` → `"airport type is not one load knows"`
  - `scheduled_service = 'maybe'` → `"scheduled_service is not yes or no"`
  - `latitude_deg = '91'` → `"latitude is not a number between -90 and 90"`

- [ ] **Step 3: Run `arc test` and see it fail** (no Protocol yet): `cd datasets/ourairports && FINETYPE_DUCKDB_EXT=… arc test` → failures.

- [ ] **Step 4: Write `arcform.yaml`, `load.sql`, `package.sql`** after FIPS's. `load` reads each CSV with explicit `columns`, `header = false`, `all_varchar` semantics and finds its header row by text (exactly one row equal to the expected names, else the header error). Release: both JSON files read with `read_json`; error if the shas differ; `release = strftime(committer date::TIMESTAMPTZ AT TIME ZONE 'UTC', '%Y-%m-%d')`, error if it does not parse. Filter to `icao_code IS NOT NULL`; drop `iata_code` and `keywords` there. Checks per spec "What `load` refuses", each a CTE with `error()`; types set `small_airport, medium_airport, large_airport, heliport, seaplane_base`. Must parse under arc (no `DESCRIBE` subquery, no `E''` strings).

- [ ] **Step 5: Run `arc test`** → `N passed, 0 failed`. Also `arc run` twice with no credential against live data: first succeeds and reports publishing into `build/lake/`, second skips every step, no "could not parse" warning.

- [ ] **Step 6: Commit** `datasets/ourairports` (not `build/`, not `datapackage.json`).

### Task 2: Descriptor, drift contract, README and the repo's checks

**Files:**
- Modify: `datasets/ourairports/descriptor.overrides.json`, `nominations.finetype.json`, `schema.finetype.json`
- Create: `datasets/ourairports/README.md`
- Modify: `.github/workflows/descriptors.yml` (protocol-tests matrix `[fips, ourairports]`), `scripts/test_manifest_one_writer.py` (`ONE_WRITER` gains `"ourairports"`), `docs/code-lists.md` (OurAirports in the intro, layout, release and rulings sections), `datasets/label-agreement.json` if strict needs reasons

**Interfaces:**
- Consumes: Task 1's columns and step names.

- [ ] **Step 1: Overrides** per spec "Descriptor": name `ourairports`, title "OurAirports — Airports with ICAO Codes", description with the credit, no-guarantee line, nightly/fortnightly cadence and IATA note; `other-pd` licence "Public domain (OurAirports)" at `https://ourairports.com/data/`; sources (data page, data dictionary `https://ourairports.com/help/data-dictionary.html`, the three file URLs, the commits API); contributors; `resource.path` `https://openlake.meridian.online/library/ourairports.parquet`; `homepage` `https://meridian.online/datasets/library/ourairports`; `x-meridian-code-list` `{"levels": ["airport"]}`; `primaryKey` `["code"]`; a description for every field (`local_code` says it is the national authority's identifier, the FAA's in the US, and not an IATA code); constraints: `code`/`code_display` `^[A-Z]{4}$` length 4, `release` date pattern, enums on `level`, `text_source`, `type`, `continent`, `iso_country` `^[A-Z]{2}$`.
- [ ] **Step 2: Nominations**: `code`, `code_display` → `geography.transportation.icao_code`; `description`, `description_full`, `corpus`, `municipality` → `representation.text.plain_text` where inference says otherwise; `release` as needed, each with a true reason.
- [ ] **Step 3: `schema.finetype.json`** hand-written as FIPS's: string-only keywords enforcing the overrides' shapes and enums, `$comment` saying what is and is not enforced.
- [ ] **Step 4: README** following FIPS's sections, with the generated step block: `python3 scripts/check_protocol_readme.py --write` (or the flag the script documents) then `python3 scripts/check_protocol_readme.py` → passes.
- [ ] **Step 5: Run the checks with the built descriptor in place** (after `arc run` with no credential): `check_label_agreement.py --strict`, `check_frictionless.py`, `check_code_list.py --parquet build/ourairports.parquet --levels airport`, `test_manifest_one_writer.py`, `test_check_protocol_readme.py`, the manifest loader at `ARC_REV` over all manifests, and `arc test` again → all pass. Then remove the built `datapackage.json`'s effect: it stays untracked.
- [ ] **Step 6: Commit.**

### Task 3: Fold, review, pull request

- [ ] Fold the spec's rulings into `docs/code-lists.md`; delete the spec and plan; commit.
- [ ] `roborev review --branch --wait`; fix or answer Medium and above; close it.
- [ ] Push, open the PR (description: what it builds, tests and CI totals, rulings, after-merge steps), `gh pr checks --watch`, link it in Forge.
