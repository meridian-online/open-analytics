# Publishing into the open lake

How an open dataset reaches readers: a Protocol's last step writes it into the open lake, and nothing outside the Run takes part. The two Standard Occupational Classification Protocols, `datasets/soc_2018` (`bls/soc_2018`, `bls/soc_2018_titles`, `bls/soc_2010_to_2018`) and `datasets/soc_2020` (`ons/soc_2020`, `ons/soc_2020_titles`, `ons/soc_2010_to_2020`), are the first to publish this way, and the open lake was cut over to it on 2026-11-14 for their first publish. This guide says how the step publishes, what a publisher needs, how the cutover ran and why in that order, the rulings behind it, and what is left. Read it before moving another Protocol onto the step.

## The open lake

The open lake is one DuckLake catalogue on R2, in the bucket `meridian-open`, served read-only at <https://openlake.meridian.online/>. A reader attaches it without a credential:

```sql
INSTALL ducklake; INSTALL httpfs;
ATTACH 'ducklake:https://openlake.meridian.online/catalog/open.ducklake' AS meridian (READ_ONLY);
SELECT * FROM meridian.bls.soc_2018 LIMIT 10;
```

It holds three things per table: the table itself, under the catalogue's data path `https://openlake.meridian.online/lake/`; the table's descriptor in the catalogue's `meta` schema (`meta.descriptors`, `meta.fields`), with the package description as the table's comment; and a readable copy, a single Parquet file at `https://openlake.meridian.online/<namespace>/<name>.parquet`, for a reader who wants one file and no catalogue. Each Run's record is kept under `runs/`, and `manifest.json` at the root lists every dataset with its readable copy and the Run that wrote it.

**Readers need DuckDB 2.0 or later to attach the catalogue.** DuckDB 2.0's DuckLake extension writes the catalogue in a storage format 1.x cannot open, and arc's publish step writes with DuckDB 2.0. A readable copy is plain Parquet, and `read_parquet` reads it on any DuckDB.

## How a Protocol publishes

The last step of the Protocol is `publish`, of `op: ducklake_write@1`. In `datasets/soc_2018/arcform.yaml` it names the lake (`public_base: https://openlake.meridian.online/`, `catalog: catalog/open.ducklake`, `data_path: lake/`, `runs: runs/`, `manifest: manifest.json`), a local fallback (`local_catalog: build/lake/open.ducklake`), the tables, and the credential:

