# A pull request's CI checks the change, and the lake holds the descriptor: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publishing Protocols stop committing `datapackage.json`; descriptors are emitted from the open lake; pull-request CI in open-analytics and web reads no live lake; a scheduled lake check on `main` reports the merge-to-publish gap and fails on drift; arc's `publish` gains a descriptor diff.

**Architecture:** One emitter (`scripts/emit_descriptor.py`) turns the lake's `meta.descriptors` plus `manifest.json` back into datapackage.org files, either as a bundle (one JSON file, for a Run step that arc test can fixture) or as a tree shaped like `datasets/` (so every existing `check_*.py` keeps its `--datasets-dir` interface). A Run step before `publish` assembles such a tree with the built descriptor laid in and runs the checks that can only be known there. The live comparisons move to a `lake` workflow.

**Tech Stack:** Python 3.12 + DuckDB 2.0.0 (`ducklake`, `httpfs`), GitHub Actions, arc at `ARC_REV` (Rust, arcform), Node (web scripts).

**Spec:** `docs/superpowers/specs/2026-11-17-ci-checks-the-change-design.md`

## Global Constraints

- DuckDB `2.0.0` everywhere a script here pins one (`DUCKDB_VERSION`, `uv@1` `deps`).
- The public catalogue is `ducklake:https://openlake.meridian.online/catalog/open.ducklake`; descriptors are `meta.descriptors(table_name VARCHAR, descriptor JSON)`; `manifest.json` is `https://openlake.meridian.online/manifest.json`, each entry carrying `bytes`, `sha256`, `published`, `url`.
- No pull-request job in either repo reads `openlake.meridian.online`.
- `examples/` keeps its committed descriptors and current checks.
- Each existing check's self-test still proves the check can fail; no check is deleted without the spec's stated reason.
- A `uv@1` step's `sha256` is the script's bytes: any edit to a script a manifest runs means updating that digest in every manifest that runs it (`shasum -a 256 <script>`).
- Public repos: no Kata refs, no private repo names, no local paths in commits, PR text or tracked files.
- Markdown prose: one line per paragraph.
- One cargo build at a time; cargo jobs capped at 6.

## Review Focus

1. **A table in the catalogue with no `manifest.json` entry, or the reverse** (e.g. a table published but its manifest entry not yet written). Expect the emitter to name the table and exit 1, not write a descriptor missing `bytes`/`hash`. Test in Task 1.
2. **The edge serving a stale catalogue or readable copy to the lake check.** Expect every read in the lake check to bypass the edge (a query string on readable copies; the catalogue attached by a query-stringed URL), so a publish minutes ago is seen. Test in Task 5 (the URL builder adds the query to every http path the tree names).
3. **A Protocol publishing several tables** (SOC: three descriptors per Protocol, `<table>.datapackage.json`). Expect the tree to hold each under its own filename and the pre-publish check to run over all three. Test in Tasks 1 and 2.
4. **A first publish: the table is not in the lake yet** (a new code list). Expect the emitter to omit it, the pre-publish check to run label agreement with the built descriptor alone for that table, and the lake check to report it as awaiting publish. Test in Tasks 2 and 5.
5. **The lake unreachable on a publisher's laptop.** Expect `emit_descriptors` to fail the Run with a message naming the catalogue URL, not a pre-publish check that passes against an empty lake. Test in Task 1.

---

## open-analytics (branch `ci-checks-the-change`, this worktree)

### Task 1: `emit_descriptor.py`

**Files:**
- Create: `scripts/emit_descriptor.py`, `scripts/test_emit_descriptor.py`
- Modify: `.github/workflows/descriptors.yml` (add an `emit-descriptor-self-test` job running the test)

**Interfaces:**
- Produces:
  - `load_descriptors(catalog: str, manifest: str) -> dict[str, dict]`: table name (`census.fips`) to its full datapackage.org descriptor, with `created` from the manifest's `published` and the sole resource's `bytes`/`hash` from `bytes`/`sha256` (`hash` written as `sha256:<hex>` if that is the form the committed descriptors use; check one).
  - `protocol_tables(datasets_dir: Path) -> list[tuple[str, str, str]]`: `(slug, table_name, descriptor_filename)` read from each `datasets/*/arcform.yaml`'s `ducklake_write@1` step (`tables[].name`, basename of `tables[].descriptor`).
  - `write_bundle(descriptors, out: Path)`: one JSON object, table name to descriptor.
  - `write_tree(datasets_dir, descriptors, out: Path, bypass_edge: bool)`: copies `datasets_dir` (skipping every `build/`) to `out/datasets/`, then writes each table's descriptor to `out/datasets/<slug>/<descriptor_filename>`. With `bypass_edge`, each http `resources[].path` gets `?v=<first 16 hex of hash>`.
  - CLI: `emit_descriptor.py <table> [--out FILE]` (default stdout), `--all --bundle FILE`, `--all --tree DIR [--bypass-edge]`, `--catalog` (default the public catalogue), `--manifest` (default the public manifest URL; a path is accepted).

