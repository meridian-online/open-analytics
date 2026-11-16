# NAICS lake publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `datasets/naics` ends in arc's `ducklake_write@1` publish step, in two pull requests: the step (green now), then the DuckDB 2.0 pins and rebuilt descriptor (published from its branch after the first merges).

**Architecture:** Copy HTS's move (`git show 0212b4d -- datasets/hts`), split so the first pull request changes nothing CI compares with the served object.

**Tech Stack:** arc (`~/.arc-main/bin/arc`, DuckDB 2.0.0), uv, finetype 0.6.67 extension at `/Volumes/Dev/meridian-online/finetype-v0.6.67/target/release/finetype.duckdb_extension`.

**Spec:** `docs/superpowers/specs/2026-11-16-naics-lake-publish-design.md`

## Global Constraints

- Never set R2 variables or create `datasets/naics/.env`; this session makes no live write. Run arc as `env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY -u R2_ACCOUNT_ID FINETYPE_DUCKDB_EXT=<ext> ~/.arc-main/bin/arc run`.
- Table `census.naics_2022`, readable copy `census/naics_2022.parquet`, primary key `code`, 2,125 rows.
- Public repo: no Kata refs, private repo names or local paths in commits, PR text or tracked files.
- Columns unchanged; no edit to `models/`, `descriptor.overrides.json`, nominations or schema.

## Review Focus

- A publish from `main` between the two merges: the manifest comment and README must say not to (Task 1).
- The descriptor in the second pull request going stale before the publish: its description says to re-commit from the publishing run (Task 2).
- Schedule B's join after NAICS republishes: reported due a rebuild, not failed; documented, not changed (Task 3).

---

### Task 1: The publish step (first pull request, branch `naics-lake-publish`)

**Files:** Modify `datasets/naics/arcform.yaml`, `datasets/naics/README.md`; Create `datasets/naics/.env.example`.

- [ ] Add `dotenv: [.env]`, replace the BOUNDARY header note with HTS's PUBLISH note, replace the trailing publish comment with a `publish` step identical to HTS's except `table: census.naics_2022`, `from: build/naics.parquet`, `readable_copy: census/naics_2022.parquet`. Leave `engine_version: ">=1.2"` and `duckdb==1.5.5`, with a comment that they move with the descriptor in the next change and NAICS is not published from `main` until then.
- [ ] Copy `datasets/hts/.env.example`.
- [ ] README: replace "Boundary (deliberate)" with a Publish section adapted from HTS's (shape check `--table census.naics_2022 --parquet datasets/naics/build/naics.parquet`, plus the not-from-`main` warning), add a step 4 line; run `python scripts/check_protocol_readme.py --write`.
- [ ] Verify: the arc command above exits 0 with the step reporting a local publish; `duckdb` attaching `ducklake:datasets/naics/build/lake/open.ducklake` reads 2,125 rows from `census.naics_2022` and a `meta.descriptors` row for it. `git diff --stat` shows `datapackage.json` reverted (`git checkout` it if the run moved it). `python scripts/check_protocol_readme.py` exits 0.
- [ ] Commit.

### Task 2: Pins and descriptor (second pull request, branch `naics-lake-publish-descriptor` from Task 1)

**Files:** Modify `datasets/naics/arcform.yaml`, `datasets/naics/datapackage.json`.

- [ ] `engine_version: ">=2.0"` with HTS's comment; `duckdb==2.0.0` in `stamp_descriptor`; drop Task 1's "pins move next" comment.
- [ ] Run the arc command with `--force`; expect the descriptor diff to be `created`, `bytes` 349873 and the new `hash` only.
- [ ] Verify locally: `uv run -q --with duckdb==2.0.0 python3 scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table census.naics_2022 --parquet datasets/naics/build/naics.parquet` prints `shape ok`. `check_engine_version.py --only naics` and `check_descriptors.py` are expected to fail on NAICS only, naming the served object.
- [ ] Commit; open as a draft whose description gives the publish steps (spec, "Second pull request").

### Task 3: Docs and fold

**Files:** Modify `docs/publishing.md`; delete the spec and plan.

- [ ] `docs/publishing.md`: a NAICS section (what the rebuild showed, the two-pull-request order and why, Schedule B's join going due a rebuild), "What is left" naming edgar, gleif and edgar_gleif, and the rulings. On Task 1's branch, then rebase Task 2's branch on it.
- [ ] Delete `docs/superpowers/` on Task 1's branch.
- [ ] Gate: the descriptors workflow's offline scripts for the touched files (`scripts/test_*.py` that read arcform or READMEs), then PR checks.