- `tables`: one entry per table, naming the SQL name it is published as (`bls.soc_2018`), the stamped Parquet the stamp step wrote (`build/soc_2018.parquet`), its descriptor (`soc_2018.datapackage.json`) and its readable copy (`bls/soc_2018.parquet`).
- `credential`: `type: r2`, `bucket: meridian-open`, and the names of the three environment variables that carry it, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID`.

The step reads only what the stamp steps wrote, so it runs after them. It writes every table the Protocol names as one snapshot, upserting each on the `primaryKey` its descriptor declares (`code` for a code list, `entry_id` for a title index, `from_code, to_code` for a crosswalk), and commits nothing it has not finished: a publish that fails leaves the catalogue as it was. A Run over unchanged data commits nothing and says so. arc sets the readable copies and `manifest.json` entries in the same step, and writes `manifest.json` with `cache-control: public, max-age=60`, so a reader of the manifest may see the previous version for up to a minute.

The Protocol declares `dotenv: [.env]`, so arc reads the three variables from a `.env` beside `arcform.yaml` as well as from the environment. An `.env.example` beside each Protocol names them with no values, and the root `.gitignore` ignores `.env` and its variants.

### The local fallback

With any of the three variables unset, `arc run` still finishes green. The step says it published locally, names the variables that are missing, and writes the lake into `build/lake/open.ducklake` instead, which attaches and reads back offline. This is how a Protocol is built and checked without a credential, on a laptop or in review.

### Publishing live

**With all three variables set, any `arc run` publishes into the live lake.** `--publish` does not switch publishing on; it only makes a missing credential fail the run instead of falling back to the local lake. So a filled `.env` is not kept in the Protocol folder, where every later run would pick it up. Keep the credential in a file outside the repository and export it for the publishing command alone, in a subshell:

```sh
(set -a; . /path/outside/the/repo/openlake.env; set +a; arc run --publish)
```

Two things a publisher must do first:

- **A full run needs finetype's DuckDB extension built for DuckDB 2.0.** The `describe` and `validate` steps load it from `FINETYPE_DUCKDB_EXT`, which must point at the extension built for the DuckDB the Protocol runs on; an extension built for 1.x does not load.
- **After moving a Protocol to a new arc or DuckDB, force a rebuild before publishing.** arc skips a step it judges fresh, and a change of engine alone does not make a step stale, so the Parquet built by the old engine would be published as it stands. Run `arc run --force` with the credential unset, which rebuilds every step and publishes only locally, then publish. This happened with SOC: its builds had been written by DuckDB 1.5.6, a forced rebuild rewrote them on 2.0.0, and the descriptors were restamped from the new Parquet before the live publish.

`scripts/check_engine_version.py` holds each published footer's `created_by` to the DuckDB its own Protocol pins in its stamp steps (`duckdb==2.0.0` for both SOC Protocols), reading every `datasets/*/datapackage.json` and every per-table `datasets/*/<table>.datapackage.json`. A copy written by the wrong engine reddens CI and names both versions.

### What `manifest.json` still says

`manifest.json` stays, in the shape the website reads today, until the website reads the catalogue. arc's manifest writer sets only `catalog.url`. The top-level `updated`, `catalog.sha256_16` and `catalog.published` were written by infra's publish pipeline and stopped moving at the cutover; nothing on the website reads them, and infra's uptime monitor now checks freshness from the catalogue's newest snapshot and the pointer from `catalog.url` alone. A reader who pins the catalogue by `catalog.sha256_16` sees a stale hash. The fields retire with the manifest.

## The cutover of 2026-11-14

Before the cutover, infra's publish pipeline wrote the open lake from outside each Run: it uploaded each dataset's files, rebuilt the catalogue, flipped `manifest.json` to point at the new catalogue, and collected old versions. The live catalogue's data path named a local folder, where arc's step writes to the https path the files are served from, and DuckLake cannot change the data path of a catalogue it has written. The cutover handed the lake to arc's publish step, in this order:

1. **infra stopped rebuilding the catalogue, flipping the manifest, collecting old versions, rolling back and uploading on its own.** Each of those rebuilt or overwrote what arc now owns, and the version collector would have deleted data files the new catalogue registers. infra's pipeline refuses to run with a message naming the cutover; its scripts stay until every Protocol has moved. The uptime monitor moved to DuckDB 2.0 in the same change, since it has to read the catalogue after the rebase.
2. **Readers were told they need DuckDB 2.0 or later.** The website's `ATTACH` snippets say so. The rebase is itself a DuckDB 2.0 write, so a 1.x reader of the catalogue would be cut off from that moment, not from the first SOC publish.
3. **The live catalogue was rebased onto the https data path.** `arc lake rebase` wrote a copy of the live catalogue whose data path is `https://openlake.meridian.online/lake/`, carrying the eight datasets already published and their eight compatibility views, and the copy was checked locally: every table and view read back with the row counts the manifest recorded. It was uploaded over `catalog/open.ducklake` with a conditional write, `If-Match` on the ETag the S3 API gave for the live catalogue before the rebase read it (the S3 API's, since a CDN may alter the public response's), so a catalogue that changed in between would not have been overwritten. The uploaded object's sha256 matched the local copy, and the eight read back through it over https.
4. **The two SOC Protocols published.** `soc_2018` committed snapshot 2 and `soc_2020` snapshot 3, each with its three readable copies and its entries in `manifest.json` (eighteen entries in all). All six read back through the catalogue over https with the row counts the Runs reported, alongside the eight and their views, with the descriptors in `meta.descriptors` and `meta.fields` and the ONS attribution line in the UK tables' descriptors and comments.

The same sequence, the rebase of the real catalogue and both SOC publishes into it, ran first under a test prefix, `lake-test/`, and was checked the same way, including a re-run that reported nothing changed and left the catalogue's ETag as it was, and infra's monitor run against it. Everything under the prefix was then deleted, and nothing had been written outside it.

## Hugh's rulings

- **This session carried the cutover**, with the SOC publish, rather than SOC publishing into an interim catalogue to be merged later: the open lake is one catalogue, and a reader should attach once.
- **The cutover was rehearsed under a test prefix before the live lake was touched**, because `arc lake rebase` had never run on the real catalogue, and a fault there would first have appeared in the catalogue every dataset is read through.
- **The agent ran the writes to `meridian-open`**, with the credential kept outside the repository and exported only for each publishing command: the rehearsal straight through, and each production write (the conditional catalogue upload, each SOC publish) only after Hugh's go-ahead for that write.
- **The four hosted code lists move one at a time**, in the order NDC, HCPCS, HTS, Schedule B, NDC first because it changes fastest, each list reviewed and published on its own. **They merge as they go.** Publishing from a branch reddens every branch that lacks that restamp, so NDC and HCPCS merge together, in HCPCS's pull request, and HTS and Schedule B each branch from `main` after the previous merge, so every pull request is green when it merges.
- **Each live write waits for Hugh's go-ahead for that write**, as SOC's did.

## NDC, the first existing dataset to move

`datasets/ndc` (`fda/ndc`) moved onto the step on 2026-11-15, and that publish was its fortnightly refresh. It was the first Protocol to publish into a table already in the lake: `fda.ndc` had been carried into the rebased catalogue in snapshot 1, so the step upserted into it rather than creating it. Snapshot 4 inserted 1,647 rows, updated 338,546 and deleted 1,339, leaving 340,193. Nearly every row updated because each carries `release`, openFDA's last-updated date, which moved; the deletions are listings openFDA no longer carries. The readable copy moved from its content-addressed key to the fixed `fda/ndc.parquet`, and its `manifest.json` entry with it. A re-run reported nothing changed.

Three things came up that the next move will meet:

