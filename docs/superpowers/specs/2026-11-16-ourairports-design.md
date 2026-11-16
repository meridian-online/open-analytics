# OurAirports' airports with ICAO codes — design

## Intent

Host OurAirports' airport list, keyed by the ICAO code, at `library/ourairports` (SQL name `meridian.library.ourairports`, file `https://openlake.meridian.online/library/ourairports.parquet`). People search for ICAO codes: "icao codes" gets 2,400 searches a month in the US and 720 in the UK. An analyst holding flight, airspace or operations data keyed by ICAO code needs one table that turns the code into the airport's name, place, coordinates and type, and joins it to country and subdivision codes.

OurAirports releases its data to the public domain (<https://ourairports.com/data/>). It is one person's project, not an issuing institution, so the list goes in `library` under the dataset's own name (bearing's ruling on namespaces).

## Goals

- A Protocol `datasets/ourairports/` that fetches OurAirports' files, builds one row per airport that has an ICAO code, meets the shared code-list contract (`docs/code-lists.md`), and publishes into the open lake through its own `publish` step.
- Tests in `datasets/ourairports/tests.yaml`, run by `arc test` against cut-down fixtures, with no Python self-test, as FIPS's are. The Protocol joins CI's `protocol-tests` matrix and the one-writer list.
- The first publish runs after the pull request merges. The descriptor, registry entry and README row come with that publish, in a second pull request, as FIPS's did.
- `docs/code-lists.md` gains OurAirports in its layout, release and rulings sections.

## Non-goals

- The dataset page on meridian.online and the ICAO code type page's code column (web work, after the publish).
- Airports with no ICAO code. They have no code other data carries in ICAO form; `ident` falls back to local and generated codes. A full airport list is a separate dataset if it is wanted.
- OurAirports' runways, frequencies and navaids files.
- Scheduling the fortnightly refresh: the recurring refresh task (bearing#99dx) picks the list up once it is published.

## Anti-goals

- **Any IATA code in the published table.** IATA's codes are proprietary. The `iata_code` column is not read into the table, and the free-text `keywords` column is left out because it carries IATA codes (measured below).
- Reading ICAO codes from `ident`. OurAirports' data dictionary defines `ident` as "the ICAO code if available", otherwise a local or internally generated code; `icao_code` is "the four-letter ICAO code for the airport (if it has one)". On 2026-11-16, 845 of the 10,734 airports with an `icao_code` have an `ident` that differs from it.

## What the source holds (measured 2026-11-16)

OurAirports' data page links `https://davidmegginson.github.io/ourairports-data/airports.csv`, the GitHub Pages site of the repository `davidmegginson/ourairports-data`, which OurAirports updates nightly (the latest commit, "data update", at 2026-11-16T02:53:14Z).

- `airports.csv`: 86,368 rows, quoted CSV, columns `id, ident, type, name, latitude_deg, longitude_deg, elevation_ft, continent, iso_country, iso_region, municipality, scheduled_service, icao_code, iata_code, gps_code, local_code, home_link, wikipedia_link, keywords`.
- 10,734 rows have an `icao_code`, all distinct and all four capital letters. By type: 5,549 small airports, 3,579 medium, 1,173 large, 415 heliports, 18 seaplane bases; no closed airport and no balloonport has one.
- `countries.csv` (`id, code, name, continent, wikipedia_link, keywords`) and `regions.csv` (`id, code, local_code, name, continent, iso_country, wikipedia_link, keywords`): every `iso_country` and `iso_region` of those rows is a code in them. 129 rows carry OurAirports' placeholder region `<country>-U-A` ("unassigned"), which is in `regions.csv` but is not an ISO 3166-2 code.
- `keywords`: 3,434 of the rows carry it, and on 58 it contains the row's own IATA code as a word. Other IATA codes (city codes, former codes) can appear in it and cannot be told apart from other words.
- `scheduled_service` is `yes` or `no` on every row; `elevation_ft` is an integer where present (10,399 rows).

## Approaches

1. **One row per airport with an ICAO code, flat, `code` = `icao_code` (chosen).** Meets the contract as HCPCS does, with one level, `airport`. The code is the form other data carries, so it joins without a rewrite.
2. **All 86,368 airports, keyed by `ident`.** Covers every airport, but `code` would be OurAirports' internal identifier, which other data does not carry, and the dataset would no longer be the ICAO list the demand is for.
3. **A two-level ladder, country then airport.** Gives `parent_code` meaning, but country rows are not ICAO codes, and a table mixing two code systems in `code` reads worse than a flat list with `iso_country` beside each airport. Country codes get their own list from Unicode CLDR (planned under `unicode`).

## The table

The ten core columns, then OurAirports' fields:

| Column | From | Notes |
|---|---|---|
| `code` | `icao_code` | Four capital letters, primary key. |
| `code_display` | `icao_code` | The same. |
| `level` | `'airport'` | One level. |
| `depth` | `0` | |
| `parent_code` | NULL | |
| `description` | `name` | OurAirports' name, with its accents. |
| `description_full` | `name` | Flat list, so the same as `description`, as HCPCS's is. |
| `release` | the repository's commit date | See *The release*. |
| `text_source` | `'ourairports'` | |
| `corpus` | `lower(strip_accents(description_full))` | |
| `ourairports_id` | `id` | BIGINT, OurAirports' stable record id, for joining back to its other files. |
| `ident` | `ident` | OurAirports' own identifier. |
| `type` | `type` | `small_airport`, `medium_airport`, `large_airport`, `heliport`, `seaplane_base` (closed set, checked). |
| `latitude_deg`, `longitude_deg` | | DOUBLE, checked within ±90 and ±180. |
| `elevation_ft` | | INTEGER, NULL where OurAirports has none. |
| `continent` | | OurAirports' two-letter continent code (`AF`, `AN`, `AS`, `EU`, `NA`, `OC`, `SA`). |
| `iso_country` | | Two letters, as OurAirports gives it (ISO 3166-1 alpha-2, plus `XK` for Kosovo where present). |
| `country_name` | `countries.csv` `name` | Joined on `iso_country`. |
| `iso_region` | | As OurAirports gives it: ISO 3166-2 where assigned, `<country>-U-A` where OurAirports has not assigned one. |
| `region_name` | `regions.csv` `name` | Joined on `iso_region`. |
| `municipality` | | Served place, NULL where absent. |
| `scheduled_service` | | BOOLEAN from `yes`/`no`. |
| `gps_code`, `local_code` | | OurAirports' GPS and local codes (in the US the FAA identifier). |
| `home_link`, `wikipedia_link` | | URLs, NULL where absent. |

Left out: `iata_code` and `keywords`, as above.

**How far the IATA rule reaches (a call made on Hugh's behalf).** The rule is that IATA's list is not republished: the `iata_code` column is never read into the table, and `keywords` is dropped because it is free text in which IATA codes appear as IATA codes and cannot be told apart from other words. It does not blank another issuer's identifier whose letters happen to equal an IATA code. Measured on 2026-11-16, of the 7,823 ICAO airports with an IATA code, `local_code` equals it on 1,712 (1,525 in the US, where `local_code` is the FAA's location identifier, which IATA adopts for US airports; 87 in Colombia; the rest scattered), `ident` on 29 (where `ident` fell back to the local code), `gps_code` on none, and the name or a URL contains it on 12. Those values are the FAA's and other national authorities' public identifiers, recorded by OurAirports as such, not a copy of IATA's code list. Blanking them would remove the FAA identifier from most US airports. The descriptor's `local_code` description says what the column is and that it is not an IATA code. If Hugh rules the other way, `load` reads `iata_code` only to blank equal values, and a test shows it.

## The release

OurAirports' files carry no date. The Protocol brackets the data fetches with two fetches of the repository's latest commit on `main` from GitHub's API (`https://api.github.com/repos/davidmegginson/ourairports-data/commits?sha=main&per_page=1`), one before and one after, as HTS brackets USITC's export with `currentRelease`. `load` refuses the run if the two commits differ, and `release` is the commit's UTC date (`2026-11-16`). The data files are fetched from `raw.githubusercontent.com/davidmegginson/ourairports-data/main/`, which serves the branch the API names, rather than from the Pages site, which redeploys after each commit.

## What `load` refuses

Each with an `error()` naming what it found: a changed header in any of the three CSVs; a commit bracket whose two commits differ, or a commit date that does not parse; an `icao_code` that is not four capital letters; a repeated `icao_code`; an `iso_country` not in `countries.csv`, or an `iso_region` not in `regions.csv`, or a region whose `iso_country` differs from the airport's; a `type` outside the closed set; a `scheduled_service` other than `yes` or `no`; a latitude or longitude that is not a number in range; an `elevation_ft` that is not an integer; an empty name.

## Descriptor

`licenses`: `other-pd`, titled "Public domain (OurAirports)", path `https://ourairports.com/data/`. `other-pd` asks for no notice (`docs/licence-notices.md`); the description credits OurAirports, as the issue asks, says its data comes with no guarantee of accuracy, that OurAirports updates nightly and Meridian refreshes fortnightly so `release` is the as-of date, and that IATA codes are left out. `sources`: OurAirports' data page, its data dictionary, and the three files fetched. `contributors`: OurAirports as author, Meridian as publisher and wrangler. `code` and `code_display` are nominated as finetype's `geography.transportation.icao_code`; the text columns as `representation.text.plain_text`, as FIPS's are.

## Publish

The `publish` step writes `library.ourairports` with its readable copy at `library/ourairports.parquet`. `datapackage.json` stays in the Protocol's `.gitignore` until the first publish. Before the merge, `check_label_agreement.py --strict` runs with the built descriptor in place, the step FIPS skipped.

## Left to the builder

The fixtures' exact rows (cut from the 2026-11-16 files, each kept row named in `testdata/README.md`), the inline checks of the pass case, and the wording of field descriptions.
