# SOC publishes into the open lake by `arc run` alone, and the open lake cuts over to arc's publish step

Date: 2026-11-14. Status: draft for Hugh's approval.

## Intent

Every open dataset is to be published by its own Protocol's last step, arc's `ducklake_write@1`, instead of by infra's scripts outside the run. The two SOC Protocols go first: `datasets/soc_2018` (BLS: `bls/soc_2018`, `bls/soc_2018_titles`, `bls/soc_2010_to_2018`) and `datasets/soc_2020` (ONS: `ons/soc_2020`, `ons/soc_2020_titles`, `ons/soc_2010_to_2020`). They were built and merged on 2026-11-03 and held back from publishing so that they would publish by `arc run` alone, as the first hosted datasets with no harness around them. Their going live is also the proof that arc's publish step works end to end in production.

The first live publish is also the cutover of the open lake, which arcform's architecture notes ("`arc lake rebase` and the cutover") put in a fixed order: rebase the live catalogue onto an https data path, tell readers they need DuckDB 2.0, and stop infra's catalogue rebuild, manifest flip and version collector together. Hugh ruled on 2026-11-14 that this session carries the cutover, and that it is rehearsed under a test prefix before the live lake is touched.

## Goals

1. Both SOC Protocols end in one `ducklake_write@1` step each, publishing their three tables as one snapshot into the open lake at `https://openlake.meridian.online/catalog/open.ducklake`, with readable copies at `<namespace>/<name>.parquet` and entries in the compatible `manifest.json`.
2. Without the write credential, `arc run` on either Protocol publishes into a local lake under `build/` and finishes green.
3. The live catalogue is rebased onto `https://openlake.meridian.online/lake/` with the eight datasets already published still readable through it, by a DuckDB 2.0 `ATTACH`, at every moment of the cutover.
4. infra no longer rebuilds the catalogue, flips the manifest or collects old versions, and its uptime monitor reads the lake with DuckDB 2.0.
5. web's `ATTACH` snippets say DuckDB 2.0 or later.
6. The DuckDB pins in this repo (CI's `DUCKDB_VERSION`, the `uv@1` steps' `duckdb==` pins, `ARC_REV` and the libduckdb it links) move to the DuckDB 2.0 that arc's publish step writes with.
7. SOC's registry entries, the held namespaces `bls` and `ons`, and the README rows exist, so the six addresses are real.

## Non-goals

- The other eight open Protocols (naics, edgar, gleif, edgar_gleif, hts, schedule_b, hcpcs, ndc). Each moves in a later session, one at a time. Until then each keeps serving the files it has, and none of them can be republished, since infra's publish is stopped.
- Deleting infra's publish scripts and workflow, which happens once all ten have moved. This session only stops the three behaviours the cutover requires.
- web reading the catalogue instead of `manifest.json`, and the SOC dataset pages.
- Any change to arcform. The one Rust build is `arc` from arcform's `main` at `4047310` (PR 169), installed locally before the first run.

## Anti-goals

- A reader of any of the eight existing datasets loses access during the cutover, through either the stable catalogue or a readable copy.
- A secret reaches git, a log, a run record or a pull request. The repo is public.
- The stable catalogue is overwritten without a check that it is the one the rebase read.

## Constraints