- **A table already in the lake must keep its shape.** The step refuses a table whose columns were dropped, retyped or reordered (`on_schema_change: fail`, the default), and the rebuilt Parquet is only compared with the live table at publish time. `scripts/check_lake_shape.py` applies the same rule beforehand, and also stops on a row count more than 10% from the live table's: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table fda.ndc --parquet datasets/ndc/build/ndc.parquet` prints `shape ok: fda.ndc 32 columns, rows 339885 -> 340193`. The tables the rebase carried have no rows in `meta.fields`, so arc has no stored key to compare and the descriptor's `primaryKey` becomes the key.
- **finetype's inference moved under DuckDB 2.0.** `describe` types each column from a sample, and on DuckDB 2.0 the sample read NDC's `depth` (0, 1, 2) as a word and `marketing_category` (ANDA, NDA, BLA) as month names, though the committed descriptor had them as an integer and text. It did so on finetype 0.6.64, 0.6.66 and 0.6.67 alike, so the version was not the cause. Both columns are now nominated in `nominations.finetype.json`, which keeps the labels they had. The descriptor records the version of the `finetype` binary on PATH as `x-finetype-version`, so `FINETYPE_DUCKDB_EXT` should point at an extension of the same version: DuckDB 2.0's community repository serves 0.6.66, and 0.6.67's extension is built from finetype's `v0.6.67` tag with `make build-extension`, which needs its `extension-ci-tools` submodule.
- **The CDN served the old readable copy after the publish.** arc uploads a readable copy with no `cache-control`, the zone caches everything for as long as the origin allows, and an edge copy of `fda/ndc.parquet` cached before the publish went on serving the old bytes, for up to Cloudflare's default edge TTL. The catalogue was not affected, since its data files are new keys. Until arc sets a revalidating `cache-control` on readable copies, check a readable copy's hash from the origin (a request with a query string bypasses the edge copy) and expect the plain URL to lag.

## HCPCS

`datasets/hcpcs` (`cms/hcpcs`) moved on 2026-11-15, after NDC. CMS's file was still October's quarter (`2026-10/2026-09-23`), so snapshot 5 changed no rows: it added the table's descriptor to `meta`, and the readable copy moved to the fixed `cms/hcpcs.parquet`, rebuilt by DuckDB 2.0. The rebuilt descriptor kept every label, so nothing was nominated. A re-run reported nothing changed.

**Publishing from a branch reddens the branches below it.** CI holds every descriptor on a branch to the object the lake serves now. Once HCPCS published from its branch, the HCPCS descriptor on NDC's branch, still the one from 2 November, no longer matched the live object, so NDC's pull request cannot pass that check again on its own. Only a branch that carries every published restamp is green.

## HTS

`datasets/hts` (`usitc/hts`) moved on 2026-11-15, after NDC and HCPCS had merged. The live table held USITC's revision `2026HTSRev22`; the rebuild fetched `2026HTSRev23`. Snapshot 6 updated all 42,036 rows, since every row carries `release`, and seven of them changed text: chapter 99 provisions 9903.88.69, 9903.88.70 and 9903.91.12 to 9903.91.16, three of which moved an effective date from 10 November 2026 to 10 January 2027. The row count did not move, so the shape check's `42036 -> 42036` did not show the new revision; compare `release` with the live table's to see one. No label moved and nothing was nominated. A re-run reported nothing changed.

HTS's `stamp_descriptor` rewrites `datapackage.json` in place and does not pass `--carry-created`, so every run, even one that publishes nothing, rewrites the descriptor's `created`. Commit the descriptor from the run that published, and expect `created` alone to move on later runs, until HTS takes the one-writer layout the other lists have.

## What is left

- **The other five open Protocols move one at a time**: schedule_b next, then naics, edgar, gleif and edgar_gleif. Each keeps serving what it has, through the rebased catalogue and its readable copy, but none can be republished until it moves, since infra's publish is stopped. Each moves its own DuckDB pin to 2.0 when it moves, and the engine check holds its footers to that pin.
- **infra's publish scripts and workflow are deleted** once all of them have moved. Until then they refuse to run.
- **The website reads the catalogue** instead of `manifest.json`, at which point the manifest and its stale fields retire. The SOC dataset pages come with that work.

### Moving a Protocol onto the step

What SOC needed, and what the next Protocol will:

1. Add `dotenv: [.env]`, the `publish` step after the stamp steps, and an `.env.example` beside the manifest. Replace any note in the manifest that says publishing happens outside it.
2. Move the Protocol's DuckDB pins (`engine_version` and the `uv@1` steps' `duckdb==`) to the DuckDB arc links, 2.0.0 today.
3. Run `arc run --force` with no credential set, with `FINETYPE_DUCKDB_EXT` pointing at the 2.0 extension, and check the local lake in `build/lake/open.ducklake`. Commit the restamped descriptors.
4. If the table is already in the lake, run `scripts/check_lake_shape.py` against the live catalogue and stop on anything but `shape ok`.
5. Export the credential for one command and run `arc run --publish`. Then check the tables through the live catalogue, and their readable copies.
6. Regenerate the README's step list (`scripts/check_protocol_readme.py --write`) and describe the publish step in the README.
