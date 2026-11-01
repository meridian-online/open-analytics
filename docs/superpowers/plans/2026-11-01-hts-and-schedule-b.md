# HTS and Schedule B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host USITC's HTS at `usitc/hts` and Census's Schedule B at `census/schedule_b` as full-ladder code lists, published from this branch, with a code-list contract the next lists reuse.

**Architecture:** Two arcform Protocols (`datasets/hts/`, `datasets/schedule_b/`) fetch the issuers' files and build each ladder in DuckDB SQL. One SQL file owned by the HTS Protocol (`datasets/hts/models/outline.sql`) reads USITC's outline for both. `scripts/check_code_list.py` holds every code list to the shared core columns, as a Protocol step and in CI. infra's publish pipeline gains the two addresses in its own pull request, and both lists are published before either pull request merges.

**Tech Stack:** arc 0.2.1 (release binary) with the DuckDB 1.5.5 CLI; Python 3 with `duckdb==1.5.5`; bash for infra.

**Spec:** `docs/superpowers/specs/2026-11-01-hts-and-schedule-b-design.md`

## Global Constraints

- Build every Protocol with **arc 0.2.1** and **DuckDB CLI 1.5.5**: `ARC_DUCKDB_BIN=<path to the DuckDB 1.5.5 CLI>`, arc from `gh release download v0.2.1 -R meridian-online/arcform -p 'arc-v0.2.1-aarch64-apple-darwin.tar.gz*'` into the session scratchpad, sha256 checked. arc 0.2.2 refuses DuckDB 1.x, and CI (`DUCKDB_VERSION: "1.5.5"`) and the publish pin (`PUBLISH_DUCKDB_PIN` 1.5.5) hold the Parquet writer to 1.5.5. Do not overwrite `~/.cargo/bin/arc`.
- Public repository: no Kata refs, no private repo names, no local paths in tracked files, commit messages, branch names or pull request text.
- `User-Agent: "Meridian Protocol (open-analytics; research@meridian.online)"` on every fetch.
- USITC URLs: `https://hts.usitc.gov/reststop/exportList?from=0100&to=9999&format=JSON&styles=false` and `https://hts.usitc.gov/reststop/currentRelease`. Census URLs: `https://www.census.gov/foreign-trade/schedules/b/{year}/sbsdf.zip` and `.../b/{year}/index.html`, `year` a Protocol param defaulting to `2026`.
- Core columns, in order: `code, code_display, level, depth, parent_code, description, description_full, release, text_source, corpus`. `description_full` joins with ` › ` (space, U+203A, space).
- HTS levels: `chapter, heading, subheading, tariff_line, statistical_line`. Schedule B levels: `chapter, heading, subheading, commodity`.
- `text_source` values: HTS `usitc | usitc_derived | meridian_label | usitc_toc`; Schedule B `census | usitc | census_derived | meridian_label`.
- Licence `other-pd` on both, as `datasets/naics/descriptor.overrides.json` has it.
- Quick checks before each commit: the self-test of every script the task touches, plus `python3 scripts/check_protocol_readme.py` once a Protocol exists. CI is the full gate.
- One cargo or arc build at a time on this machine.

## Review Focus

1. **USITC revision lands mid-run** (the two `currentRelease` reads differ): the run must fail before export, naming both revisions. Test in Task 2.
2. **A ten-digit HTS line with no printed rate under an eight-digit line that has one**: it carries the eight-digit line's rates and `rate_from_code` names that line, not NULL rates. Test in Task 2.
3. **An `indent` of `"03"`**, and an unnumbered heading directly under a chapter with no numbered heading above it: parents and `description_full` still resolve. Test in Task 2.
4. **January rollover**: Schedule B year 2027 against `2026HTSRev22` fails the run with a message naming both. Test in Task 4.
5. **Chapter 98 in Schedule B**: headings `9801`–`9803` take Census text (`census_derived`), never USITC's import-provision text. Test in Task 4.

---

### Task 1: The code-list contract check

**Files:**
- Create: `scripts/check_code_list.py`
- Create: `scripts/test_check_code_list.py`
- Modify: `.github/workflows/descriptors.yml` (a self-test job and a check job, beside the label-agreement jobs)

