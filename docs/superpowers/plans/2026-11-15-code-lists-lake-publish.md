# The four hosted code lists publish into the open lake: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NDC, HCPCS, HTS and Schedule B each end in arc's `ducklake_write@1` publish step and are published live into the open lake, one pull request each.

**Architecture:** Each Protocol's `arcform.yaml` gains the publish step SOC has (`datasets/soc_2018/arcform.yaml` is the model), moves its DuckDB pins to 2.0.0, is force-rebuilt on DuckDB 2.0 into a local lake, is checked against the live table's shape, and is published live after Hugh's go-ahead. Branches stack: `ndc-lake-publish` from `main`, then `hcpcs-lake-publish` from it, `hts-lake-publish`, `schedule-b-lake-publish`.

**Tech Stack:** arc (arcform 4047310), DuckDB 2.0.0, DuckLake, finetype's DuckDB 2.0 extension, uv, Python 3 checks under `scripts/`.

**Spec:** `docs/superpowers/specs/2026-11-15-code-lists-lake-publish-design.md`

## Global Constraints

- arc: a binary built from arcform 4047310, the revision CI pins. Check `arc --version` names it before each run; an older arc on PATH must not be used. Commands below write `arc` for it.
- DuckDB: `engine_version: ">=2.0"`; every `uv@1` step's `deps` pin `duckdb==2.0.0`. No `uv@1` script's `sha256` changes.
- `FINETYPE_DUCKDB_EXT` points at finetype's extension as DuckDB 2.0.0 installs it (`INSTALL finetype FROM community`, under DuckDB's extension directory for v2.0.0) for every rebuild.
- A rebuild before the live write runs with no R2 variable set: `env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY -u R2_ACCOUNT_ID arc run --force`.
- The live write runs only after Hugh's go-ahead for that dataset, as `(set -a; . <credential file outside the repository>; set +a; arc run --publish)` from the Protocol folder. No `.env` file is created in any Protocol folder.
- `on_schema_change` is not set (default `fail`).
- Public repo: no Kata refs, local paths or private repo names in tracked files, commit messages, branch names or pull request text.
- Prose in Markdown is one line per paragraph, not hard-wrapped.

## Review Focus

