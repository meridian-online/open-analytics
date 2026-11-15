# `census/fips`: FIPS state and county codes

## Goal

Host the Census Bureau's FIPS (ANSI/INCITS) codes for US states and counties as a code list at `census/fips` in the open lake (`meridian.census.fips`, readable copy `census/fips.parquet`), built and published by its own Protocol, with its tests in a `tests.yaml` that `arc test` runs and no Python self-test.

FIPS codes are the join key of most US government data, and the five-digit county code is how ACS, QCEW, BEA and CDC tables name a county. An analyst should be able to join any such table to this list on `code` and get the county's name, its state, and whether Census still uses the code.

## Non-goals

- Places, county subdivisions, tracts, congressional districts and the other ANSI lists. They are larger, have their own vintages, and would be separate datasets.
- Land and water area and internal points from the Gazetteer. Useful, but they are measurements, not codes, and widen the descriptor; later work if wanted.
- The dataset page on meridian.online. It follows the first publish, in the web repo.
- The first publish. It runs after this branch merges.

## Sources

Three Census files, all US Government works in the public domain (`other-pd`).

1. **ANSI state codes**, `national_state2020.txt` (<https://www2.census.gov/geo/docs/reference/codes2020/national_state2020.txt>): 57 rows, the 50 states, DC, Puerto Rico, the four Island Areas and the U.S. Minor Outlying Islands. Columns `STATE|STATEFP|STATENS|STATE_NAME`.
2. **ANSI county codes**, `national_county2020.txt` (same folder): 3,235 rows, with Census's class code (`CLASSFP`) and functional status (`FUNCSTAT`). Columns `STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT`.
3. **The 2026 Gazetteer county file**, `2026_Gaz_counties_national.txt` inside `2026_Gaz_counties_national.zip` (<https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/>): 3,222 counties and county-equivalents of the 50 states, DC and Puerto Rico in Census's current vintage.

The ANSI code-list page Census links (<https://www.census.gov/library/reference/code-lists/ansi.html>) serves only the 2020 vintage. Measured on 2026-11-16, the 2026 Gazetteer and the 2020 ANSI county list agree on every shared code's name and GNIS code, and differ in one place: Connecticut. In 2022 Census replaced Connecticut's eight counties (`09001` to `09015`) with its nine planning regions (`09110` to `09190`) as county-equivalents. Data from 2022 on (ACS 2022 and later, QCEW, BEA) uses the planning regions; older data uses the counties. The 2020 list alone would fail every join to current Connecticut data, and the Gazetteer alone has no Island Areas and no class or status codes.

## Shape

One row per state or state-equivalent and per county or county-equivalent. Levels `state`, `county`.

| Column | Type | Meaning |
|---|---|---|
| `code` | VARCHAR, key | Two digits for a state (`01`), five for a county (`01001`), the form other data carries. |
| `code_display` | VARCHAR | Same as `code`; Census prints FIPS codes as digits. |
| `level` | VARCHAR | `state` or `county`. |
| `depth` | INTEGER | 0 or 1. |
| `parent_code` | VARCHAR | A county's state code; NULL on a state. |
| `description` | VARCHAR | Census's name (`Autauga County`, `Alabama`). |
| `description_full` | VARCHAR | `Alabama › Autauga County`. |
| `release` | VARCHAR | Both vintages read, `ANSI 2020, Gazetteer 2026`. |
| `text_source` | VARCHAR | `census_ansi` or `census_gazetteer`: which file the row came from. |
| `corpus` | VARCHAR | `description_full` lowered and accent-stripped. |
| `state_code` | VARCHAR | The two-digit state code, on every row. |
| `county_code` | VARCHAR | The three-digit county code; NULL on a state. |
| `state_abbr` | VARCHAR | USPS abbreviation (`AL`). |
| `gnis_code` | VARCHAR | The eight-digit GNIS feature code Census lists (`STATENS`, `COUNTYNS`, `ANSICODE`). |
| `class_code` | VARCHAR | Census's class code (`H1`, `H4`, `H5`, `H6`, `C7`); NULL on states and on counties only the Gazetteer lists. |
| `functional_status` | VARCHAR | Census's functional status (`A`, `B`, `C`, `F`, `G`, `N`, `S`); NULL on states and on counties only the Gazetteer lists. |
| `current` | BOOLEAN | Whether the code is in Census's current vintage: true when the Gazetteer lists it (a county, or a state with a county in it), false for a county in a state the Gazetteer covers but which it no longer lists (Connecticut's eight counties), NULL where the Gazetteer does not cover the state (the Island Areas and Minor Outlying Islands). |

Measured: 57 states and 3,244 counties (3,235 from ANSI, 9 from the Gazetteer), 3,301 rows.

## How `load` builds it

- Each file's header is checked against the columns above; a moved or renamed column fails the run.
- Each vintage is read from its file's name (`national_state2020.txt`, `national_county2020.txt`, `2026_Gaz_counties_national.txt`). The run fails if a name does not parse or the two ANSI vintages differ.
- Codes are checked for shape: two digits, three digits, a five-digit Gazetteer GEOID, an eight-digit GNIS code.
- Every ANSI county's state is in the ANSI state file, and every Gazetteer county's state is too.
- A county both files list must have the same name and GNIS code in both. A difference fails the run, naming the code, so a rename in a later vintage is decided by a person rather than published under the older name.
- A Gazetteer county that ANSI does not list is added, with `text_source = 'census_gazetteer'`, NULL class and status, and `current = true`.

## Tests

`datasets/fips/tests.yaml`, run by `arc test`, against cut-down copies of the three files committed under `datasets/fips/testdata/` (Alabama, Connecticut, DC, New Mexico for an accented name, Puerto Rico, American Samoa and the Minor Outlying Islands). Fixtures are `.csv` files holding Census's pipe-delimited text, so a `break:` can rewrite them; arc places each at the `.txt` path the fetch writes.

- A pass case whose inline checks hold the built table: row counts by level and source, Connecticut's counties `current = false` and planning regions `current = true`, Island Areas `current` NULL, `release`, an accented name kept, every parent a state.
- Refusal breaks, each matching `load`'s `error()` text: a renamed county header, a renamed Gazetteer header, a county whose state is not in the state file, a name that differs between the two files, a GNIS code that differs, a malformed county code.
- `check_code_list` (a `uv` step) refusing is not tested by message: arc can match only `step 'check_code_list' failed (exit code n)` for a `uv` step.

CI gains a job that builds `arc` at `ARC_REV` and runs `arc test` in `datasets/fips` with `--junit`, with finetype's 0.6.67 release binary and extension, `uv`, and DuckDB 2.0's command line. `ARC_REV` moves to arcform main `f57ff3c`, the merge that added `arc test`, with the loader's `Cargo.toml` and `Cargo.lock`.

## What the branch commits, and what waits for the publish

CI holds every committed descriptor, `datasets/*/datapackage.json` or per-table, to the object the lake serves, and fails on a 404; it has no allowance for an unpublished object. So the branch commits the Protocol without its descriptor: `arcform.yaml`, `models/`, `descriptor.overrides.json`, `nominations.finetype.json`, `schema.finetype.json`, `.env.example`, `README.md` with the generated step list, `tests.yaml` and `testdata/`. `arc test` runs `describe`, `check_code_list`, `validate` and `stamp_descriptor` in its copy, so the descriptor is built and checked on every CI run without being committed.

After merge, the first `arc run --publish` writes `datasets/fips/datapackage.json`, and one follow-up change commits it with the README's Datasets row, the registry entry; the `census` namespace is already held.

## Calls made on Hugh's behalf

He was away during the build; each is reversible before the first publish.

- **Two Census vintages, Gazetteer for Connecticut.** The ANSI 2020 lists are the base, since they are the code list Census publishes and carry the Island Areas and the class and status codes; the 2026 Gazetteer adds the planning regions and decides `current`. Connecticut's old counties stay, marked not current, because pre-2022 data uses them.
- **A name or GNIS mismatch between the two files fails the run** rather than taking either file's text.
- **`release` names both vintages** in one value, as the contract requires one release per file.
- **No area or coordinates** from the Gazetteer in this version.