- [ ] **Step 1: Write failing tests** in `scripts/test_emit_descriptor.py`, against a scratch DuckLake built in a temp dir (a `meta.descriptors` table holding two descriptors with no `created`/`bytes`/`hash`, one of them a SOC-shaped table) and a scratch `manifest.json` on disk:
  - `test_single_table_round_trips`: the emitted descriptor equals the stored one plus `created == entry["published"]`, `resources[0]["bytes"] == entry["bytes"]`, hash from `entry["sha256"]`.
  - `test_missing_manifest_entry_refused`: a stored table without a manifest entry exits 1, stderr names the table.
  - `test_unreachable_catalogue_refused`: `--catalog ducklake:/nonexistent/x.ducklake` exits 1 and stderr names that path.
  - `test_tree_layout`: `--all --tree` writes `datasets/<slug>/datapackage.json` and, for the multi-table Protocol, `datasets/<slug>/<table>.datapackage.json`; `datasets/label-agreement.json` and each `descriptor.overrides.json` are copied; no `build/` is copied.
  - `test_bypass_edge`: with `--bypass-edge` every http path ends `?v=<16 hex>`; without, paths are unchanged.
  - `test_unpublished_table_omitted`: a Protocol table absent from the catalogue is absent from bundle and tree, and listed on stderr as `not yet published: <table>`.
- [ ] **Step 2:** `python scripts/test_emit_descriptor.py` fails (no module).
- [ ] **Step 3:** Implement. Attach with `ATTACH '<catalog>' AS lake (READ_ONLY)` after `INSTALL ducklake; INSTALL httpfs`.
- [ ] **Step 4:** Tests pass. Then against the live lake from a laptop: `python scripts/emit_descriptor.py census.fips | python -c "import json,sys; d=json.load(sys.stdin); print(d['resources'][0]['bytes'])"` prints the same `bytes` as `datasets/fips/datapackage.json` on `main` (seconds).
- [ ] **Step 5:** Commit.

### Task 2: `check_before_publish.py`

**Files:**
- Create: `scripts/check_before_publish.py`, `scripts/test_check_before_publish.py`
- Modify: `.github/workflows/descriptors.yml` (self-test job)

**Interfaces:**
- Consumes: `write_tree`-shaped trees from Task 1 (call `write_tree` directly with descriptors loaded from the bundle).
- Produces: CLI `check_before_publish.py --datasets-dir ../../datasets --slug <slug> --lake-bundle build/lake_descriptors.json --built <descriptor_path>=<parquet_path> [--built ...] --json build/check_before_publish.json`. Exit 0 when every check passes, 1 naming each failing check and finding.

What it runs, over a tree in `build/check_tree/` (bundle descriptors laid in, then each `--built` descriptor laid over its table with `resources[0].path` set to the absolute local Parquet path):
- `check_label_agreement.py --strict --datasets-dir <tree>/datasets`
- `check_frictionless.py --datasets-dir <tree>/datasets --only`-equivalent narrowing to `<slug>` if it has one, else whole tree
- `check_descriptors.py --datasets-dir <tree>/datasets --only <slug> --require-self-description --readme /dev/null`-equivalent (skip the catalogue rule here; it belongs to the lake check): rules 1–7 and the licence rules, against the local Parquet.

- [ ] **Step 1: Failing tests**, each on a scratch datasets dir with two code-list packages and a tiny built Parquet:
  - `test_clean_passes`: exit 0, JSON lists the three checks as passed.
  - `test_label_drift_fails`: the built descriptor labels `description` `representation.text.word` where the bundle's sibling has `plain_text`, no reason recorded → exit 1, output names `label agreement` and the field.
  - `test_override_broken_by_data`: built descriptor has `required: true` on a column with a NULL → exit 1 naming `required`.
  - `test_frictionless_refusal`: `pattern` on an integer field → exit 1 naming frictionless.
  - `test_first_publish`: the bundle lacks the slug's table → still runs; label agreement compares against siblings only; exit 0 on a clean descriptor.
  - `test_multi_table`: three `--built` pairs for one slug are all laid in and all checked.