**Interfaces:**
- Produces: `python3 scripts/check_code_list.py --parquet PATH --levels L1,L2,... [--json OUT]` checks one built file; exit 0 pass, 1 contract broken (each finding printed `code-list: <rule>: <detail>`), 2 cannot run. With no `--parquet`, it reads every `datasets/*/datapackage.json` carrying `"x-meridian-code-list": {"levels": [...]}` at package level and checks the file at `resources[0].path`.
- Produces: `CORE_COLUMNS: tuple[str, ...]` (the ten names in order) and `check(con, relation: str, levels: list[str]) -> list[str]` returning finding strings, importable by tests.

- [ ] **Step 1: Write the failing tests** in `scripts/test_check_code_list.py`, `unittest`, building each fixture as a Parquet in a temp dir with `duckdb`. One passing case (a three-level ladder) and one case per rule, each asserting the rule name appears in the output and exit is 1: `core-columns` (a column missing; two swapped), `code-unique` (repeated), `code-not-null`, `parent-present` (parent not in file), `parent-null-below-top`, `depth-step` (child depth not parent+1), `level-depth` (a row's depth is not its level's position in `--levels`), `level-set` (level outside `--levels`), `release-not-null`, `release-single` (two distinct), `description-not-null`. Plus: no descriptor carries the key → exit 2 with "no code-list descriptors", and an unreadable path → exit 2.
- [ ] **Step 2: Run** `python3 scripts/test_check_code_list.py` — Expected: FAIL, module not found.
- [ ] **Step 3: Implement `check()` and the CLI** in `scripts/check_code_list.py`. Each rule is one SQL query over the relation. Standard library plus `duckdb` only, with a PEP 723 header pinning `duckdb==1.5.5` so it runs under `op: uv@1`.
- [ ] **Step 4: Run** `python3 scripts/test_check_code_list.py` — Expected: all pass.
- [ ] **Step 5: Add the CI jobs**: `test_check_code_list.py` on every push, and `check_code_list.py` (descriptor mode) after the descriptor check, installing `duckdb==${DUCKDB_VERSION}` the way the neighbouring jobs do.
- [ ] **Step 6: Commit** `scripts/check_code_list.py scripts/test_check_code_list.py .github/workflows/descriptors.yml` — "A code list is held to the shared core columns".

### Task 2: The HTS outline and ladder in SQL

**Files:**
- Create: `datasets/hts/models/outline.sql`, `datasets/hts/models/load.sql`, `datasets/hts/models/package.sql`
- Create: `datasets/hts/chapters.csv` (columns `chapter,chapter_title,section,section_title`; 99 rows)
- Create: `scripts/testdata/hts/` (a reduced `hts.json`, `release_before.json`, `release_after.json`)
- Create: `scripts/test_hts_models.py`

**Interfaces:**
- Produces: `outline.sql` reads `build/hts.json` and creates `hts_outline(seq BIGINT, code VARCHAR, code_display VARCHAR, indent INTEGER, description VARCHAR, context VARCHAR[])` and `hts_levels(prefix VARCHAR, def_seq BIGINT, def_code VARCHAR, text VARCHAR, text_source VARCHAR)`. `text` follows the spec's node rule; `text_source` is `usitc`, `usitc_derived` or `meridian_label`, and Schedule B treats `meridian_label` as not named by USITC. Schedule B (Task 4) runs this file by `../hts/models/outline.sql` and reads only `hts_levels` and `hts_outline`.
- Produces: `load.sql` creates `hts` with the core columns, then `indent INTEGER, section VARCHAR, section_title VARCHAR, units VARCHAR[], general_rate VARCHAR, special_rate VARCHAR, column2_rate VARCHAR, rate_from_code VARCHAR, footnotes VARCHAR[], quota_quantity VARCHAR, additional_duties VARCHAR`. It reads `build/release_before.json`, `build/release_after.json` and `chapters.csv`, and calls `error('USITC revision changed during the run: <before> then <after>')` when they differ, and `error('chapter <cc> has no title in chapters.csv')` for an untitled chapter. `package.sql` adds `corpus` as `lower(strip_accents(description_full))` and makes `hts_out`.

