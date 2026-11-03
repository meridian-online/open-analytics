# US SOC 2018 and UK SOC 2020 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two Protocols, `datasets/soc_2018/` (BLS) and `datasets/soc_2020/` (ONS), each writing a code list, a job-title index and an edition crosswalk, each table described, checked and stamped inside the Run, with nothing published.

**Architecture:** One `arcform.yaml` per issuer fetches each source workbook once, loads it with DuckDB's `excel` extension into staging tables, and builds three terminal tables. Two new contracts (title index, crosswalk) are shared `sql:` checks under `contracts/` that fail the run with `error()`; the code list reuses `scripts/check_code_list.py`. Each table's committed descriptor is `<table>.datapackage.json` in the Protocol directory, outside CI's `datasets/*/datapackage.json` sweeps.

**Tech Stack:** arc 0.2.1, DuckDB CLI 1.5.5 (and Python `duckdb==1.5.5` for self-tests), finetype, openpyxl for test fixtures.

**Spec:** `docs/superpowers/specs/2026-11-03-soc-code-lists-design.md`

## Global Constraints

- Builds run with the arc 0.2.1 release binary and DuckDB CLI 1.5.5, never the older `arc` on the default `PATH`. The controller supplies an environment file to `source` before any run; it exports `ARC_DUCKDB_BIN` (the DuckDB 1.5.5 CLI) and `FINETYPE_DUCKDB_EXT` (the finetype extension built for DuckDB 1.5.5), and puts the arc 0.2.1 binary and a `python3` that imports `duckdb==1.5.5` first on `PATH`. In this plan, `arc` means that binary: check `arc --version` prints `arc 0.2.1` before a run.
- One cargo or arc build at a time on this machine; implementers do not run in parallel.
- Every fetch sends `User-Agent: "Meridian Protocol (open-analytics; research@meridian.online)"`.
- No new Python outside tests. New checks are `sql:` steps; `check_code_list.py` and `stamp_descriptor.py` are reused by `uv@1` with their current sha256 pins (`11b06ca7…`, `b2258…`, copy from `datasets/ndc/arcform.yaml`).
- One writer per file: export and describe write to `build/stage/`; `stamp_descriptor` alone writes `build/<table>.parquet` and `<table>.datapackage.json`.
- Nothing is published, and `datasets/registry.json`, the README Datasets table and the namespaces are not touched.
- Public repo: no Kata refs, no `hughcameron/*` names, no local paths in tracked files, commit messages or PR text.
- `code`: US hyphenated (`11-1011`, `code_display = code`); UK plain digits as text (`1111`).
- `description_full` joins with ` › ` (space, U+203A, space); `corpus` is `lower(strip_accents(...))`.
- Committed descriptors are the ones a finished `arc run` wrote; never hand-edited after the run.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An issuer revises its workbook** (new ONS file date, BLS header changes): `load` must fail on an unparseable BLS release line, or an ONS sidecar that is missing or whose URL does not match its release pattern, not publish a NULL or stale release. Pinned in Tasks 2 and 5.
2. **Excel cells typed as numbers** (ONS codes as integers, `'<blank>'` strings, trailing spaces): codes must come out as trimmed text with no `.0`, and `'<blank>'` must read as NULL. Pinned in Task 5.
3. **A title or crosswalk row pointing at a code the list lacks**: the shared checks must fail the run, naming the code. Pinned in Task 1 and exercised in Tasks 3 and 6.
4. **BLS's two minor-group code forms** (`11-3000` vs `15-1200`): every broad group finds its parent; a broad group with no minor at either form fails the run. Pinned in Task 2.
5. **ONS's "Total sample" rows and cross-reference rows**: base counts land on the pairing rows, and only `see …` rows are dropped from the index; any other dropped row fails the run. Pinned in Tasks 6.

---

### Task 1: The title-index and crosswalk contracts

**Files:**
- Create: `contracts/check_titles.sql`, `contracts/check_crosswalk.sql`, `contracts/README.md`
- Test: `scripts/test_contracts.py`
- Modify: `.github/workflows/descriptors.yml` (one job, `contracts-sql`, as `ndc-models` is laid out, installing `duckdb==${DUCKDB_VERSION}`)

