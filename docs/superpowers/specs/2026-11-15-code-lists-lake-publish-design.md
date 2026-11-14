# The four hosted code lists publish into the open lake by arc run alone

## Intent

NDC (`fda/ndc`), HCPCS (`cms/hcpcs`), HTS (`usitc/hts`) and Schedule B (`census/schedule_b`) move onto arc's `ducklake_write@1` publish step, as the two SOC Protocols did on 2026-11-14. Since that cutover, infra's publish pipeline refuses to run, so none of these four can be republished until its Protocol publishes itself. Their fortnightly refresh falls due on 2026-11-15, and the move is that refresh: the forced rebuild fetches each source as it stands today and the live publish writes it.

## Goals

- Each of the four Protocols ends in a `publish` step and publishes its one table live into the open lake, with its descriptor in `meta.descriptors` and `meta.fields`, its readable copy at `https://openlake.meridian.online/<namespace>/<name>.parquet`, and its `manifest.json` entry.
- Each moves in its own pull request, in the order NDC, HCPCS, HTS, Schedule B, and each is live and its pull request ready before the next begins.
- Each live write runs only after Hugh's go-ahead for that write.

## Non-goals

- naics, edgar, gleif and edgar_gleif. They move later; edgar_gleif's form is still open.
- Deleting infra's publish scripts, the website reading the catalogue, retiring `manifest.json`.
- Scheduling the refresh. It stays a hand-run publish.
- Changing what any dataset contains, beyond what its source changed since the last publish.

## Anti-goals

- A live publish that rewrites a table under a different schema. The step's default, `on_schema_change: fail`, stays; a refusal comes back to Hugh rather than being switched to `replace`.
- A publish of Parquet written by DuckDB 1.5.5. Each is force-rebuilt on 2.0.0 first, as SOC was.
- A filled `.env` inside a Protocol folder.

## What is already true

- All four tables exist in the live catalogue, carried by yesterday's `arc lake rebase` (snapshot 1): `fda.ndc`, `cms.hcpcs`, `usitc.hts`, `census.schedule_b`. The first publish of each upserts into the existing table, not a new one.
- Each Protocol's descriptor names the same columns in the same order as its live table (checked 2026-11-15). Types are not yet compared: the local lake starts empty, so the local publish cannot show a type drift. Before each live write, the rebuilt Parquet's column types are compared with the live table's over https.
- The upsert deletes rows absent from the new Parquet, so codes the source dropped (delisted NDC packages, for instance) leave the table, and the snapshot records them as deletions.
- The readable copy moves from the content-addressed `<namespace>/<name>/<sha16>.parquet` to the fixed `<namespace>/<name>.parquet`, and the manifest entry follows. The old object stays, so a link to it keeps working.
- CI already installs DuckDB 2.0.0 and arcform 4047310; the engine check holds each dataset's footers to its own Protocol's pin, so each dataset's check moves to 2.0.0 in the pull request that moves its pins.

## Approaches

1. **Four pull requests, each branched from the one before (chosen).** Each publish waits only on its own review, not on Hugh merging the previous pull request, and the four merge in order without conflicts in the files they share (`docs/publishing.md`, `docs/code-lists.md`, the README).
2. Four pull requests each from `main`. Each would wait for the previous merge, or conflict in the shared docs.
3. One pull request for all four. Puts four live writes behind one review and reverses the issue's one-at-a-time order.

Hugh approved approach 1 on 2026-11-15.

## The design

For each Protocol, in order, on its own branch (`ndc-lake-publish`, then `hcpcs-lake-publish` from it, `hts-lake-publish`, `schedule-b-lake-publish`):

1. In `arcform.yaml`: `engine_version: ">=2.0"`, `dotenv: [.env]`, every `duckdb==1.5.5` pin to `duckdb==2.0.0`, and a `publish` step (`ducklake_write@1`) after `stamp_descriptor`, configured as SOC's is, with one table entry: the table's SQL name, `from: build/<name>.parquet`, `descriptor: datapackage.json`, `readable_copy: <namespace>/<name>.parquet`. Replace the header's BOUNDARY note and the trailing publish comment, which say publishing happens outside the Protocol. A `uv@1` step's `deps` are not part of its script's sha256, so the digests stay.
2. Add `.env.example` beside the manifest, a copy of SOC's.
3. Rebuild with `arc run --force`, credential unset and `FINETYPE_DUCKDB_EXT` pointing at the 2.0 extension, which publishes into `build/lake/open.ducklake`. Read the local lake back: row count, descriptor in `meta.descriptors`. Commit the restamped `datapackage.json`, whose rows move with the refresh.
4. Run the repo's checks the change touches: the engine check, the README check (`scripts/check_protocol_readme.py --write` regenerates the step list), the descriptor tests.
5. Compare the rebuilt Parquet's columns with the live table's, over https, by the rule arc's publish applies (`schema_difference` in arcform's `src/lake/commit.rs`): the same names with the same DuckDB type strings, in the same order, with new columns allowed only at the end. A difference arc would refuse stops the move before the live write and goes to Hugh with the difference. The rebased tables have no rows in `meta.fields`, so arc has no stored key to compare and the descriptor's `primaryKey` becomes the key.
6. Ask Hugh for the go-ahead, then publish live: `(set -a; . <credential file outside the repo>; set +a; arc run --publish)`, with an arc built from arcform 4047310, the revision CI pins.
7. Read back over https on DuckDB 2.0 with no credential: the table's row count equals the run's, `meta.descriptors` holds its descriptor, the readable copy and its manifest entry resolve, and the other tables and views still read.
8. Update the dataset's README (the publish step, the readable copy's address), `docs/code-lists.md` where it says the dataset publishes through infra, and `docs/publishing.md` (move the dataset from "What is left" to the published list, with the date and snapshot).
9. Push, open the pull request, run roborev's branch review, wait for the checks, link it to the Kata issue in Forge, and report it ready.

## Calls made on Hugh's behalf

- **Schedule B's `derive_join_coverage` pin moves to 2.0.0 with the rest.** It runs `datasets/edgar_gleif/scripts/crosswalk_join.py`, which reads naics's published object to measure Schedule B's one join. Its comment says to keep it in step with CI's `DUCKDB_VERSION`, which is already 2.0.0. The figures it measures are row counts, which do not depend on the engine, and naics's object is not republished here, so Schedule B's `measuredAgainst` stays valid. Nothing joins to NDC, HCPCS or HTS, so republishing them does not put any other dataset's measured join out of date.
- **HTS keeps its layout.** Its `describe` writes `datapackage.json` and `stamp_descriptor` rewrites `build/hts.parquet` and `datapackage.json` in place, unlike the stage-then-stamp layout of the other three. The publish step only reads the stamped files, so it works with either layout. Moving HTS to one writer per file is a separate restructure, out of scope here.
- **One go-ahead per live write.** Hugh is around during the build (brief, 2026-11-15), so each dataset waits for its own go-ahead and nothing is batched.
- **The readable copies' fixed addresses already resolve.** Each descriptor's `path` already names `<namespace>/<name>.parquet`, and those objects are served today, so the publish overwrites them in place.
- Schedule B shares HTS's `models/outline.sql` by relative path. HTS moves first, and the shared file does not change when Schedule B moves.

## Left to the builder

- Wording of README and doc changes.
- What to do if a source fails to fetch today: retry, and if it stays down, move that dataset's branch to ready without its live publish and say so.
- What to do if a local publish shows a schema refusal or a row count far from the last publish (more than 10%): stop before the live write and bring it to Hugh with the difference.