- [ ] **Step 2:** Run; fail.
- [ ] **Step 3:** Implement by calling each script's `main(argv)` in-process (they all expose `main`), so one `uv@1` step needs one script digest. If a check needs a narrowing flag it lacks (`--only` on `check_frictionless.py`, catalogue-rule skip on `check_descriptors.py`), add the smallest flag, with a self-test case in that script's existing test file.
- [ ] **Step 4:** Tests pass; every existing `scripts/test_check_*.py` still passes.
- [ ] **Step 5:** Commit.

### Task 3: Protocols write descriptors under `build/` and check before publishing

**Files:**
- Modify: all 14 `datasets/*/arcform.yaml`; delete the 18 committed `datasets/*/datapackage.json` and `datasets/*/*.datapackage.json`; each Protocol `README.md` (regenerated step list); `datasets/edgar_gleif/scripts/crosswalk_join.py` if it globs `--datasets-dir`.

**Interfaces:**
- Consumes: Task 1 CLI (`--all --bundle`), Task 2 CLI.
- Produces, in every publishing Protocol, two steps after the stamp step(s) and before `publish`:
  - `emit_descriptors`: `uv@1`, `script: ../../scripts/emit_descriptor.py`, `args: [--all, --bundle, build/lake_descriptors.json]`, `produces: [build/lake_descriptors.json]` (exactly one file, so `arc test` can fixture it by step name).
  - `check_before_publish`: `uv@1`, `script: ../../scripts/check_before_publish.py`, reads the bundle and the built descriptor(s) and Parquet(s), `produces: [build/check_before_publish.json]`.
- Each `stamp_descriptor` `--out-descriptor` and each `publish` `tables[].descriptor` moves from `<name>.json` to `build/<name>.json` (same basename).
- edgar_gleif's join step reads `build/lake_descriptors.json` instead of `../../datasets/{edgar,gleif,naics}/datapackage.json`; `crosswalk_join.py` gains `--lake-bundle` and resolves siblings from it (its own descriptor from `build/stage/described.json` as now).