**Interfaces:**
- Consumes, in the same DuckDB connection (set up by the calling Protocol's own model): a view `contract_titles` (the exported title index), a view `contract_code_list` (the code list Parquet from the same Run), and a one-row table `contract_title_level(level VARCHAR)`; for crosswalks `contract_crosswalk`, `contract_code_list`, and `contract_crosswalk_shape(from_pattern VARCHAR, level VARCHAR)`.
- Produces: each file ends with `CREATE OR REPLACE TABLE contract_titles_result AS SELECT <rows> AS rows_checked` (resp. `contract_crosswalk_result`) after every rule passes; on any failure it raises `error('<rule>: <first offending value>')`.

- [ ] **Step 1: Write `scripts/test_contracts.py`** with one passing case per contract and one failing case per rule in the spec ("The job-title index contract", "The edition crosswalk contract"): `test_titles_pass`, `test_titles_empty`, `test_titles_column_order`, `test_titles_duplicate_entry_id`, `test_titles_null_title`, `test_titles_code_not_in_list` (asserts the message contains the code), `test_titles_wrong_level`, `test_titles_two_releases`; `test_crosswalk_pass`, `test_crosswalk_empty`, `test_crosswalk_column_order`, `test_crosswalk_duplicate_pair`, `test_crosswalk_null_code`, `test_crosswalk_to_code_not_in_list`, `test_crosswalk_to_code_wrong_level`, `test_crosswalk_from_code_shape`, `test_crosswalk_two_releases`. Each builds the input tables in-memory with Python `duckdb`, runs the SQL file, and asserts `duckdb.InvalidInputException` with the rule name in the message, or the result row count.
- [ ] **Step 2: Run** `uv run --quiet --with duckdb==1.5.5 python3 scripts/test_contracts.py` — Expected: FAIL, files missing.
- [ ] **Step 3: Write the two SQL files.** Core column order is checked against `DESCRIBE contract_titles` / `contract_crosswalk`: titles lead with `entry_id, title, code, code_description, release, text_source, corpus`; crosswalks with `from_code, from_description, to_code, to_description, from_edition, to_edition, release`. `contracts/README.md` states the interface above in a paragraph per contract.
- [ ] **Step 4: Run** the same command — Expected: all tests PASS (under a second).
- [ ] **Step 5: Commit** "The title-index and crosswalk contracts, checked in SQL".

### Task 2: BLS staging and the `bls/soc_2018` code list

**Files:**
- Create: `datasets/soc_2018/models/load.sql`, `datasets/soc_2018/models/code_list.sql`
- Create: `scripts/testdata/soc_2018/` with cut copies of `soc_2018_definitions.xlsx`, `soc_2018_direct_match_title_file.xlsx`, `soc_2010_to_2018_crosswalk.xlsx` (header rows and sheet names kept; rows: major 11 and 15 with every minor, broad and detailed under `11-3000`, `11-9000`, `15-1200`; the crosswalk and title rows for those detailed codes)
- Test: `scripts/test_soc_2018_models.py`

**Interfaces:**
- Consumes: the three workbooks at `build/bls/<BLS file name>`.
- Produces: staging tables `bls_definitions(soc_group, code, title, definition)`, `bls_titles(code, title, direct_match_title, illustrative)`, `bls_crosswalk(code_2010, title_2010, code_2018, title_2018)`, scalar table `bls_release(release VARCHAR, crosswalk_release VARCHAR)`; terminal table `soc_2018_out` with the ten core columns, then `definition, major_code, minor_code, broad_code`.

- [ ] **Step 1: Write the failing tests**: `test_code_list_passes_contract` (export `soc_2018_out` to Parquet, run `check_code_list.py --levels major,minor,broad,detailed`, exit 0); `test_parents` (`15-1211`→`15-1210`, `15-1210`→`15-1200`, `11-3010`→`11-3000`, `11-3000`→`11-0000`, `11-0000`→NULL); `test_ancestors` (`15-1211` has `major_code='15-0000'`, `minor_code='15-1200'`, `broad_code='15-1210'`; a major row has all three NULL); `test_release` (`'2018/2020-04-15'` on every row); `test_release_unparseable_fails` (header line replaced → error mentioning release); `test_broad_without_minor_fails` (minor `11-3000` deleted from the fixture → error); `test_definition_only_on_detailed`; `test_code_display_equals_code`.
- [ ] **Step 2: Run** `uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2018_models.py` — Expected: FAIL.
- [ ] **Step 3: Write `load.sql`** (`INSTALL excel; LOAD excel;`, `read_xlsx(..., header=false, all_varchar=true, stop_at_empty=false)` with an explicit `range`. BLS's sheet layouts, measured 2026-11-03: in `soc_2018_definitions.xlsx` and `soc_2018_direct_match_title_file.xlsx` the release line is cell `A4`, the column headers row 8 and the data from row 9; in `soc_2010_to_2018_crosswalk.xlsx` the release line is `A4` ("November 2017"), the column headers row 9 and the data from row 10. Read each release line with its own one-cell range and check the header row's text before reading data, failing the run if it moved. The release line is parsed with `regexp_extract` for "Updated <Month> <d>, <yyyy>", and the crosswalk's "November 2017" as `2017-11`; `error()` on any parse failure) and **`code_list.sql`** (parent rule from the spec's "Parents" paragraph; `error()` on any non-major row with no parent at the level above; `description_full` from the ancestors' titles; `corpus`; `text_source='bls'`).
- [ ] **Step 4: Run** the test — Expected: PASS.
- [ ] **Step 5: Commit** "The BLS SOC 2018 code list, with its ancestors and release".

### Task 3: `bls/soc_2018_titles` and `bls/soc_2010_to_2018`

**Files:**
- Create: `datasets/soc_2018/models/titles.sql`, `datasets/soc_2018/models/crosswalk.sql`, `datasets/soc_2018/models/check_inputs_titles.sql`, `datasets/soc_2018/models/check_inputs_crosswalk.sql`
- Test: `scripts/test_soc_2018_models.py` (extend)

**Interfaces:**
- Consumes: Task 2's staging tables and `soc_2018_out`; Task 1's contract interface.
- Produces: `soc_2018_titles_out` (core title columns, then `illustrative BOOLEAN`); `soc_2010_to_2018_out` (core crosswalk columns, `from_edition='2010'`, `to_edition='2018'`). The `check_inputs_*` models create the `contract_*` views over `build/stage/<table>.parquet` and `build/stage/soc_2018.parquet`, with level `detailed` and `from_pattern '^[0-9]{2}-[0-9]{4}$'`.

- [ ] **Step 1: Write the failing tests**: `test_titles_pass_contract` and `test_crosswalk_pass_contract` (export, run the check-inputs model then `contracts/check_*.sql`); `test_entry_id_form` (`'11-1011|Chief Executive Officer'`); `test_illustrative` (`'x'`→true, `''`→false); `test_code_description` (matches `soc_2018_out.description`); `test_crosswalk_pairs` (row count equals the fixture's pairs; `release='2017-11'`).
- [ ] **Step 2: Run** the Task 2 command — Expected: FAIL.
- [ ] **Step 3: Write the four models.** `corpus` for titles is `lower(strip_accents(title))`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** "The BLS job-title index and SOC 2010 to 2018 crosswalk".

### Task 4: The BLS Protocol, run to completion

**Files:**
- Create: `datasets/soc_2018/arcform.yaml`, `datasets/soc_2018/README.md` (with the generated step block `check_protocol_readme.py` requires), and per table `<table>.overrides.json`, `<table>.nominations.finetype.json`, `<table>.schema.finetype.json` for `soc_2018`, `soc_2018_titles`, `soc_2010_to_2018`
- Create (written by the run, then committed): `datasets/soc_2018/soc_2018.datapackage.json`, `soc_2018_titles.datapackage.json`, `soc_2010_to_2018.datapackage.json`
- Modify: `.github/workflows/descriptors.yml` (job `soc-2018-models` running `scripts/test_soc_2018_models.py` with duckdb and openpyxl)
- Modify: `scripts/test_manifest_one_writer.py` (append `"soc_2018"` to `ONE_WRITER`)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `build/soc_2018.parquet`, `build/soc_2018_titles.parquet`, `build/soc_2010_to_2018.parquet` and the three committed descriptors.

- [ ] **Step 1: Write `arcform.yaml`**: three `http_fetch@1` steps to `build/bls/`; `load`; `code_list`, `titles`, `crosswalk`; per table `parquet_export@1` to `build/stage/<table>.parquet` (zstd, `order_by: "code"` for the list, `"entry_id"` for titles, `"from_code, to_code"` for the crosswalk); per table `datapackage_describe@1` to `build/stage/<table>.described.json`; `check_code_list` (`--levels major,minor,broad,detailed`); `check_inputs_titles` + `check_titles` (`sql: ../../contracts/check_titles.sql`); likewise for the crosswalk; per table `finetype_validate@1`; per table `stamp_descriptor` with `--out build/<table>.parquet --out-descriptor <table>.datapackage.json --carry-created`. Every `sql:` step declares under `depends_on` the files it reads, as `datasets/hcpcs/arcform.yaml` does: `load` the three fetched workbooks (BLS's release comes from cell A4, not the sidecars, so the sidecars are not declared here; Task 7's ONS `load` declares its sidecars, which it reads), each `check_inputs_*` step the stage Parquets it opens (its table's and `build/stage/soc_2018.parquet`). Each table's describe output, stamp output and drift schema has its own path, so no two steps declare the same file. Overrides: `licenses` `other-pd`, "Public domain (U.S. Bureau of Labor Statistics)", `https://www.bls.gov/opub/copyright-information.htm`; `sources` the BLS 2018 SOC page and each file; contributors BLS author, Meridian publisher and wrangler; `resource.path` the future `https://openlake.meridian.online/bls/<table>.parquet`; `x-meridian-code-list` levels; `x-meridian-title-index` `{"codeList": "bls/soc_2018"}`; `x-meridian-crosswalk` `{"from": "2010", "to": "2018", "toCodeList": "bls/soc_2018"}`; `code` constraint pattern `^[0-9]{2}-[0-9]{4}$`; a description for every field.
- [ ] **Step 2: Run the manifest checks**: `python3 scripts/check_protocol_readme.py` and `python3 scripts/test_manifest_one_writer.py` — Expected: both pass, `check_protocol_readme.py` counting `soc_2018` among the manifests it examined, and `test_manifest_one_writer.py` reading `datasets/soc_2018/arcform.yaml` (it names the manifests it checks in `ONE_WRITER`).
- [ ] **Step 3: Run the Protocol cold**: `cd datasets/soc_2018 && arc run --force` — Expected: outcome `success`, every step reached; about a minute (three small fetches, finetype describe of three tables).
- [ ] **Step 4: Check the outputs**: row counts 1,447 / 6,520 / 900 (`duckdb -c "select count(*) from 'build/<t>.parquet'"`), and `git status` shows the three descriptors as new files written by the run.
- [ ] **Step 5: Commit** the Protocol and the three descriptors as the run wrote them: "The BLS SOC 2018 Protocol: code list, job-title index and crosswalk".

### Task 5: ONS staging and the `ons/soc_2020` code list

**Files:**
- Create: `datasets/soc_2020/models/load.sql`, `datasets/soc_2020/models/code_list.sql`
- Create: `scripts/testdata/soc_2020/` with cut copies of `soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx` (major 1 and 9, every group beneath them), `soc2020volume2thecodingindexexcel20260827v2.xlsx` (INFO and FILE SPEC sheets whole; index rows for those unit groups plus two `see …` rows and two rows that repeat a title and both qualifiers), `soc2010soc2020relationshiptablesjuly2021.xlsx` (unit-sheet blocks whose every 2020 `to_code` is a unit group in the code-list fixture, so the contract check holds against a correct model; at least one 2010 group whose jobs went to two or more 2020 groups)
- Test: `scripts/test_soc_2020_models.py`

**Interfaces:**
- Consumes: `build/ons/volume1.xlsx`, `build/ons/coding_index.xlsx`, `build/ons/relationship_2010_2020.xlsx`, each with its `.arcmeta` sidecar (the test writes a sidecar beside each fixture copy, its `url:` the real ONS URL from Task 7).
- Produces: staging `ons_groups(code, level, title, group_description, entry_routes, tasks VARCHAR[], related_job_titles VARCHAR[])`, `ons_index(...)` with the spec's columns, `ons_relationship(code_2010, title_2010, code_2020, title_2020, men_lfs_base, men_lfs_share, men_census_base, men_census_share, women_lfs_base, women_lfs_share, women_census_base, women_census_share)`, `ons_release(code_list_release, index_release, crosswalk_release)`; terminal `soc_2020_out` with the core columns, then `group_description, entry_routes, tasks, related_job_titles, major_code, sub_major_code, minor_code`.

- [ ] **Step 1: Write the failing tests**: `test_code_list_passes_contract` (`--levels major,sub_major,minor,unit`); `test_codes_are_text` (`'1111'`, no `'.0'`, no `'<blank>'` anywhere); `test_parents_by_prefix` (`1111`→`111`→`11`→`1`); `test_ancestors`; `test_tasks_split` (first task of `1111` begins `analyses economic`, no leading `~`); `test_description_not_capitalised` (`11` is `Corporate managers and directors`); `test_release` (`'2020/2026-08-27'`); `test_release_from_sidecar_url` (a sidecar naming `…excel20270115.xlsx` gives `2020/2027-01-15`); `test_sidecar_url_without_date_fails`; `test_missing_sidecar_fails`; `test_relationship_release_from_url` (`…july2021.xlsx` gives `2021-07`).
- [ ] **Step 2: Run** `uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2020_models.py` — Expected: FAIL.
- [ ] **Step 3: Write `load.sql` and `code_list.sql`.** Codes via `trim(cast(... AS VARCHAR))` with `nullif(..., '<blank>')`; `~ `-bulleted cells split with `string_split` on newline and stripped of the leading `~ `; each workbook is fetched to a fixed path (`build/ons/volume1.xlsx`, `build/ons/coding_index.xlsx`, `build/ons/relationship_2010_2020.xlsx`), and each release is read from the `url:` line of the `.arcmeta` sidecar `http_fetch@1` writes beside the workbook, so it is the one in the URL actually fetched and a refresh needs no clean-up. Volume 1 and the coding index: `regexp_extract(url, 'excel([0-9]{8})')`, giving `2020/2026-08-27` and, with INFO's "Version <n>" line, `v14/2026-08-27`. The relationship tables: the month and year at the end of the file name (`…relationshiptablesjuly2021.xlsx`), `regexp_extract(url, '(january|february|march|april|may|june|july|august|september|october|november|december)([0-9]{4})\.xlsx$', 1)` for the month (mapped to its number) and the same call with group 2 for the year, giving `2021-07`. The run fails if a sidecar is missing or its URL does not match its pattern; `text_source='ons'`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** "The ONS SOC 2020 code list, with its ancestors and release".

### Task 6: `ons/soc_2020_titles` and `ons/soc_2010_to_2020`

**Files:**
- Create: `datasets/soc_2020/models/titles.sql`, `crosswalk.sql`, `check_inputs_titles.sql`, `check_inputs_crosswalk.sql`
- Test: `scripts/test_soc_2020_models.py` (extend)

**Interfaces:**
- Consumes: Task 5's staging tables and `soc_2020_out`; Task 1's contract interface.
- Produces: `soc_2020_titles_out` (core title columns, then `title_natural, additional_qualifier, industry_qualifier, default_flag, soc_2020_ext, soc_2020_ext_title, soc_2010, soc_2000, soc_1990, isco_08, version_added_or_amended, change`); `soc_2010_to_2020_out` (core crosswalk columns, `from_edition='2010'`, `to_edition='2020'`, then the four base and four share columns). Check inputs: level `unit`, `from_pattern '^[0-9]{4}$'`.

- [ ] **Step 1: Write the failing tests**: `test_titles_pass_contract`; `test_crosswalk_pass_contract`; `test_see_rows_dropped` (the two `see …` rows absent, all others present); `test_non_see_uncoded_row_fails` (a row with `}}}}` and no `see` → error); `test_repeated_title_kept_by_entry_id`; `test_corpus_has_qualifiers`; `test_crosswalk_bases_on_pairs` (each pairing carries its 2010 group's "Total sample" bases; no row has `to_code` NULL or `'Total sample'`); `test_zero_share_pair_kept`; `test_crosswalk_release` (`'2021-07'`).
- [ ] **Step 2: Run** the Task 5 command — Expected: FAIL.
- [ ] **Step 3: Write the four models.** Crosswalk: split ONS's `"1115: Chief executives…"` cells on the first `': '`; carry the last non-null 2010 cell down. Order is the sheet's row order: number the rows in the same scan as the `read_xlsx` call, with `preserve_insertion_order` left on and the read single-threaded, or from a row-number column if the excel extension in DuckDB 1.5.5 provides one; `test_crosswalk_bases_on_pairs` pins the bases of two named 2010 groups so a misordered fill fails; bases from the "Total sample" row of each 2010 group.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** "The ONS coding index and SOC 2010 to 2020 relationship table".

### Task 7: The ONS Protocol, run to completion

**Files:**
- Create: `datasets/soc_2020/arcform.yaml`, `README.md`, per-table overrides, nominations and drift schemas for `soc_2020`, `soc_2020_titles`, `soc_2010_to_2020`
- Create (written by the run, then committed): the three `<table>.datapackage.json`
- Modify: `.github/workflows/descriptors.yml` (job `soc-2020-models`)
- Modify: `scripts/test_manifest_one_writer.py` (append `"soc_2020"` to `ONE_WRITER`)

**Interfaces:**
- Consumes: Tasks 1, 5, 6.
- Produces: the three Parquets under `build/` and three committed descriptors.

- [ ] **Step 1: Write `arcform.yaml`** as Task 4's, fetching these three ONS URLs to the fixed paths Task 5 reads (checked 2026-11-03): `https://www.ons.gov.uk/file?uri=/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/soc2020volume1structureanddescriptionsofunitgroups/soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx`, `https://www.ons.gov.uk/file?uri=/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/soc2020volume2codingrulesandconventions/soc2020volume2thecodingindexexcel20260827v2.xlsx`, `https://www.ons.gov.uk/file?uri=/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020/therelationshipbetweenstandardoccupationalclassification2010andstandardoccupationalclassification2020/soc2010soc2020relationshiptablesjuly2021.xlsx`; `check_code_list --levels major,sub_major,minor,unit`. Overrides: `licenses` `[{"name": "OGL-UK-3.0", "title": "Open Government Licence v3.0", "path": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"}]`; the description carries "Source: Office for National Statistics licensed under the Open Government Licence v3.0." and says Meridian prepared the tables from ONS's files; ONS as author; `code` pattern `^[0-9]{1,4}$`; `x-meridian-*` keys naming `ons/soc_2020`.
- [ ] **Step 2: Run** `python3 scripts/check_protocol_readme.py` and `python3 scripts/test_manifest_one_writer.py` — Expected: both pass with `soc_2020` examined, as in Task 4.
- [ ] **Step 3: Run cold**: `cd datasets/soc_2020 && arc run --force` — Expected: `success`; about two minutes (the 3.5 MB index).
- [ ] **Step 4: Check** row counts 551 / 32,439 / the unit sheet's pairings, and that the run wrote the three descriptors.
- [ ] **Step 5: Commit** "The ONS SOC 2020 Protocol: code list, coding index and relationship table".

### Task 8: Fold the design into the code-list guide

**Files:**
- Modify: `docs/code-lists.md`, `datasets/label-agreement.json` (only if `scripts/check_label_agreement.py` reports new SOC disagreements that are true of both packages; it reads only `datasets/*/datapackage.json`, so expect none)
- Delete: `docs/superpowers/specs/2026-11-03-soc-code-lists-design.md`, `docs/superpowers/plans/2026-11-03-soc-code-lists.md`

- [ ] **Step 1: Write into `docs/code-lists.md`**: the contract's `code` rule restated ("the form other data carries", with HTS digits-only and SOC hyphenated as its two answers); "The job-title index contract" and "The edition crosswalk contract" sections from the spec, with the `contracts/` interface; "A Protocol that writes several tables" (one fetch, per-table describe and stamp, descriptors as `<table>.datapackage.json`, why the publish waits for arcform's publish step); "Made for SOC" rulings, the spec's operator rulings and the calls in its "Calls made" section, each with its reason; measured counts under a dated heading.
- [ ] **Step 2: Run** `uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2018_models.py && uv run --quiet --with duckdb==1.5.5 --with openpyxl python3 scripts/test_soc_2020_models.py && uv run --quiet --with duckdb==1.5.5 python3 scripts/test_contracts.py` — Expected: all pass.
- [ ] **Step 3: Delete the spec and plan**, then commit "Fold the SOC design into the code-list guide".

## Verification costs

- Self-tests (Tasks 1–3, 5–6): seconds each, local, no network beyond the first `INSTALL excel`.
- Protocol runs (Tasks 4, 7): one to two minutes each, fetching from bls.gov and ons.gov.uk.
- Full CI gate: on the pull request only; locally, only to reproduce a CI failure.

## Task dependencies

Task 1 first. Tasks 2 → 3 → 4 (BLS) and 5 → 6 → 7 (ONS) each depend on Task 1 and run in order; the BLS chain goes before the ONS chain, one implementer at a time. Task 8 last.