- **DuckDB 2.0 everywhere the lake is written or read.** DuckDB 2.0's DuckLake extension writes the catalogue in a storage format 1.x cannot open (Hugh's ruling when the publish step was designed). The rebase itself is a 2.0 write, so the monitor's move to 2.0 must be merged before the live rebase, not after.
- **`arc lake rebase --upload` writes only if nothing is at the key.** It cannot replace the existing stable catalogue.
- **R2 API tokens scope to a whole bucket, not a prefix** (learned in arcform's live check). The rehearsal and the live run use the same token on `meridian-open`. Hugh creates it; the test token used for arcform's live check was deleted.
- **One Rust build this session**, before the first run, and before the brightfield session's build.
- **Merges are Hugh's**, in Forge, after branch review and green CI. Three pull requests: open-analytics (this branch), infra, web.
- **The repo is public.** No Kata refs, private repo names or local paths in tracked files, commits or pull request text.

## Approaches considered

| Approach | For | Against |
|---|---|---|
| **Rehearse under a test prefix, then cut over (chosen)** | The rebase of the real catalogue, with its eight datasets and views, and SOC's first publish into it, are proven together before production is touched | About half an hour and two extra runs; a test prefix to clean up |
| Straight to live | Quicker; arcform's live check already proved the step on R2 | `arc lake rebase` has never run on the real catalogue, so a fault would first appear in the catalogue every dataset is read through |
| SOC publishes into a separate interim catalogue, cutover later | No cutover this session | Contradicts one catalogue per owner; readers attach twice; a later merge of two catalogues |

Hugh chose the rehearsal on 2026-11-14 ("Agreed, let's do the rehearsal"), and that this session carries the cutover ("I'm happy for the cutover to run in this session").

## The design

### The publish step in each Protocol

Each SOC `arcform.yaml` gains `dotenv: [.env]` and a last step, `publish`, of `op: ducklake_write@1`:

- `public_base: https://openlake.meridian.online/`, `catalog: catalog/open.ducklake`, `data_path: lake/`, `runs: runs/`, `manifest: manifest.json`, `local_catalog: build/lake/open.ducklake`.
- `tables`: the three stamped Parquet files the stamp steps write (`build/<table>.parquet`) with their descriptors (`<table>.datapackage.json`), as `bls.soc_2018` and so on, each with `readable_copy: <namespace>/<name>.parquet`.
- `credential`: `type: r2`, `bucket: meridian-open`, `env` naming `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`.

The header's BOUNDARY note, which says publishing is not an arcform step, is replaced by a note on the publish step: what it writes, where, and that it publishes locally without the credential. Each Protocol's README describes the new step and how to publish (the three variables exported for that command only, then `arc run --publish`; with all three set, any `arc run` publishes live, so a filled `.env` is not kept in the Protocol folder). `.env` is added to the root `.gitignore`, and an `.env.example` naming the three variables (no values) sits beside each Protocol. `scripts/check_protocol_readme.py` and the manifest-load job must accept the new step; whatever they need changes with it.

The primary key each table is upserted on comes from its descriptor's `primaryKey`, as the step reads it: `code` for the code lists, `entry_id` for the title indexes, `from_code, to_code` for the crosswalks.

### DuckDB 2.0 pins

- Each SOC Protocol's `engine_version` becomes `">=2.0"`, and its `uv@1` steps' `duckdb==1.5.5` pins become the 2.0 release (`duckdb==2.0.0`, or whichever 2.0 patch arc at `4047310` links), with each script's `sha256` unchanged since the scripts do not change.
- CI's `DUCKDB_VERSION` moves to the same version. `ARC_REV` moves to `4047310…` so the manifest-load job knows `ducklake_write@1`, with `ARC_LIBDUCKDB_VERSION` and `ARC_RUST_TOOLCHAIN` matching what arcform's CI uses at that revision, and `scripts/arc_manifest_load`'s Cargo files and lock follow.
- The engine check (`check_engine_version.py`) then expects 2.0 footers. The eight existing datasets' published footers name v1.5.5. Either the check is limited to the datasets that publish through the lake step, or it accepts each dataset's own recorded writer until that dataset moves. The builder picks whichever keeps the check meaningful for SOC and does not redden on the eight, and states it in the workflow's comment. Other Protocols' pins are not moved: each moves with its Protocol.

### Registry, namespaces and README rows

`datasets/registry.json` gains held namespaces `bls` (U.S. Bureau of Labor Statistics) and `ons` (Office for National Statistics) and entries for the six tables, in the shape the existing entries use, with whatever `scripts/test_registry.py` requires. The root README's dataset table gains SOC's rows. The UK entries carry the OGL notice the descriptors already carry (settled by PR 73).

### The rehearsal

With Hugh's new token in a file outside the repo, exported only for each publishing command:

1. Build `arc` from arcform's `main` (`cargo install --path . --features mcp,bundled-duckdb`, or as arcform's release notes say), and check `arc --version` and that `ducklake_write@1` is in its catalogue.
2. `arc lake rebase --from https://openlake.meridian.online/catalog/open.ducklake --out <scratch>/rehearsal.ducklake --data-path https://openlake.meridian.online/lake-test/lake/ --upload https://openlake.meridian.online/lake-test/catalog/open.ducklake --bucket meridian-open --credential-env r2:…`. The eight existing tables are registered at their current https addresses, so they read through the test catalogue unchanged.
3. Make an untracked copy of each SOC Protocol beside the real one (same directory depth, so `../../scripts` still resolves) with the publish step's keys under `lake-test/`: `catalog: lake-test/catalog/open.ducklake`, `data_path: lake-test/lake/`, `runs: lake-test/runs/`, `manifest: lake-test/manifest.json`, readable copies under `lake-test/`. Run each with `arc run --publish`.
4. From a clean DuckDB 2.0 with no credentials, `ATTACH 'ducklake:https://openlake.meridian.online/lake-test/catalog/open.ducklake'` and check: all six SOC tables with the row counts the Protocols report; the eight existing tables and their views with the row counts the live manifest records; `meta.descriptors` and `meta.fields` for the six; the UK descriptors' OGL attribution in `meta.descriptors` and the table comment. Read one readable copy with `read_parquet` over https. Re-run one Protocol and check the step reports `unchanged` and writes nothing.
5. Run infra's monitor, with its DuckDB 2.0 change, against the test catalogue.
6. Delete everything under `lake-test/` and remove the untracked copies.