- [ ] **Step 1:** Edit the manifests. `emit_descriptors` must run before edgar_gleif's join step there (`depends_on` as that file's style uses).
- [ ] **Step 2:** `git rm` the 18 descriptors; confirm each Protocol's `.gitignore` covers `build/`.
- [ ] **Step 3:** `python scripts/check_protocol_readme.py --write`, then `python scripts/check_protocol_readme.py` passes.
- [ ] **Step 4:** `cd scripts/arc_manifest_load && cargo run --locked -- ../../datasets ../../examples` (or the job's exact command from `descriptors.yml`) loads every manifest (a few minutes on a cold build).
- [ ] **Step 5:** A real local run with no credential: `cd datasets/fips && ~/.arc-fips/bin/arc run --force` finishes, `build/check_before_publish.json` reports three passes, and `build/lake/open.ducklake` holds `census.fips` (minutes; reads the live lake for the bundle, which is fine locally). Repeat for `soc_2018` (multi-table).
- [ ] **Step 6:** Commit.

### Task 4: Pull-request tests that read committed descriptors

**Files:**
- Create: `datasets/{fips,naics_2017_to_2022,naics_2022_titles,ourairports}/testdata/lake_descriptors.json`
- Modify: those four `tests.yaml` (fixture `emit_descriptors: testdata/lake_descriptors.json`); `scripts/test_soc_2018_models.py`, `scripts/test_soc_2020_models.py` (drop `DescriptorTest`, keep model tests); any other `scripts/test_*.py` that reads a deleted descriptor (`grep -l "datapackage.json" scripts/test_*.py`: each either reads a scratch descriptor already, or is changed to)

- [ ] **Step 1:** Build each fixture bundle from the live lake once (`emit_descriptor.py --all --bundle`), trimmed to the tables the Protocol's labels are compared with. Commit as fixtures.
- [ ] **Step 2:** `arc test` in each of the four Protocols passes (`~/.arc-fips/bin/arc test` with `FINETYPE_VERSION` 0.6.67 binary on PATH; about a minute each).
- [ ] **Step 3:** Add one `tests.yaml` case in `fips` with `expect: { fails_with: "label agreement" }` whose break gives the fixture bundle a sibling with a disagreeing label, proving the step can fail under `arc test`.
- [ ] **Step 4:** `for t in scripts/test_*.py; do python $t || echo FAIL $t; done` prints no FAIL (a few minutes).
- [ ] **Step 5:** Commit.

### Task 5: CI splits into pull-request checks and a `lake` workflow

**Files:**
- Create: `.github/workflows/lake.yml`, `scripts/lake_check.py`, `scripts/test_lake_check.py`
- Modify: `.github/workflows/descriptors.yml` (remove `descriptors-match-their-data`, `engine-version-matches-the-pin`, `joins-run-against-their-data`, `code-list-contract`, `label-agreement`, `descriptors-are-accepted-by-the-reference-implementation`, `constraint-ratchet`, `endpoints-serve-what-is-declared`, and `description-needs-no-repository` if it reads a live object; keep their self-tests; rewrite the header comments to say where each guarantee lives now); `README.md` (each catalogue row links the readable copy); `scripts/check_descriptors.py` (`CATALOGUE_DESCRIPTOR_LINK` matches the readable-copy link and finds the table by it)

**Interfaces:**
- Consumes: Task 1 `--all --tree --bypass-edge`.
- Produces: `lake_check.py --tree DIR --repo . --json FILE`: runs `check_descriptors.py --require-self-description`, `check_engine_version.py`, `check_label_agreement.py --strict`, `check_frictionless.py`, `check_code_list.py`, the joins script and `publish_dataset.py verify` over the tree, and sorts every finding into `awaiting_publish` or `drift`. Exit 1 only when `drift` is non-empty. Step summary lists both.
- `classify(slug: str | None, published_at: datetime | None, last_change_at: datetime) -> Literal["awaiting_publish", "drift"]`: `awaiting_publish` when the table is not published, or when the repo inputs the finding compares against (`datasets/<slug>/`, `README.md` for a catalogue finding, `datasets/label-agreement.json` for a label finding) changed in git after the table's `published`; otherwise `drift`. `last_change_at` is `git log -1 --format=%cI -- <paths>`.

`lake.yml`: `on: push: branches [main]`, `schedule: cron "17 6 * * 1"`, `workflow_dispatch`; checkout with full history; install DuckDB 2.0.0 and the Python deps the checks need; `python scripts/emit_descriptor.py --all --tree "$RUNNER_TEMP/tree" --bypass-edge` with the catalogue attached at `ducklake:https://openlake.meridian.online/catalog/open.ducklake?v=<run id>`-style edge bypass (confirm DuckLake accepts the query string; if not, note it in the workflow header and in `docs/publishing.md`); then `lake_check.py`.

- [ ] **Step 1: Failing tests** in `scripts/test_lake_check.py`: `classify` cases — unpublished table → awaiting; repo changed after publish → awaiting; repo unchanged since publish → drift; catalogue finding uses README's time. And an end-to-end case on a scratch tree whose served object (a local file) disagrees with its descriptor's `bytes` → exit 1 with one drift finding.
- [ ] **Step 2:** Fail; implement; pass.
- [ ] **Step 3:** Pull-request jobs: `grep -n "openlake.meridian.online" .github/workflows/descriptors.yml` returns nothing outside comments, and no remaining PR job runs a script with a live URL default.
- [ ] **Step 4:** Run `lake.yml`'s commands locally against the live lake: drift is empty (all tables published from `main`'s inputs today), and README/catalogue findings are none (minutes; downloads every object for `verify`).
- [ ] **Step 5:** Commit.

### Task 6: Docs and fold

**Files:**
- Modify: `docs/publishing.md`, `docs/code-lists.md` where they describe committing descriptors or the two-pull-request path; `README.md` (how to get a descriptor: the emitter, the SQL, the footer)
- Delete: `docs/superpowers/specs/2026-11-17-ci-checks-the-change-design.md`, `docs/superpowers/plans/2026-11-17-ci-checks-the-change.md`

- [ ] **Step 1:** `docs/publishing.md`: replace "Moving a Protocol onto the step" and the two-pull-request passages with the one path (build locally, merge, publish from `main`, the scheduled check reports the gap); add a section "Where a descriptor lives" with the spec's rulings and reasons in this repo's own words; keep the per-dataset history as history.
- [ ] **Step 2:** `grep -rn "datasets/[a-z_0-9]*/datapackage.json" README.md docs scripts .github datasets --include='*' | grep -v examples` shows only intended references.
- [ ] **Step 3:** Delete the spec and plan; commit.

## web (worktree of `meridian-online/web`, branch `descriptors-from-the-lake`)

### Task 7: web reads descriptors from the lake, and parity leaves pull requests

