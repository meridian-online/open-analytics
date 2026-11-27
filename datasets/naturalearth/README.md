# `naturalearth` — the world's countries at 1:110m, and their names

Natural Earth's countries at 1:110m scale (`admin_0_countries`, release 5.1.1), the small-scale generalisation meant for a world map, as two tables built in one Run:

| Table | Address | Job | Rows (2026-11-27) |
|---|---|---|---|
| `countries_110m` | `naturalearth/countries_110m` | one row per country or territory Natural Earth draws: its ISO codes, name, continent and outline as GeoJSON text | 177 |
| `country_names` | `naturalearth/country_names` | one row per spelling of a country's name or code, folded to a key, with the country it means | 3,875 |

A map draws `countries_110m` with no spatial extension and no network, since each outline is GeoJSON text (`geom`). Other data that names countries joins to it through `country_names`: fold the column with `strip_accents(lower(trim(x)))`, join on `name_key`, and join `iso_a3` to `countries_110m`. Both are registered in `../registry.json`. This directory holds their facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`) | **Protocol** — how they're made | run `arc run` → produces the two tables |
| `build/<table>.datapackage.json` | **Descriptor** — what each is (built by `arc run`, not committed: the open lake holds the published copies) | schema, finetype labels |
| `<table>.overrides.json`, `<table>.nominations.finetype.json`, `<table>.schema.finetype.json` | the descriptor's inputs | the curated half, the declared finetype labels, the drift contract |
| `aliases.csv` | an input to `country_names` | the spellings Meridian adds, each with its reason |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one version of each table. The step DAG:

1. **fetch** — Natural Earth's `ne_110m_admin_0_countries.zip`, a zipped shapefile.
2. **extract** — the shapefile's members and its `VERSION.txt`.
3. **load** (`models/load.sql`) — read the shapefile with DuckDB's spatial extension (`ST_Read`), check the release, and choose each country's key (see below).
4. **countries**, **names** (`models/`) — build each table: the countries with their outlines through `ST_AsGeoJSON`, and the name keys with `aliases.csv` added.
5. **export** — write each table's zstd Parquet to `build/stage/`, ordered by its primary key.
6. **describe** — emit each descriptor from its Parquet, typed by finetype with the table's nominations and overrides applied.
7. **validate** — gate each Parquet against its `<table>.schema.finetype.json`.
8. **stamp** — write each published `build/<table>.parquet` with its finished descriptor in the footer, and `build/<table>.datapackage.json` beside it.
9. **check before publishing** — `emit_descriptors` writes every published table's descriptor, as the open lake holds it, to `build/lake_descriptors.json`, and `check_before_publish` holds each built descriptor to the frictionless reference implementation and to `scripts/check_descriptors.py`'s rules read against the local Parquet, and runs label agreement across the bundle; label agreement does not yet compare a per-table descriptor such as these. A failing check stops the run before publish.
10. **publish** (`ducklake_write`) — write the two tables into the open lake as one snapshot, each upserted on its key, with each descriptor in the lake's `meta` schema and a readable Parquet copy per table at `naturalearth/<table>.parquet`.

Produces `build/countries_110m.parquet` and `build/country_names.parquet`, about 190 KB together.

## The key

`iso_a3` is the key of `countries_110m`, and it is never NULL. Natural Earth sets `ISO_A3` to `-99` where ISO's code is disputed or missing and gives the code a map should use in `ISO_A3_EH`: France and Norway carry `-99` in `ISO_A3` and `FRA` and `NOR` in `ISO_A3_EH`. So the key is `ISO_A3_EH`. Three countries have no ISO code at all, Northern Cyprus, Somaliland and Kosovo, and they take Natural Earth's own `ADM0_A3` (`CYN`, `SOL`, `KOS`), so all 177 have a key and none is dropped. `iso_a2` takes `ISO_A2_EH` the same way (`XK` for Kosovo) and is NULL for Northern Cyprus and Somaliland. `load` fails the run on a missing or repeated key, a malformed `iso_a2`, or an empty shape.

## The names

`country_names` gathers every spelling Natural Earth gives a country and folds each to a key with DuckDB's `strip_accents(lower(trim(x)))`. The spellings come from four ranked sources:

| Rank | Source |
|---|---|
| 0 | `aliases.csv`: common abbreviations Natural Earth's fields do not carry (`usa`, `us`, `uk`, `rsa`, `eire`), each with its reason |
| 1 | the country's own codes and English names: `iso_a3`, `iso_a2`, `NAME`, `NAME_LONG`, `FORMAL_EN`, `ADMIN`, `NAME_ALT` |
| 2 | its name in each of Natural Earth's 26 languages, `NAME_AR` to `NAME_ZHT` |
| 3 | `SOVEREIGNT`, the country it belongs to |

A key resolves to the country that carries it at the best rank it appears at. Without the ranks, a key any two countries share would be ambiguous, and several common ones are: New Caledonia and the French Southern Lands have `SOVEREIGNT` France, Greenland has Denmark, the Falklands the United Kingdom and Puerto Rico the United States, and `ao`, `bi` and `nga` are Angola's, Burundi's and Nigeria's codes as well as Vietnamese for Austria, Belgium and Russia. Ranked, each of those means the country a reader expects. A key two countries carry at the same best rank would mean either, so it is left out, and the `names` step prints each such key in the run log; release 5.1.1 has none. `names` fails the run on an alias whose `iso_a3` is not a country here, an alias without a reason, or an alias repeated.

## The release

Natural Earth serves its current release at a fixed URL, and the archive's `VERSION.txt` states it. `load` fails the run unless it says `5.1.1`, the release both descriptors name, so a new release is taken by a person, who updates the descriptors and that check together.

## Publish

The last step publishes the two tables into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential. With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset, and keep the credential in a file outside the Protocol folder, exported only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing. [`docs/publishing.md`](../../docs/publishing.md) says what a publisher needs first and the path from a branch to the lake.

## Run

Needs the `arc` binary, `duckdb` with its `spatial` extension (the `load` step installs it), `uv` (for the stamps and the checks), and `finetype` with its DuckDB extension (`FINETYPE_DUCKDB_EXT`): `arc run`. The archive is about 210 KB, and the full run takes under a minute.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 16 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_countries` | `op: http_fetch@1` |
| 2 | `extract_countries` | `op: archive_extract@1` |
| 3 | `load` | `sql: models/load.sql` |
| 4 | `countries` | `sql: models/countries.sql` |
| 5 | `names` | `sql: models/names.sql` |
| 6 | `export_countries_110m` | `op: parquet_export@1` |
| 7 | `export_country_names` | `op: parquet_export@1` |
| 8 | `describe_countries_110m` | `op: datapackage_describe@1` |
| 9 | `describe_country_names` | `op: datapackage_describe@1` |
| 10 | `validate_countries_110m` | `op: finetype_validate@1` |
| 11 | `validate_country_names` | `op: finetype_validate@1` |
| 12 | `emit_descriptors` | `op: uv@1` |
| 13 | `stamp_countries_110m` | `op: uv@1` |
| 14 | `stamp_country_names` | `op: uv@1` |
| 15 | `check_before_publish` | `op: uv@1` |
| 16 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