### The live cutover

In this order, with Hugh present:

1. The infra pull request is merged (Hugh): the Publish openlake workflow's publish job and `publish-all.sh` refuse to run with a message naming the cutover, `gc-versions.sh` refuses `--delete`, and the monitor runs DuckDB 2.0. Verify stays.
2. The web pull request is merged (Hugh): every `ATTACH` snippet says DuckDB 2.0 or later.
3. Rebase locally: `arc lake rebase --from <live catalogue> --out <scratch>/open.ducklake --data-path https://openlake.meridian.online/lake/`, recording the live catalogue's ETag before the download. Check it as in rehearsal step 4, locally.
4. Upload it to `catalog/open.ducklake` with a conditional write, `If-Match` on the recorded ETag (the S3 API, through `aws s3api put-object --if-match` or curl with SigV4), so a catalogue that changed since the rebase read it is not overwritten. Until the first SOC publish, `manifest.json`'s `catalog` still names the old pinned 1.x catalogue, which stays served and readable; the SOC publish repoints it at a pinned copy of the rebased one.
5. Run `arc run --publish` on `soc_2018`, then on `soc_2020`.
6. Check as in rehearsal step 4 against the live catalogue, and run the monitor. Record on the SOC hosting issue as each table goes live.

If the rebased catalogue fails its check, nothing is uploaded. If the upload is refused (the ETag moved), stop and find out what wrote the catalogue. If a SOC publish fails after the cutover, the eight are unaffected, since the step commits nothing it has not finished, and the run is fixed and re-run.

### Docs

`docs/` gains the publish path's account (how a Protocol publishes, the local fallback, the `.env`, `--publish`, the cutover and why it ran in this order), with Hugh's two rulings from this brainstorm, folded in before the branch is called ready.

### Who runs what, and when the pull requests merge

Hugh ruled on 2026-11-14 that the agent runs every write to `meridian-open` with his token, kept outside the repo and exported only for each publishing command: the rehearsal without stopping, and each production write (the conditional catalogue upload, each SOC publish) only after his go-ahead for that write. The infra and web pull requests are merged by Hugh before the live rebase. The live SOC publish runs from this branch, so this repo's pull request is made ready, and merged, after SOC is live; the docs fold writes the cutover up as done.

### The manifest's top-level fields

arc's manifest writer sets only `catalog.url`. The live manifest's `updated`, `catalog.sha256_16` and `catalog.published` were infra's and stop moving at the cutover. Nothing in web reads them; only infra's monitor does, for its freshness warning. The infra pull request moves the monitor's freshness check to the catalogue's newest snapshot time (read through its DuckDB 2.0 `ATTACH`), and its pointer check to `catalog.url` alone. The stale fields are left in the manifest, which retires when web reads the catalogue. (The builder's call, reversible.)

### The manifest-load job's Rust build

Moving `ARC_REV` rebuilds `scripts/arc_manifest_load` against DuckDB 2.0. That build is not run locally, keeping this session to one Rust build; CI checks it, so the commit that moves `ARC_REV` is pushed early and its CI result read before the work that depends on it. (The builder's call.)

## Left to the builder

- The exact DuckDB 2.0 patch version, taken from what arc at `4047310` links.
- How the engine-version check treats the eight unmoved datasets (above).
- The tool for the conditional upload in cutover step 4.
- What the manifest-load job and the README check need to accept the new step.
- The wording of the infra refusals and the web copy, within the rules of those repos.

## Calls made on Hugh's behalf

- The conditional upload with `If-Match` replaces `arc lake rebase --upload` for the live key, avoiding a gap and a second Rust build.
- Registry entries, namespaces and README rows are in this branch, since they are in this repo and make the addresses real; the dataset pages are separate work.
- infra's publish is stopped by refusal rather than deletion, leaving deletion to the later retirement of infra's scripts.
- The eight unmoved datasets cannot be republished between this cutover and each one's own move.