**Files:**
- Modify: `.github/workflows/ci.yml` (move the steps that set `MERIDIAN_OPEN_ANALYTICS_DIR` for descriptor parity, and the checks that read descriptors, out of the pull-request path); create `.github/workflows/lake-parity.yml` (push to main, weekly, dispatch); headers of `scripts/build-dataset-profiles.mjs`, `build-dataset-joins.mjs`, `build-dataset-summaries.mjs`, `build-protocol-contracts.mjs`, `dataset-addresses.mjs` (how to regenerate: run open-analytics' emitter into a tree, point `MERIDIAN_OPEN_ANALYTICS_DIR` at it); `src/data/protocol-research/*.json`, `src/data/dataset-research/*.json` (citations of `blob/main/datasets/<slug>/datapackage.json` move to the readable copy URL)

**Interfaces:**
- Consumes: open-analytics `scripts/emit_descriptor.py --all --tree DIR --bypass-edge` (the tree's `DIR/datasets/<slug>/…` matches `descriptorFile(dir, slug)` unchanged).

`lake-parity.yml`: clone open-analytics at `main`, `pip install duckdb==2.0.0`, run the emitter into `$RUNNER_TEMP/oa`, set `MERIDIAN_OPEN_ANALYTICS_DIR=$RUNNER_TEMP/oa`, run the same parity checks `ci.yml` runs today, report in the step summary. Example Protocols still read from the clone itself (`examples/` is copied into nothing; point their reader at the clone).

- [ ] **Step 1:** Locally: emit a tree from the live lake, run each parity check with `MERIDIAN_OPEN_ANALYTICS_DIR` at it; all pass (they pass on `main` today against the committed files, which equal the lake).
- [ ] **Step 2:** Edit workflows; `ci.yml` on a pull request no longer clones open-analytics for descriptors (it may still clone for `examples/`).
- [ ] **Step 3:** Rewrite the research citations; `node scripts/check-dataset-research.mjs` and `check-protocol-research.mjs` pass.
- [ ] **Step 4:** web's full local gate (`npm test`, or what web's CLAUDE.md names) passes.
- [ ] **Step 5:** Commit. This pull request merges right after open-analytics', since its lake job needs the emitter on open-analytics' `main`.

## arcform (worktree of `meridian-online/arcform`, branch `publish-descriptor-diff`)

### Task 8: `ducklake_write@1` gains `on_descriptor_change`

**Files:**
- Modify: `src/operator.rs` (schema: `"on_descriptor_change": {"type": "string", "enum": ["report", "fail"], "default": "report"}` with a description), `src/lake/commit.rs` (the comparison beside the `meta.descriptors` replacement at "Replace a table's rows in `meta.descriptors` and `meta.fields` where they differ"), `src/lake/meta.rs` if the diff helper belongs there; docs where `ducklake_write` options are documented.

**Interfaces:**
- Produces: `descriptor_changes(stored: &Value, new: &Value) -> Vec<DescriptorChange>` where each change names a JSON pointer and old/new value, ignoring `created`, resources' `bytes`/`hash` (already dropped) and finetype confidence fields (find the key the descriptors use, e.g. `x-finetype-confidence`). `fail` refuses the publish, nothing written, when a field's `x-finetype-label` changed and the new field lacks `"x-finetype-nominated": true`; the message names the table, field, old and new label, and says to nominate the label or fix the input. `report` prints every change. No stored descriptor → no output.

- [ ] **Step 1: Failing tests** in `commit.rs`'s test module: `report_lists_a_moved_constraint`, `report_ignores_confidence_and_created`, `fail_refuses_an_unnominated_label_move_and_writes_nothing` (catalogue snapshot count unchanged), `fail_accepts_a_nominated_label_move`, `no_stored_descriptor_reports_nothing`.
- [ ] **Step 2:** `cargo test -j 6 lake::` fails.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `cargo test -j 6` passes (several minutes); `cargo clippy -j 6 --all-targets -- -D warnings` clean if arcform's CI runs it.
- [ ] **Step 5:** Commit.

## Dependencies and order

- Task 1 → Tasks 2, 5, 7. Task 2 → Task 3 → Task 4. Tasks 3, 4, 5 → Task 6 (last on the open-analytics branch). Task 8 is independent.
- Merge order: open-analytics, then web. arcform at any time; moving open-analytics' `ARC_REV` and setting `on_descriptor_change` is a follow-up once arcform's change has merged, as is an `arc` verb to emit a descriptor.

## Verification costs

| Command | Where | Cost |
|---|---|---|
| `python scripts/test_*.py` | open-analytics | seconds each, a few minutes all |
| `arc test` per Protocol (4) | open-analytics | about a minute each; needs finetype 0.6.67 and DuckDB CLI on PATH |
| `arc run --force` (FIPS, SOC 2018), no credential | open-analytics | minutes; reads the live lake |
| arc-manifest-load (`cargo run --locked`) | open-analytics | minutes cold |
| `lake_check.py` against the live lake | open-analytics | minutes; downloads every object |
| web parity checks and `npm test` | web | minutes |
| `cargo test -j 6` | arcform | several minutes |