- [ ] **Step 1: Make `chapters.csv`** from USITC's table of contents: the JSON literal assigned in `https://hts.usitc.gov/main.*.js` (the `section_type":"chapter"` entries; each chapter's `label` is its title, the section's `title`/`label` its numeral and title). The CSV carries no comment lines; the source URL and the date copied go in `datasets/hts/README.md` (Task 3).
- [ ] **Step 2: Make the fixture** by cutting `hts.json` down to these USITC rows, in file order: chapter 01 headings `0101` (with unnumbered "Horses:" and its children through `0101.21.00.20`), `0104` (`0104.10.00.00`), `0105` through `0105.11.00.40` (the "Weighing not more than 185 g:" and "Breeding stock…" chains), `0409.00.00` and its ten-digit children (single-line heading; ten-digit lines with no printed rate), one row with `indent` `"03"`, and two chapter 99 rows. Release files: both `{"name":"2026HTSRev22",…}`.
- [ ] **Step 3: Write the failing tests** in `scripts/test_hts_models.py`, which copies the fixture into a temp `build/`, runs `outline.sql`, `load.sql`, `package.sql` with the DuckDB 1.5.5 CLI (`ARC_DUCKDB_BIN` or `duckdb` on PATH; skip with a message naming it if absent), and asserts:
  - `0409` exists with `level='heading'`, `text_source='usitc_derived'`, `description='Natural honey'`, `parent_code='04'`;
  - `010511` exists with `description='Weighing not more than 185 g: Chickens'`;
  - `0101210010` has `parent_code='01012100'`, `description_full` starting with chapter 01's title from `chapters.csv` followed by ` › ` and then heading 0101's text, and containing `Horses:` exactly once;
  - no USITC row contributes twice to one row's `description_full` (checked by row identity: the outline rows feeding each `description_full` have distinct `seq`s); two rows with the same text both appear (`0409000035` → `<ch04> › Natural honey › Other: › Other: › White or lighter`);
  - a ten-digit line whose eight-digit prefix USITC does not print hangs from a made `tariff_line` row with `text_source='usitc_derived'`;
  - an unnumbered row directly under a chapter, before that chapter's first numbered heading (add one to the fixture), leaves its numbered child's `parent_code` as the chapter and puts the unnumbered text in the child's `description_full`;
  - a ten-digit `0409000010`-style line that prints no rate has `general_rate` equal to its eight-digit line's and `rate_from_code='04090000'`; a line that prints its own has `rate_from_code=code`;
  - `chapter` rows have `text_source='usitc_toc'` and `depth=0`, and every row has `section` set;
  - the `"03"` row has `indent=3`;
  - with `release_after.json` set to `2026HTSRev23`, the run fails with stderr containing `2026HTSRev22 then 2026HTSRev23`;
  - `scripts/check_code_list.py --parquet <exported hts_out> --levels chapter,heading,subheading,tariff_line,statistical_line` exits 0.
- [ ] **Step 4: Run** `python3 scripts/test_hts_models.py` — Expected: FAIL (models missing).
- [ ] **Step 5: Write the three SQL files.** `code` is `htsno` with dots removed; `code_display` is `htsno` for numbered rows, and for made levels the dotted form of the prefix (`0409`, `0105.11`). The ladder is the union of every numbered code and every 2/4/6/8-digit prefix of one, de-duplicated. A row's `parent_code` is its longest proper prefix of length 2, 4, 6 or 8 that is in the ladder. Rates inherit from the nearest ancestor in the ladder that prints a non-empty `general`, walking up through the ancestors.
- [ ] **Step 6: Run** `python3 scripts/test_hts_models.py` — Expected: all pass.
- [ ] **Step 7: Commit** — "The HTS ladder is built in SQL from USITC's outline".

### Task 3: The HTS Protocol, its descriptor and a cold run

**Files:**
- Create: `datasets/hts/arcform.yaml`, `datasets/hts/README.md`, `datasets/hts/.gitignore` (`build/`), `datasets/hts/descriptor.overrides.json`, `datasets/hts/nominations.finetype.json`, `datasets/hts/schema.finetype.json`, `datasets/hts/datapackage.json` (emitted by the run)

**Interfaces:**
- Consumes: Task 2's models; Task 1's `check_code_list.py`.
- Produces: `datasets/hts/build/hts.parquet` and `datasets/hts/datapackage.json` with `"x-meridian-code-list": {"levels": ["chapter","heading","subheading","tariff_line","statistical_line"]}`, resource `path` `https://openlake.meridian.online/usitc/hts.parquet`, package and resource `name` `hts`, `primaryKey` `["code"]`.