1. **A column type that drifted under DuckDB 2.0** (for example `DECIMAL` width, `STRUCT` field order in NDC's `active_ingredients`). Expected: the move stops before the live write and Hugh sees the difference. Pinned by Task 1's shape check.
2. **A row count far from the last publish** (more than 10% from the live table). Expected: stop before the live write and bring the figures to Hugh. Pinned by Task 1's shape check.
3. **An upstream that changed shape or failed today** (openFDA, CMS, USITC, Census). Expected: the Protocol's own load checks fail the rebuild; retry, and if it stays down, the dataset's branch goes ready without its live publish and says so.
4. **The other datasets after a publish.** Expected: every other table and the eight views still read through the live catalogue. Pinned by Task 2's read-back.
5. **A re-run over unchanged data.** Expected: the step reports nothing changed and commits nothing. Pinned by Task 2's second run.

## Files

Per dataset `<d>` in `ndc`, `hcpcs`, `hts`, `schedule_b`:

- Modify `datasets/<d>/arcform.yaml`: pins, `dotenv`, `publish` step, header and trailing comments.
- Create `datasets/<d>/.env.example`: a copy of `datasets/soc_2018/.env.example`.
- Modify `datasets/<d>/datapackage.json`: restamped by the rebuild.
- Modify `datasets/<d>/README.md`: regenerated step list and a paragraph on the publish step, modelled on `datasets/soc_2018/README.md`'s.
- Modify `docs/publishing.md`, `docs/code-lists.md`: the dataset moves from "left" to published.

Shared, written once:
- Create `scripts/check_lake_shape.py`: the pre-publish shape check (Task 1). Its test `scripts/test_check_lake_shape.py`, and a CI step in `.github/workflows/descriptors.yml` that runs the test.

Table per dataset:

| `<d>` | table | readable copy | from |
|---|---|---|---|
| ndc | `fda.ndc` | `fda/ndc.parquet` | `build/ndc.parquet` |
| hcpcs | `cms.hcpcs` | `cms/hcpcs.parquet` | `build/hcpcs.parquet` |
| hts | `usitc.hts` | `usitc/hts.parquet` | `build/hts.parquet` |
| schedule_b | `census.schedule_b` | `census/schedule_b.parquet` | `build/schedule_b.parquet` |

Each table entry has `descriptor: datapackage.json`. Confirm `from` against the Protocol's `stamp_descriptor` `--out` (HTS stamps in place) before writing it.

---

### Task 1: The pre-publish shape check (on `ndc-lake-publish`)

**Files:** Create `scripts/check_lake_shape.py`, `scripts/test_check_lake_shape.py`; modify `.github/workflows/descriptors.yml` (one job step running the test, beside `test_check_engine_version.py`'s).

**Interfaces:**
- Produces: `python scripts/check_lake_shape.py --catalog <ducklake url or path> --table <schema.name> --parquet <path> [--max-row-change 0.10]`. Exit 0 and one line `shape ok: <table> <n> columns, rows <live> -> <new>` when the Parquet would publish into the table without a refusal and the row count is within the limit; exit 1 naming each difference otherwise. A table absent from the catalogue is exit 0 (`new table`).
- `shape_difference(existing: list[tuple[str, str]], incoming: list[tuple[str, str]]) -> list[str]`: the same rule as arcform's `schema_difference` (`src/lake/commit.rs`): a dropped column, a changed type string, a column added before the last existing one, a reorder. New columns at the end are allowed.

- [ ] **Step 1: Write the failing tests** in `scripts/test_check_lake_shape.py` (unittest, as the other `scripts/test_*.py`):
  - `test_identical_columns_pass`: `shape_difference([("code","VARCHAR")], [("code","VARCHAR")]) == []`.
  - `test_trailing_new_column_passes`: adding `("x","INTEGER")` at the end gives `[]`.
  - `test_type_change_is_named`: `VARCHAR` to `INTEGER` gives one entry containing `code` and both types.
  - `test_dropped_column_is_named`, `test_reorder_is_named`, `test_column_added_before_last_is_named`.
  - `test_cli_against_local_lake`: build a DuckLake in a temp dir with a table `s.t(code VARCHAR, n INTEGER)` of 10 rows; a Parquet of the same shape and 10 rows exits 0; one with `n` as `BIGINT` exits 1 naming `n`; one of 20 rows exits 1 naming the row change.
- [ ] **Step 2:** `python scripts/test_check_lake_shape.py` fails (module missing).
- [ ] **Step 3:** Implement. Attach the catalogue read-only with `ducklake`; read columns in order from `duckdb_columns()`; read the Parquet's with `DESCRIBE SELECT * FROM read_parquet(?)`. Type strings compare as DuckDB prints them.
- [ ] **Step 4:** `python scripts/test_check_lake_shape.py` passes (duckdb 2.0.0).
- [ ] **Step 5:** Commit: "Check a rebuilt table's shape against the open lake before publishing".

### Task 2: NDC moves onto the publish step (on `ndc-lake-publish`)

**Files:** per-dataset files above for `ndc`.

**Interfaces:** Consumes Task 1's CLI.

- [ ] **Step 1: Edit `arcform.yaml`.** `engine_version: ">=2.0"`, `dotenv: [.env]` placed as in soc_2018, both `duckdb==1.5.5` to `duckdb==2.0.0`, the `publish` step after `stamp_descriptor` copied from soc_2018 with the one NDC table entry. Rewrite the header's BOUNDARY paragraph and replace the trailing `# publish:` comment, so nothing says publishing happens outside the Protocol. Add `.env.example`.
- [ ] **Step 2: Force-rebuild locally** with the Global Constraints command. Expected: green; the publish step says it published locally and names the three missing variables.
- [ ] **Step 3: Check the local lake.** `duckdb` attaches `datasets/ndc/build/lake/open.ducklake`: `fda.ndc` row count equals the run's, `meta.descriptors` has one row for it. Footer: `SELECT created_by FROM parquet_file_metadata('build/ndc.parquet')` names v2.0.0.
- [ ] **Step 4: Check the shape against the live lake.** `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table fda.ndc --parquet datasets/ndc/build/ndc.parquet`. Expected: `shape ok`. Anything else: stop and bring it to Hugh.
- [ ] **Step 5: Run the touched checks.** `python scripts/check_engine_version.py` (with `DUCKDB_VERSION=2.0.0`), `python scripts/test_ndc_models.py`, `python scripts/check_protocol_readme.py --write` then `python scripts/test_check_protocol_readme.py`, `python scripts/check_descriptors.py`, `python scripts/test_manifest_one_writer.py`. All pass.
- [ ] **Step 6: Commit** the manifest, `.env.example`, restamped `datapackage.json`, README step list: "NDC publishes into the open lake by arc run". Wait for roborev's review of the commit and fold its findings into the fix round.
- [ ] **Step 7: Go-ahead.** Flag the issue needs-human, ask Hugh with the run's row count, the live count and the shape line. Do not continue without it.
- [ ] **Step 8: Publish live** with the Global Constraints command. Expected: one snapshot committed, with inserted, updated and deleted counts.
- [ ] **Step 9: Read back over https** with DuckDB 2.0 and no credential: `fda.ndc` count equals the run's; `meta.descriptors` holds `fda.ndc`; `https://openlake.meridian.online/fda/ndc.parquet` reads with the same count; `manifest.json`'s `fda/ndc` entry names it and the new run; every other table and the eight views still read. Run the publish once more: it reports nothing changed and the snapshot count is unchanged.
- [ ] **Step 10: Docs.** README paragraph on the publish step and readable copy; `docs/code-lists.md` where it says NDC publishes through infra or that its refresh runs through `publish-all.sh`; `docs/publishing.md`: NDC in the published list with date and snapshot id, removed from "What is left". Commit: "NDC is live in the open lake".
- [ ] **Step 11: Ready.** Fold, then delete the spec and plan on this branch (they stay on the pushed `code-lists-lake-publish-plan` branch for the later tasks). Push, open the pull request, `roborev review --branch --wait`, fix Medium and above, close it, `gh pr checks <pr> --watch`, Forge sync and link. Report ready.

### Task 3: HCPCS (branch `hcpcs-lake-publish` from `ndc-lake-publish`)

Task 2's steps for `hcpcs` (`cms.hcpcs`, `cms/hcpcs.parquet`, `scripts/test_hcpcs_models.py`), except Step 11 has no spec or plan to delete. Its pull request's base is `ndc-lake-publish` until that merges.

### Task 4: HTS (branch `hts-lake-publish` from `hcpcs-lake-publish`)

Task 2's steps for `hts` (`usitc.hts`, `usitc/hts.parquet`, `scripts/test_hts_models.py`). HTS keeps its layout: `stamp_descriptor` rewrites `build/hts.parquet` and `datapackage.json` in place; the publish step reads those. Three `duckdb` pins.

### Task 5: Schedule B (branch `schedule-b-lake-publish` from `hts-lake-publish`)

Task 2's steps for `schedule_b` (`census.schedule_b`, `census/schedule_b.parquet`, `scripts/test_schedule_b_models.py`). Its three pins include `derive_join_coverage`'s. After the rebuild, check that `datapackage.json`'s `naics` join keeps `measuredAgainst: sha256:8c091c158138215269dd607e72872fa221431636ab82c101c39eff7eb537d4d5` and that `python datasets/edgar_gleif/scripts/crosswalk_join.py` (check mode, as CI runs it) passes. Its `docs/publishing.md` update also says the four hosted code lists are done and what remains is naics, edgar, gleif and edgar_gleif.

## Verification cost

- `scripts/test_check_lake_shape.py`: seconds, offline.
- Each forced rebuild: one download (NDC's zip is the largest, about 15 MB) and under a few minutes.
- The shape check and read-backs: a few small https reads of the catalogue.
- Each live write: one snapshot into `meridian-open`; irreversible in the sense that readers see it, recoverable by time travel.
