# NAICS publishes into the open lake by arc run alone

## Intent

`datasets/naics` (`census/naics_2022`, 2,125 rows) is the next open dataset to move onto arc's `ducklake_write@1` publish step, after SOC and the five code lists (`docs/publishing.md`). Until it moves it cannot be republished at all, since infra's publish pipeline is stopped. Three pieces of work wait on every open Protocol moving: deleting infra's publish scripts, CI's DuckDB engine pin, and the website reading the catalogue.

## What the brief settled

Hugh, 2026-11-16: a project; NAICS moves first, with its columns unchanged; the build goes through to a ready pull request; the live publish waits until after the merge; Hugh is away during the build. Bringing NAICS onto the code-list contract (renamed and added columns) stays a separate change for a session when Hugh is around, because renaming published columns changes what readers query. A second open-analytics session runs alongside; the two never publish to the open lake at the same time. This session makes no live write.

## Goals

- `datasets/naics/arcform.yaml` ends in a `publish` step, as HTS's does, publishing `census.naics_2022` from `build/naics.parquet` with `datapackage.json` and the readable copy at `census/naics_2022.parquet`.
- The Protocol's DuckDB pins move to 2.0.0 and its descriptor is the one the live publish writes.
- Every pull request this work opens is green when it is called ready, except where this design says otherwise.

## Non-goals

- The code-list contract for NAICS.
- The one-writer layout: NAICS keeps `stamp_descriptor` rewriting `build/naics.parquet` and `datapackage.json` in place, as HTS did.
- Rebuilding Schedule B. Its NAICS join records the hash of the NAICS object it measured (`measuredAgainst`). Once NAICS republishes, CI's join check reports Schedule B's join as due a rebuild and does not fail, so Schedule B can be refreshed whenever it next runs.
- The legacy `naics` entry in `manifest.json` (the `source.naics` alias's old copy). The publish step writes only the `census/naics_2022` entry, as it did for the others.

## Constraint that shapes the work

CI checks the committed descriptor and each Protocol's DuckDB pin against the object the lake serves, on every pull request:

- `check_descriptors.py` holds the committed `bytes` and `hash` to the served readable copy, and the embedded footer descriptor to the committed one.
- `check_engine_version.py` holds the served footer's `created_by` to the `duckdb==` the Protocol's stamp step pins. The served NAICS copy was written by DuckDB 1.5.5; the stamp step keeps the writer's `created_by`, so a rebuild by arc writes 2.0.0.

So a pull request that moves the pin to 2.0.0, or commits the rebuilt descriptor, is red until NAICS has republished, and the brief puts that publish after the merge.

## What a local rebuild showed

`arc run --force` with no credential, with arc on DuckDB 2.0.0 and finetype 0.6.67's extension, rebuilt NAICS with the same 2,125 rows. The descriptor moved only `bytes` (349,871 → 349,873), `hash` and `created`; no finetype label moved, so nothing needs nominating. `check_lake_shape.py` against the live catalogue printed `shape ok: census.naics_2022 5 columns, rows 2125 -> 2125`.

## Approaches

1. **One pull request, published from its branch before merge**, as NDC, HCPCS and HTS were. Simplest, but its served-object checks are red until the publish, and it puts the publish before the merge, against the brief.
2. **Two pull requests (chosen).** The first adds the publish step, `.env.example`, the README's Publish section and the docs, and leaves the pins and the descriptor as they are, so every check is green and it can merge while nothing is published. The second moves the pins to 2.0.0 and commits the rebuilt descriptor; it is opened as a draft now, its served-object checks are red until the publish, and after the first merges it is rebased on `main`, Hugh gives the go-ahead, NAICS publishes from its worktree, CI re-runs green, and it merges. This keeps the publish after a merge, as the brief asks, keeps the first pull request green, and keeps the descriptor committed from the run that published.
3. **One pull request with the old pins, published from `main` after merge, and a follow-up for the descriptor.** Rejected: the stamp would run on DuckDB 1.5.5 against a 2.0 Parquet, and the engine check would redden `main` until the pins moved anyway, so it adds a step and a red window without removing the second pull request.

The agent chose approach 2 because Hugh was away; it can be undone by folding the second pull request into the first.

## The design

First pull request, branch `naics-lake-publish`:

- `datasets/naics/arcform.yaml`: `dotenv: [.env]`; the BOUNDARY note replaced by HTS's PUBLISH note; a `publish` step after `stamp_descriptor` with `table: census.naics_2022`, `from: build/naics.parquet`, `descriptor: datapackage.json`, `readable_copy: census/naics_2022.parquet`, and HTS's lake and credential settings. `engine_version` and the `duckdb==` pin stay as they are, with a comment that they move with the descriptor in the next change and that NAICS must not be published from `main` until that change lands: a publish with the 1.5.5 stamp over a Parquet arc wrote with 2.0 would redden the engine check. The README's Publish section says the same. (Agent's call, after the builder check: a comment, not a guard step, since the window lasts from one merge to the next and nobody publishes without Hugh's go-ahead.)
- `datasets/naics/.env.example`, as HTS's.
- `datasets/naics/README.md`: the boundary section replaced by a Publish section with the shape check for `census.naics_2022`, a step 4 line for the publish, and the generated step list regenerated by `scripts/check_protocol_readme.py --write`.
- `docs/publishing.md`: a NAICS section saying what the move found and the two-pull-request order and why; "What is left" lists edgar, gleif and edgar_gleif.

Second pull request, branch `naics-lake-publish-descriptor`, from the first:

- `engine_version: ">=2.0"` and `duckdb==2.0.0` in `stamp_descriptor`.
- `datasets/naics/datapackage.json` from the local forced rebuild. The live publish runs plain `arc run --publish` with no `--force`, so every step but `publish` is skipped as fresh and the published bytes are the ones this descriptor describes, as FIPS's publish did. The committed descriptor is provisional: if `build/` does not survive until the go-ahead, or a step turns stale (the rebase, a re-fetched workbook), the publishing run rebuilds, and the publisher commits `datapackage.json` from that run to the same pull request before CI re-runs. `git diff datasets/naics/datapackage.json` after the publish shows whether it moved. (Agent's call, after the builder check.)
- Its description gives the publish steps for after the first merges: rebase, check no other session is publishing, the shape check, ``arc run --publish` with the credential exported for that command alone`, readback over https, re-run CI.

## Verification

- The quick checks and the tests the change touches, then the full descriptors workflow on each pull request.
- A local `arc run` with no credential publishes into `build/lake/open.ducklake`, and `census.naics_2022` reads back from it with 2,125 rows and its descriptor in `meta.descriptors`.

## Left to the builder

The wording of comments and docs; whether any test needs to know about the new step.