- [ ] **Step 1: Write `arcform.yaml`** with the steps the spec lists, in order: `fetch_release_before`, `fetch_hts`, `fetch_release_after` (each `http_fetch@1`, `fetch_release_after` with `depends_on: [build/hts.json]`), `outline`, `load`, `package`, `export_hts` (`parquet_export@1`, `order_by: "code"`, zstd), `describe`, `check_code_list` (`op: uv@1`, `script: ../../scripts/check_code_list.py`, `sha256:` its digest, `deps: [duckdb==1.5.5]`), `validate`, `stamp_descriptor` (the `command:` form NAICS uses). Header comment in NAICS's style, naming the publish boundary and `usitc/hts`.
- [ ] **Step 2: Write `descriptor.overrides.json`**: title "HTS — Harmonized Tariff Schedule of the United States"; licence `other-pd` titled "Public domain (U.S. International Trade Commission)" at `https://www.usitc.gov/tariff_affairs/about_hts.htm`; sources `https://hts.usitc.gov/` and `https://hts.usitc.gov/download`; contributors USITC (author) and Meridian (publisher, wrangler); a description per field. Each rate field's description says the rate is as USITC prints it, that ten-digit lines take the rate of the line named in `rate_from_code`, and that chapter 99 provisions reached through `footnotes` can add duties. `release`'s says it is USITC's revision name. `x-meridian-code-list` as above.
- [ ] **Step 3: Run the Protocol cold**: `rm -rf build && ARC_DUCKDB_BIN=… <arc 0.2.1> run` in `datasets/hts`. Expected: every step succeeds. Record the row count and the count per level.
- [ ] **Step 4: Nominate and contract.** If `code` is not inferred as `geography.transportation.hs_code`, check that label's pattern against digits-only codes of lengths 2–10 with `finetype`. If it admits them, nominate it with the reason; if not, leave inference and note the mismatch in the README for a finetype issue. Write `schema.finetype.json` self-derived as NAICS's is (closed `enum` on `level`, `text_source`). Re-run until `describe` and `validate` pass.
- [ ] **Step 5: Write `README.md`** in NAICS's shape: the three facets, the step DAG in prose, the chapter file's source and copy date, the revision bracket, and the generated step block (`python3 scripts/check_protocol_readme.py --write`).
- [ ] **Step 6: Run** `python3 scripts/check_protocol_readme.py` and `python3 scripts/check_code_list.py --parquet datasets/hts/build/hts.parquet --levels chapter,heading,subheading,tariff_line,statistical_line` — Expected: both exit 0.
- [ ] **Step 7: Commit** the Protocol files and the emitted descriptor — "usitc/hts: the Protocol and its descriptor".

### Task 4: The Schedule B ladder in SQL

