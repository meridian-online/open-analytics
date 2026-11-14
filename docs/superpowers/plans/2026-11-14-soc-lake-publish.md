# SOC lake publish and open-lake cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The two SOC Protocols publish their six tables into the open lake by `arc run` alone, after the open lake is cut over to arc's publish step (rehearsed first under `lake-test/`).

**Architecture:** Each SOC `arcform.yaml` ends in one `ducklake_write@1` step reading the stamped Parquet and descriptors it already makes. The live catalogue is rebased once onto an https data path; infra stops rebuilding it, flipping the manifest and collecting versions; readers move to DuckDB 2.0. Three pull requests: open-analytics (this branch), infra, web.

**Tech Stack:** arc (arcform `main` at `4047310`), DuckDB 2.0 / DuckLake, Cloudflare R2 (S3 API), Python `unittest` checks, bash, Node (infra monitor), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-14-soc-lake-publish-design.md`

## Global Constraints

- Public repos (open-analytics, web): no Kata refs, no `hughcameron/*` repo names, no local paths in tracked files, commits or pull request text.
- No secret in git, logs, run records or pull request text. `.env` is gitignored; `.env.example` holds names only.
- Credential variables: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`; bucket `meridian-open`.
- Lake addresses: `public_base: https://openlake.meridian.online/`, `catalog: catalog/open.ducklake`, `data_path: lake/`, `runs: runs/`, `manifest: manifest.json`, `local_catalog: build/lake/open.ducklake`.
- Tables: `bls.soc_2018` (key `code`), `bls.soc_2018_titles` (`entry_id`), `bls.soc_2010_to_2018` (`from_code, to_code`); `ons.soc_2020`, `ons.soc_2020_titles`, `ons.soc_2010_to_2020` likewise. Readable copies at `<namespace>/<name>.parquet`.
- arc for this work is `$ARC=$HOME/.arc-main/bin/arc` (Task 1). Do not replace the `arc` on `PATH`; another session uses it.
- One Rust build on this machine this session (Task 1). `scripts/arc_manifest_load` is checked in CI only.
- Every write to `meridian-open` outside `lake-test/` needs Hugh's go-ahead for that write. Merges are Hugh's.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A run with no `.env` at all, or one with only some variables set: the publish step publishes locally and the run is green (Task 3 runs both cases).
2. A SOC model run on DuckDB 2.0 that silently changes results, e.g. `~` regex now matching part of a string: the SOC model tests must pass on 2.0 unchanged (Task 2).
3. The engine check reading the eight unmoved datasets after `DUCKDB_VERSION` moves: it must neither redden on their 1.5.5 footers nor stop checking them (Task 2 test).
4. infra's publish or collector run by hand after the cutover: it refuses with a message, and writes nothing (Task 5 test).
5. A rebased catalogue missing one of the eight datasets or a view: caught before upload by the read-back check (Task 7 and 8 check script).

---

### Task 1: Build arc from arcform's main

**Files:** none tracked.

- [ ] **Step 1:** In `~/github/meridian-online/arcform` at `4047310` (`git rev-parse HEAD` must print `40473103aba2c901d49e6fe175abe4bc99ddacbf`), run `cargo install --path . --locked --root $HOME/.arc-main`, linking Homebrew's DuckDB 2.0.0 library (no `bundled-duckdb`). No other cargo build runs at the same time.
- [ ] **Step 2:** Verify: `$HOME/.arc-main/bin/arc --version` prints the main-branch version, and `$HOME/.arc-main/bin/arc` lists `ducklake_write@1` among its operators (its catalogue or `arc lake --help`). `arc lake rebase --help` prints the rebase flags.

### Task 2: DuckDB 2.0 pins and ARC_REV

**Files:**
- Modify: `datasets/soc_2018/arcform.yaml`, `datasets/soc_2020/arcform.yaml` (`engine_version: ">=2.0"`; every `duckdb==1.5.5` → `duckdb==2.0.0`; the comments naming the 1.3 floor and `DUCKDB_VERSION`)
- Modify: `.github/workflows/descriptors.yml` (`DUCKDB_VERSION: "2.0.0"`, `ARC_REV: "40473103aba2c901d49e6fe175abe4bc99ddacbf"`, `ARC_LIBDUCKDB_VERSION` and `ARC_RUST_TOOLCHAIN` as arcform's `.github/workflows/` use at that rev)
- Modify: `scripts/arc_manifest_load/Cargo.toml` and `Cargo.lock` (rev twice, as its comment says; lock regenerated with `cargo generate-lockfile`/`cargo update -p arcform` only, not built)
- Modify: `scripts/check_engine_version.py`, `scripts/test_check_engine_version.py`
- Modify: docstrings of `scripts/test_soc_2018_models.py`, `scripts/test_soc_2020_models.py` (`duckdb==2.0.0`)

**Interfaces:** Produces: CI pinned to DuckDB 2.0 and arc at `4047310`, so Task 3's `ducklake_write@1` step loads in `arc-manifest-load`.

- [ ] **Step 1: Engine-check test.** In `test_check_engine_version.py` add `test_each_dataset_held_to_its_protocols_stamp_pin`: a fixture dataset whose `arcform.yaml` stamp step pins `duckdb==1.5.5` and whose footer names v1.5.5 passes while the check runs as 2.0.0; the same dataset with a v1.5.6 footer fails naming both versions; a dataset whose Protocol pins nothing falls back to `DUCKDB_VERSION`.
- [ ] **Step 2:** Run `uv run --with duckdb==2.0.0 python3 scripts/test_check_engine_version.py`. Expected: the new test fails.
- [ ] **Step 3:** In `check_engine_version.py` add `expected_version(dataset_dir: Path, default: str) -> str`, reading the `duckdb==X` pin from the dataset's `arcform.yaml` `stamp_descriptor.py` step (all such pins in one Protocol must agree, else exit 2 naming them), else `default`. Hold each footer to it. Update the module docstring and the workflow comment: footers name the writer their own Protocol pins, so each Protocol's pin moves when it moves onto the publish step.
- [ ] **Step 4:** Make the pin edits listed under Files.
- [ ] **Step 5: Verify.**
  - `uv run --with duckdb==2.0.0 python3 scripts/test_check_engine_version.py` passes.
  - `uv run --quiet --with duckdb==2.0.0 --with openpyxl python3 scripts/test_soc_2018_models.py` and the same for `test_soc_2020_models.py` pass unchanged. If a model's result changes on 2.0, stop and report the case: it is a model fix, made in this task with a test naming the 2.0 behaviour.
  - `uv run --with duckdb==2.0.0 python3 scripts/check_engine_version.py` against the shipped datasets: exit 0 (the eight are held to 1.5.5).
  - Every other `scripts/test_*.py` passes on `duckdb==2.0.0`, or a failure is shown to also occur on `main` and is recorded as unrelated.
- [ ] **Step 6: Commit and push**, then read CI's `arc-manifest-load` and `arc-manifest-load-self-test` jobs (`gh run watch`). Expected: green. A loader failure is fixed here before Task 3.

### Task 3: The publish step in both SOC Protocols

**Files:**
- Modify: `datasets/soc_2018/arcform.yaml`, `datasets/soc_2020/arcform.yaml` (`dotenv: [.env]`; final step `publish`; header BOUNDARY note replaced)
- Create: `datasets/soc_2018/.env.example`, `datasets/soc_2020/.env.example` (the three variable names, empty values, a comment saying what they are for)
- Modify: `.gitignore` (`.env`, but not `.env.example`)
- Modify: `datasets/soc_2018/README.md`, `datasets/soc_2020/README.md` (the publish step; how to publish: copy `.env.example` to `.env`, fill it, `arc run --publish`; a run without it publishes locally into `build/lake/`; readers need DuckDB 2.0)
- Modify, if the checks need it: `scripts/check_protocol_readme.py`, `scripts/test_check_protocol_readme.py`, `scripts/test_manifest_one_writer.py`

**Interfaces:** Consumes Task 1's `$ARC`, Task 2's pins. Produces: the `publish` step, `with:` exactly as the Global Constraints give, `tables:` in the order code list, titles, crosswalk, each `from: build/<table>.parquet`, `descriptor: <table>.datapackage.json`, `readable_copy: <ns>/<table>.parquet`; `credential: {type: r2, bucket: meridian-open, env: {key_id: R2_ACCESS_KEY_ID, secret: R2_SECRET_ACCESS_KEY, account_id: R2_ACCOUNT_ID}}`.

- [ ] **Step 1:** Add the step and the files above.
- [ ] **Step 2: Repo checks.** `python3 scripts/check_protocol_readme.py` and `scripts/test_check_protocol_readme.py`, `scripts/test_manifest_one_writer.py`, `scripts/test_registry.py` pass. If `test_manifest_one_writer.py` treats the publish step's uploads or `build/lake/` as a second writer of a file, extend it with a test naming `ducklake_write` and fix.
- [ ] **Step 3: Local run, no credential.** With no `.env` and none of the three variables in the environment: `$ARC run` in `datasets/soc_2018`, then `datasets/soc_2020`. Expected: exit 0; the publish step reports a local publish naming the three missing variables; `build/lake/open.ducklake` exists.
- [ ] **Step 4: Local lake reads back.** `duckdb` (2.0) `ATTACH 'ducklake:datasets/soc_2018/build/lake/open.ducklake' AS l (READ_ONLY)`: `bls.soc_2018`, `bls.soc_2018_titles`, `bls.soc_2010_to_2018` have the row counts of the stamped Parquet; `meta.descriptors` has three rows; `meta.fields` has one row per column. Same for `ons` in `soc_2020`, plus `meta.descriptors` for `ons.soc_2020` carrying the OGL `x-attribution`.
- [ ] **Step 5: Partial credential.** With only `R2_ACCOUNT_ID` set (a dummy value), `$ARC run` in `datasets/soc_2018` publishes locally and is green; `$ARC run --publish` fails the publish step naming the two missing variables. Unchanged second plain run reports the local publish `unchanged`.
- [ ] **Step 6: Commit and push**; CI's manifest-load job is green with the new step.

### Task 4: Registry, namespaces and README rows

**Files:** Modify `datasets/registry.json`, `README.md`; test `scripts/test_registry.py` (shipped registry).

- [ ] **Step 1:** Add namespaces `bls` (`kind: held`, `issuer: "U.S. Bureau of Labor Statistics"`, `claimed: false`) and `ons` (`issuer: "Office for National Statistics"`). Add six entries in the existing shape (`uid` a fresh UUID4, `schema`, `name`, `title` from each descriptor's `title`, `aliases: []`, `manifest`, `doi: null`, `protocol: datasets/soc_2018/arcform.yaml` or `soc_2020`).
- [ ] **Step 2:** Add the six rows to the root README's dataset table (rows from each descriptor; row counts from Task 3's runs; the UK rows' licence "OGL v3.0" with ONS's attribution line), and the six addresses to its address paragraph. `python3 scripts/check_descriptors.py` (its README-catalogue rule) and `scripts/test_registry.py` pass.
- [ ] **Step 3: Commit and push.**

### Task 5: infra pull request — stop the old publish, monitor on DuckDB 2.0

Repo `~/github/meridian-online/infra`, branch `openlake-cutover`, from `origin/main`, in a worktree.

**Files:**
- Modify: `publish/publish-all.sh`, `publish/publish-catalog.sh`, `publish/publish-protocol-artifact.sh`, `publish/publish-edgar-gleif-crosswalk.sh`, `publish/publish-gleif-search.sh`: with `--publish`, exit 1 before any write with "the open lake is published by each Protocol's arc publish step since the cutover; see publish/README.md". `--plan`/verify unchanged.
- Modify: `publish/gc-versions.sh`: `--delete` refuses likewise; the dry run still works.
- Modify: `.github/workflows/publish-openlake.yml`: the publish job is removed or made to fail with the same message; verify and schedule stay.
- Modify: `monitor/openlake-check.mjs`: freshness from the catalogue's newest snapshot (`SELECT max(snapshot_time) FROM ducklake_snapshots('m')` after the `ATTACH`), not `manifest.updated` or `catalog.published`; the pointer check uses `catalog.url` only.
- Modify: `.github/workflows/openlake-uptime.yml`: `DUCKDB_VERSION: "2.0.0"`, comment updated.
- Modify: `publish/README.md`: the cutover, dated 2026-11-14, and what is stopped.
- Test: `publish/test/` (a new `refuses-after-cutover.test.sh`: each script with `--publish`, and `gc-versions.sh --delete`, exits 1 with the message and makes no network call, run with no credentials).

- [ ] **Step 1:** Write `refuses-after-cutover.test.sh`; run it: fails.
- [ ] **Step 2:** Make the refusals; run it: passes. Existing `publish/test/*.test.sh` still pass, or each failure is a test of the removed path and is removed with a line in the README saying why.
- [ ] **Step 3:** Monitor changes. Run `node monitor/openlake-check.mjs` against today's lake with DuckDB 1.5.4 on `PATH`: unchanged result. (It runs against the rebased catalogue in Task 7 step 5 with 2.0.)
- [ ] **Step 4:** Commit, push, open the pull request, roborev branch review, CI. Ready for Hugh to merge before Task 8. Do not merge.

### Task 6: web pull request — DuckDB 2.0 or later beside each ATTACH

Repo `~/github/meridian-online/web`, branch `lake-duckdb-2`, from `origin/main`, in a worktree.

**Files:** Modify `content/docs/datasets/use-anywhere.mdx` and every other page `git grep -ln "ducklake:https"` lists; `docs/docs-inventory.md` entries for those pages, as web's CLAUDE.md requires.

- [ ] **Step 1:** Each `ATTACH` snippet's surrounding prose says the lake needs DuckDB 2.0 or later, because DuckDB 2.0 writes the catalogue in a format 1.x cannot open. Follow web's CLAUDE.md writing and inventory rules.
- [ ] **Step 2:** web's CI checks (`npm run build` and its gates) pass locally or in CI.
- [ ] **Step 3:** Commit, push, open the pull request, roborev branch review, CI. Ready for Hugh to merge before Task 8. Do not merge.

### Task 7: Rehearsal under `lake-test/` (needs Hugh's token)

**Files:** untracked only: `datasets/soc_2018_rehearsal/`, `datasets/soc_2020_rehearsal/` (copies), the `.env` files, and a read-back script in the session scratchpad, `check_lake.py`.

**Interfaces:** Produces `check_lake.py --catalog <url-or-path> [--expect-soc <counts.json>]` (only the tables the JSON lists are checked; absent, no SOC table is checked) --expect-existing <manifest url>`, exit 0 when: the six SOC tables exist with the given row counts; each of the eight existing datasets in the live manifest reads through the catalogue with the manifest's `rows`; every view in the old catalogue exists; `meta.descriptors` holds the six SOC tables; `ons.soc_2020`'s descriptor carries ONS's attribution line. Runs with DuckDB 2.0, no credentials.

- [ ] **Step 1:** Flag the issue needs-human; Hugh creates an R2 token for `meridian-open` and fills `datasets/soc_2018/.env` (and a copy for `soc_2020`). Confirm `git status` does not show `.env`.
- [ ] **Step 2:** `$ARC lake rebase --from https://openlake.meridian.online/catalog/open.ducklake --out <scratch>/rehearsal.ducklake --data-path https://openlake.meridian.online/lake-test/lake/ --upload https://openlake.meridian.online/lake-test/catalog/open.ducklake --bucket meridian-open --credential-env r2:key_id=R2_ACCESS_KEY_ID,secret=R2_SECRET_ACCESS_KEY,account_id=R2_ACCOUNT_ID` (variables exported from the `.env` for this command only).
- [ ] **Step 3:** Copy each SOC Protocol to `datasets/soc_20XX_rehearsal/` with every publish key under `lake-test/` (`catalog`, `data_path`, `runs`, `manifest`, each `readable_copy`); `$ARC run --publish` in each. Expected: green, each publish reports a committed snapshot.
- [ ] **Step 4:** `check_lake.py` against `https://openlake.meridian.online/lake-test/catalog/open.ducklake`: exit 0. `read_parquet('https://openlake.meridian.online/lake-test/bls/soc_2018.parquet')` count matches. Re-run `soc_2018_rehearsal` with `--publish`: the step reports `unchanged`; the test catalogue's ETag is unchanged.
- [ ] **Step 5:** Task 5's monitor, with DuckDB 2.0, pointed at the test base (`OPENLAKE_BASE` or its equivalent set to `https://openlake.meridian.online/lake-test`): its catalogue checks pass.
- [ ] **Step 6:** Delete every object under `lake-test/` (list with the S3 API, delete, list again: empty) and every `runs/` object the rehearsal wrote outside it, if any. Remove the rehearsal copies. Report to Hugh.

### Task 8: Live cutover (needs Hugh's merges and go-aheads)

- [ ] **Step 0:** Before any live write: `roborev review --branch --wait` on this branch; fix or answer every Medium-and-above finding and close it, so the live publish runs reviewed code.
- [ ] **Step 1:** Infra and web pull requests merged (Hugh). Confirm on `main` of each.
- [ ] **Step 2:** Record the live catalogue's ETag through the S3 API (`head-object`, not the public https response, which a CDN may alter), then `$ARC lake rebase --from https://openlake.meridian.online/catalog/open.ducklake --out <scratch>/open.ducklake --data-path https://openlake.meridian.online/lake/`. `check_lake.py --catalog <scratch>/open.ducklake --expect-soc <none>`: exit 0 for the eight.
- [ ] **Step 3 (go-ahead):** Upload `<scratch>/open.ducklake` to `catalog/open.ducklake` with `If-Match: <recorded ETag>` through the S3 API (aws CLI `s3api put-object --if-match`, endpoint `https://<account>.r2.cloudflarestorage.com`, or curl `--aws-sigv4`). A 412 stops the cutover. Then `check_lake.py` against the live https catalogue: exit 0. Monitor workflow run on infra `main` (`gh workflow run openlake-uptime.yml`): green.
- [ ] **Step 4 (go-ahead):** `$ARC run --publish` in `datasets/soc_2018`. `check_lake.py` with the three BLS counts: exit 0. Comment on the SOC hosting issue that `bls/soc_2018`, `bls/soc_2018_titles` and `bls/soc_2010_to_2018` are live.
- [ ] **Step 5 (go-ahead):** Same for `datasets/soc_2020`; comment that the three ONS tables are live.
- [ ] **Step 6:** `curl -s https://openlake.meridian.online/manifest.json`: six SOC entries, `catalog.url` names a pinned copy of the rebased catalogue, the eight entries unchanged. Monitor run: green.

### Task 9: Fold, review, ready

- [ ] **Step 1:** Write `docs/publishing.md`: how a Protocol publishes (the step, the local fallback, `.env`/`.env.example`, `--publish`, DuckDB 2.0 for readers), the cutover of 2026-11-14 and its order with reasons, Hugh's rulings (the cutover in this session; rehearsed first; the agent runs writes with a go-ahead per production write), and what remains (the eight Protocols, retiring infra's scripts, web reading the catalogue). Link it from `README.md` and the two SOC READMEs.
- [ ] **Step 1b:** Extend `scripts/check_engine_version.py`'s discovery to per-table descriptors (`datasets/*/*.datapackage.json`), now that SOC's readable copies exist, with a test; run it against the shipped datasets: exit 0.
- [ ] **Step 2:** Delete this plan and the spec from the branch.
- [ ] **Step 3:** `roborev review --branch --wait` (covering the fold, since Task 8 Step 0 reviewed the rest); fix or answer each Medium-and-above finding; close it. `gh pr create`; `gh pr checks <pr> --watch`. Link the pull request to the issue in Forge. Description: CI test totals and the rulings. Report it ready and stop.

## Self-review notes

Spec coverage: goals 1–2 Task 3; 3 Tasks 7–8; 4 Task 5; 5 Task 6; 6 Task 2; 7 Task 4; docs Task 9. Ordering: Task 2 before 3 (loader must know the op); 5 and 6 can run beside 2–4 but subagents run one at a time; 7 needs the token and Tasks 1–5; 8 needs 7 and Hugh's merges of 5 and 6.