**Files:**
- Create: `datasets/schedule_b/models/load.sql`, `datasets/schedule_b/models/package.sql`
- Create: `scripts/testdata/schedule_b/` (a reduced `econcord.txt` in CRLF fixed width, a reduced `index.html`, and the HTS fixture's three files by reference to `scripts/testdata/hts/`)
- Create: `scripts/test_schedule_b_models.py`

**Interfaces:**
- Consumes: `../hts/models/outline.sql` → `hts_levels`, `hts_outline`.
- Produces: `load.sql` reads `build/sbsdf/econcord.txt`, `build/schedule_b_index.html`, `build/release_before.json`, `build/release_after.json`, the year from `getvariable('year')` or a one-row `params` table (whichever arc 0.2.1 offers for `params:`), and the `.arcmeta` of the zip fetch if the member date is not available. It creates `schedule_b` with the core columns, then `description_short, unit_1, unit_2, sitc, end_use, agricultural, naics, hitech, section, section_title, hts_release`. It errors with `'HS edition mismatch: Schedule B <year>, USITC <revision>'` when the revision's year differs from `year`, and with `'USITC names <n> of <m> <headings|subheadings> outside chapter 98; 99% required'` when either level, counted on its own, is below 99%.

- [ ] **Step 1: Make the fixture**: ten-digit lines under chapters 01 (`0101210000`, `0101290000`, matching the HTS fixture's prefixes), 04 (`0409000000`), 88 (`8800000000`, any line), 98 (`9801100000`, `9802100000`); an `index.html` holding the chapter and section anchor text for 01, 04, 88, 98 in Census's form (`Chapter 1 : Live Animals`, `Section 1 - Live Animals; Animal Products`).
- [ ] **Step 2: Write the failing tests** in `scripts/test_schedule_b_models.py`, run the way Task 2's are, asserting:
  - `0101` has `text_source='usitc'` and USITC's heading text; `010121` exists with `parent_code='0101'`;
  - `9801` has `text_source='census_derived'` and the text of `9801100000`'s line, not USITC's;
  - `8800` and `880000` are `census_derived`;
  - chapter `01` is `Live Animals`, `text_source='census'`, with `section='I'` (the Roman numeral, also for Census's `Section 1`);
  - `0101210000` has `level='commodity'`, `parent_code='010121'`, `description_short` and `description` both set, and `hts_release='2026HTSRev22'`;
  - `release` matches `^2026/\d{4}-\d{2}-\d{2}$`;
  - with `year=2027` the run fails with `HS edition mismatch: Schedule B 2027, USITC 2026HTSRev22`;
  - with the fixture's HTS heading rows removed but subheadings kept, the run fails with the headings message; with subheading rows removed, with the subheadings message;
  - `check_code_list.py --levels chapter,heading,subheading,commodity` exits 0 on the export.
- [ ] **Step 3: Run** `python3 scripts/test_schedule_b_models.py` — Expected: FAIL.
- [ ] **Step 4: Write `load.sql` and `package.sql`.** Fixed-width read: one VARCHAR column per line (`read_csv(..., columns={'line':'VARCHAR'}, header=false, delim=E'\x01', quote='')`), CR stripped, fields by the layout in the spec; empty strings to NULL. Chapter and section titles: `read_text` of the index, `regexp_extract_all` over the tag-stripped text, for `Chapter (\d{1,2}) : ` and `Section (\d+|[IVX]+) - `, each title ending at the next `Chapter`/`Section` marker. `agricultural` is a boolean only if the file's distinct values are exactly two, otherwise VARCHAR as printed.
- [ ] **Step 5: Run** `python3 scripts/test_schedule_b_models.py` — Expected: all pass.
- [ ] **Step 6: Commit** — "The Schedule B ladder takes its upper levels from USITC's outline".

### Task 5: The Schedule B Protocol, its descriptor and a cold run

**Files:**
- Create: `datasets/schedule_b/arcform.yaml`, `README.md`, `.gitignore`, `descriptor.overrides.json`, `nominations.finetype.json`, `schema.finetype.json`, `datapackage.json` (emitted)

**Interfaces:**
- Consumes: Task 4's models; Task 1's check; `census/naics_2022`'s descriptor for the NAICS join.
- Produces: `datasets/schedule_b/build/schedule_b.parquet`; descriptor with resource `path` `https://openlake.meridian.online/census/schedule_b.parquet`, `name` `schedule_b`, `primaryKey` `["code"]`, `x-meridian-code-list` levels `chapter,heading,subheading,commodity`, and an `x-joins.joins[]` entry from `naics` to `census/naics_2022`'s `code` in `datasets/edgar_gleif`'s form (`cardinality`, `coverage`, `evidence`, `measuredAgainst` set to the NAICS object's hash, `package` its descriptor URL); no `schema.foreignKeys` entry.

- [ ] **Step 1: Write `arcform.yaml`**: `params: year: "2026"`; `fetch_schedule_b` + `archive_extract@1` to `build/sbsdf/`; `fetch_index`; the three USITC fetches as in Task 3; `outline` (`sql: ../hts/models/outline.sql`); `load`; `package`; `export_schedule_b` (`order_by: "code"`); `describe`; `derive_join_coverage` (`op: uv@1` running `../edgar_gleif/scripts/crosswalk_join.py --write`, sha256-pinned, on the staged descriptor, as the crosswalk Protocol does, with `--local schedule_b=<the staged Parquet>` so the left side is read from the build, not openlake; the script refuses a slug with no `datapackage.json` yet, so commit a minimal seed `datasets/schedule_b/datapackage.json` before the first cold run, which the run then overwrites); `check_code_list`; `validate`; `stamp_descriptor`.
- [ ] **Step 2: Write `descriptor.overrides.json`**: title "Schedule B — U.S. Export Classification Codes"; licence `other-pd` "Public domain (U.S. Census Bureau)" at `https://www.census.gov/about/policies/open-gov/open-data.html`; sources Census's Schedule B page, the year's index, and USITC's export as the source of heading and subheading text; contributors Census (author), USITC (contributor, for the heading text), Meridian. Field descriptions: `text_source` names its three values; `hts_release` says which USITC revision supplied heading text; `naics` notes the join to `census/naics_2022`. Description says Census's bulk file carries no endorsement notice and that the data is credited to the Census Bureau.
- [ ] **Step 3: Measure the NAICS join** at build through `derive_join_coverage`: declare the join with `where: {"field": "level", "equals": "commodity"}`, so coverage is commodity rows and how many of them match a `code` in the published `census/naics_2022.parquet`; a commodity line with a blank `naics` counts as unmatched. That is how `crosswalk_join.py` counts, so CI's re-measurement agrees. Write `coverage` into the generated `datapackage.json` from the build (not by hand in the overrides), as `crosswalk_join.py --write` does for the crosswalk.
- [ ] **Step 4: Run cold**, nominate and write the contract, and write `README.md`, as Task 3 Steps 3–5.
- [ ] **Step 5: Run** `python3 scripts/check_protocol_readme.py` and `check_code_list.py` on the build — Expected: exit 0.
- [ ] **Step 6: Commit** — "census/schedule_b: the Protocol and its descriptor".

### Task 6: Registry, namespace, catalogue and the cross-package checks

**Files:**
- Modify: `datasets/registry.json`, `README.md` (Datasets table, address list), `datasets/label-agreement.json`, `datasets/frictionless-rejections.json` (only if a refusal appears)
- Test: `scripts/test_registry.py` (shipped-registry case)

- [ ] **Step 1:** Add namespace `usitc` (`kind: held`, `issuer: "U.S. International Trade Commission"`, `claimed: false`) to `namespaces`, `schemas` ("Datasets issued by the U.S. International Trade Commission.") and `reserved`. Add entries `usitc`/`hts` and `census`/`schedule_b`, each with a new `uuid4` `uid`, `aliases: []`, `manifest` `https://openlake.meridian.online/manifest.json`, `doi: null` and `protocol` pointing at its `arcform.yaml`.
- [ ] **Step 2: Run** `python3 scripts/test_registry.py` — Expected: OK.
- [ ] **Step 3:** Add the two README rows (rows written from the built counts; Task 9 rewrites them with `--write-catalogue`), and the two addresses to the address paragraph.
- [ ] **Step 4: Run** `python3 scripts/check_label_agreement.py --strict`. For each difference the new packages introduce (the shared names `code`, `level`, `description` against NAICS are expected), add a `label-agreement.json` reason pinned to the values when the populations differ, or fix the descriptor when it is a defect. Then `python3 scripts/check_frictionless.py` and `python3 scripts/check_constraint_ratchet.py --base origin/main`. Expected: all exit 0.
- [ ] **Step 5: Commit** — "Registry entries and the held namespace usitc".

### Task 7: The code-list guide

**Files:**
- Create: `docs/code-lists.md`
- Modify: `README.md` (one paragraph pointing to it)

- [ ] **Step 1: Write `docs/code-lists.md`**: what a hosted code list is (Hugh's ruling, in its own words); the core columns table; the check and how a descriptor opts in (`x-meridian-code-list`); a Protocol's layout (fetch with the release recorded, a `load.sql` of its own, `package`, export, describe, `check_code_list`, validate, stamp); how a release is recorded (HTS revision bracket; Schedule B year and file date); when SQL is shared between lists (only for a shared source quirk; the HTS outline as the one case, and why `nest-by-*` operations and a macro library were not built); the edition guard and the January rollover; recopying `chapters.csv` at an HS edition.
- [ ] **Step 2:** Every claim in it that is a number is one measured in Tasks 3 and 5, with its date.
- [ ] **Step 3: Commit** — "How the next code list reuses the contract".

### Task 8: infra publishes the two addresses (infra repository, its own branch and pull request)

**Files (in a checkout of meridian-online/infra, branch `hts-and-schedule-b`):**
- Modify: `publish/publish-all.sh` (`_script_for` and `PROTOCOL_SLUGS` gain `usitc/hts` and `census/schedule_b` on `publish-protocol-artifact.sh`)
- Modify: `publish/publish-catalog.sh` (`DATASETS` admits an address with no old name, written `usitc/hts:` with an empty right side; `NTABLES` counts one table for such an entry and three for the others)
- Modify: `publish/README.md` (address table rows), `publish/test/*.test.sh` that enumerate addresses

- [ ] **Step 1: Write failing tests** in `publish/test/catalog-names.test.sh`: an entry with no old name builds the namespace table and no compatibility view, and the expected count is 4×3 + 2 = 14. In `declares-what-it-uploaded.test.sh`: `--plan` maps both new addresses to `publish-protocol-artifact.sh` and resolves their artifacts through the registry (that test reads the open-analytics branch of the same name).
- [ ] **Step 2: Run** the two tests — Expected: FAIL.
- [ ] **Step 3: Implement** the mapping and the catalogue change.
- [ ] **Step 4: Run** every `publish/test/*.test.sh` — Expected: PASS.
- [ ] **Step 5: Dry run**: `PUBLISH_R2_MODE=dry bash publish/publish-all.sh --slugs "usitc/hts census/schedule_b"` against the open-analytics branch — Expected: both planned, artifacts found, no PUT outside `usitc/` and `census/schedule_b`.
- [ ] **Step 6: Commit, push, open the infra pull request.**

### Task 9: Publish from the branch and restamp

- [ ] **Step 1:** Back up the live manifest to the scratchpad: `curl -fsS https://openlake.meridian.online/manifest.json`.
- [ ] **Step 2:** Confirm `npx -y wrangler@latest whoami` shows the account and `ACCOUNT_ID` is available. If either is not, flag the Kata issue `needs-human` naming the missing credential, and stop this task only.
- [ ] **Step 3:** Rebuild both Protocols cold (Tasks 3 and 5), then `bash publish/publish-all.sh --publish --slugs "usitc/hts census/schedule_b"` from the infra branch with the pinned DuckDB first on `PATH`. Expected: both current releases, snapshots, run records, manifest keys and the rebuilt catalogue, with every smoke passing.
- [ ] **Step 4:** `python3 scripts/publish_dataset.py restamp` for the two, `python3 scripts/check_descriptors.py --write-catalogue`, then `python3 scripts/check_descriptors.py`, `python3 scripts/check_code_list.py`, `python3 scripts/check_engine_version.py` — Expected: exit 0.
- [ ] **Step 5:** Read each through the catalogue: `ATTACH 'ducklake:https://openlake.meridian.online/catalog/open.ducklake' AS meridian (READ_ONLY); SELECT level, count(*) FROM meridian.usitc.hts GROUP BY 1;` and the same for `meridian.census.schedule_b` — Expected: counts equal the build's.
- [ ] **Step 6: Commit** the restamped descriptors and README — "Both code lists are published at their addresses".

### Task 10: Fold, review and hand over

- [ ] **Step 1:** Fold the spec into `docs/code-lists.md` (the rulings with their reasons: the best-asset rule, rates verbatim, approach C over macros and operations, publish before merge) and delete `docs/superpowers/specs/2026-11-01-hts-and-schedule-b-design.md` and this plan.
- [ ] **Step 2:** `roborev review --branch --wait` in open-analytics and in infra; fix or answer each finding at Medium and above; close each review.
- [ ] **Step 3:** Open the open-analytics pull request; `gh pr checks <pr> --watch` on both.
- [ ] **Step 4:** Link both pull requests to the Kata issue in Forge (sync, then `kata-links`), and check each reply lists the issue.
- [ ] **Step 5:** Put the CI test totals and the rulings made during the build in each description, and report both as ready, with links.

## Dependencies

1 → 2 → 3 → 4 → 5 → 6 → 9 → 10; 7 after 5; 8 after 6 (it reads the registry on the branch); 9 needs 8.

## Verification costs

- Each self-test (`test_check_code_list.py`, `test_hts_models.py`, `test_schedule_b_models.py`, `test_registry.py`): seconds, no network.
- A cold Protocol run: one 12.6 MB USITC fetch and, for Schedule B, a 0.9 MB zip and one HTML page; under a minute each.
- `check_descriptors.py`, `check_engine_version.py`: footer range reads of every published object; seconds.
- infra's `publish/test/*.test.sh`: a minute or two, offline.
- The live publish: writes to prod R2 and the catalogue; minutes.
