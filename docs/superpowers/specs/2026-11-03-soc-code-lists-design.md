# US SOC 2018 and UK SOC 2020: design

## Intent

Host the two Standard Occupational Classifications an analyst most often meets: the US 2018 SOC from the Bureau of Labor Statistics, and the UK's SOC 2020 from the Office for National Statistics. "soc codes" draws 8,100 searches a month in the US and 5,400 in the UK. Most of those searchers have a job title and want its code, or have codes and want them labelled or rolled up.

A hosted dataset is prepared as the best asset for an analyst's insight, not mirrored from the issuer's file (`docs/code-lists.md`, "What a hosted code list is"). Taking the analyst's seat, SOC serves three jobs, and each issuer publishes what each one needs:

1. **Labelling and rolling up coded data** (OEWS wages, ACS, the Labour Force Survey): the code list.
2. **Coding free-text job titles** (job postings, HR extracts, survey answers): a job-title index, one row per title pointing at a code.
3. **Bridging editions** (OEWS before 2021, the UK Labour Force Survey before its switch to SOC 2020): an edition crosswalk.

So each issuer gets one Protocol that writes three tables. This is also the first Protocol that writes several datasets, and the first hosted dataset meant to publish by `arc run` alone, with no publish script around it. It is built now and stops before publishing, which waits on arcform's publish step learning to write to openlake (see "Where the build stops").

## The six tables

| Address | Job | Source | Rows (measured 2026-11-03) |
|---|---|---|---|
| `bls/soc_2018` | code list | BLS `soc_2018_definitions.xlsx` | 1,447: 23 major, 98 minor, 459 broad, 867 detailed |
| `bls/soc_2018_titles` | job-title index | BLS `soc_2018_direct_match_title_file.xlsx` | 6,520 titles over 864 detailed occupations |
| `bls/soc_2010_to_2018` | edition crosswalk | BLS `soc_2010_to_2018_crosswalk.xlsx` | 900 pairs: 840 codes of 2010, 867 of 2018 |
| `ons/soc_2020` | code list | ONS SOC 2020 Volume 1 workbook | 551: major, sub-major, minor and 412 unit groups |
| `ons/soc_2020_titles` | job-title index | ONS SOC 2020 Volume 2, the coding index (version 14) | 32,439 coded entries of 32,670 |
| `ons/soc_2010_to_2020` | edition crosswalk | ONS SOC 2010 to SOC 2020 relationship tables, July 2021 | unit-group sheet, about 1,080 rows |

All the BLS files are US Government works in the public domain. All the ONS files are published under the Open Government Licence v3.0, which requires attribution.

## Goals

- Two Protocols, `datasets/soc_2018/` (BLS) and `datasets/soc_2020/` (ONS), each fetching its issuer's files once and writing its three tables, each table exported, described, checked and stamped inside the Run.
- Each table carries a descriptor emitted by a finished Run and committed as that Run wrote it.
- Two new table contracts beside the code-list contract, the job-title index and the edition crosswalk, each checked inside the Run, written up in `docs/code-lists.md` so NAICS's index and concordance follow them.
- `docs/code-lists.md` records the rulings below and the multi-table Protocol pattern.

## Non-goals

- **Publishing.** Neither Protocol is published from this work, and neither is added to `datasets/registry.json` or the README's Datasets table; the held namespaces `bls` and `ons` are added with the publish. See "Where the build stops".
- **Dataset pages** in web, which follow the publish.
- **Teaching the publish seam about several datasets per Protocol.** `scripts/publish_dataset.py` and CI's served-object checks assume one `datapackage.json` per directory. The publish step in arcform retires that seam, so this work adds nothing to it.
- **O*NET**, which is CC BY with a modification notice and trademark rules: a separate dataset if wanted.
- **ONS's SOC 2020 to 2010 direction**, and the major, sub-major and minor sheets of the relationship tables. The unit-group sheet in the 2010 to 2020 direction is the one an analyst uses to move old data forward; the others are later work if wanted.
- **The ISCO-08 framework sheet** in ONS's coding index, whose titles are the ILO's and whose terms were not checked. Each index entry's ISCO-08 code, which is ONS's own mapping, is carried; its title is not.
- **A finetype type for hyphenated SOC codes.** finetype is adding one separately; until it lands the column declares its shape in the overrides and links to no type page.

## Anti-goals

- **A crosswalk Meridian inferred.** Every crosswalk row is the issuer's own published pairing. Deriving a SOC 2010 to 2020 mapping from the job titles the coding index shares would read as an official concordance and is not one.
- **New scripts outside the Protocol.** Each check this work adds is a `sql:` step that fails the run with DuckDB's `error()`, not a Python script; the existing `check_code_list.py` and `stamp_descriptor.py` steps are reused as the other code lists use them. Test files are the exception, as they are today.
- **Text that is not the issuer's, presented as theirs.** Every `description` names its source in `text_source`, as on the other lists.

## Rulings made with the operator

- **US SOC's `code` keeps BLS's hyphen (`11-1011`).** The contract's rule is that `code` holds the code in the form other data carries it. HTS is digits-only by that rule applied to trade data. For SOC, BLS's OEWS, O*NET (`11-1011.00`) and the Department of Labor's filings carry the hyphen; Census's ACS carries digits with `X` placeholders that match no code in either form. `code_display` equals `code` for this list. UK SOC's `code` is ONS's plain digits (`1111`), the form the Labour Force Survey carries.
- **Scope: three tables per issuer.** The code list, the job-title index and the edition crosswalk, for both issuers. The UK crosswalk is in scope because ONS publishes its own relationship tables; had it not, the UK index's SOC 2010 column would have stood in for one.
- **One Protocol per issuer, writing several tables (approach B below).** Protocols are meant to carry real work, sized to the job, and the framework is judged against Dagster, Prefect, Mage and Dagu by how little has to be written around a Protocol.
- **Stop before publishing.** SOC waits for arcform's publish step so it can be the first hosted dataset published by `arc run` alone, rather than this work extending the publish seam.

## Approaches considered

- **A. One Protocol per table.** Six directories, each fetching its issuer's files itself, as Schedule B reads USITC's export itself. It fits the publish seam and every CI check as they stand. Declined: it multiplies the fetches and the manifests, and it shapes the Protocol around the harness rather than the work.
- **B. One Protocol per issuer, writing its three tables. Chosen.** One fetch of each file, one load of each workbook, and arc's skipping of steps whose inputs have not changed means a refresh rebuilds only the tables whose sources moved. Its cost is that the publish seam cannot publish it, which is why the build stops before publishing.
- **C. The title index and crosswalk read the published code list from the lake.** Declined: it ties each build to the lake and to an earlier publish, which Schedule B's design avoided, and the UK code list is not published.

## How each Protocol is laid out

A Protocol directory under `datasets/`, named for the issuer's edition, as the single-table lists are named for their dataset. Its steps, in order:

1. **Fetch**: one `http_fetch@1` per issuer file, with the Meridian User-Agent. BLS and ONS both serve that agent (checked 2026-11-03).
2. **`load`**: `sql:` models reading every workbook into staging tables, with DuckDB's `excel` extension, as `census/naics_2022`'s `load.sql` does. The release of each source is read here.
3. **One model per table**: `code_list.sql`, `titles.sql`, `crosswalk.sql`, each building its terminal table from the staging tables and adding `corpus` where the contract has one.
4. **Contract checks inside the Run**: `check_code_list` (the existing `uv@1` step) on the code list; `check_titles` and `check_crosswalk`, `sql:` steps over the exported files that fail the run on any rule below, including that every code the index or crosswalk points at is a code in the code list built in the same Run.
5. **Per table**: `parquet_export@1` to `build/stage/`, `datapackage_describe@1`, `finetype_validate@1`, and `stamp_descriptor`, which alone writes the published Parquet and the committed descriptor, keeping one writer per file.

**Where the descriptors live.** Each table's committed descriptor is `<table>.datapackage.json` in the Protocol directory (`datasets/soc_2018/soc_2018_titles.datapackage.json`), with its overrides, nominations and drift schema beside it under the same prefix. CI's descriptor sweeps read `datasets/*/datapackage.json`, so these are not swept against a served object before one exists. The Protocol's own steps and a model self-test (`scripts/test_soc_2018_models.py`, `scripts/test_soc_2020_models.py`, run in CI as the other lists' self-tests are) hold them to the contracts meanwhile. How the publish step and CI pick them up is settled with the publish step.

## The code lists

Both follow the code-list contract (`docs/code-lists.md`): the ten core columns, then the list's own.

**Ancestor columns.** Each row also carries the code of each level above it, so an analyst rolls a detailed occupation up to its major group with one GROUP BY, without walking `parent_code`. A row's own level and the levels below it are NULL. US: `major_code`, `minor_code`, `broad_code`. UK: `major_code`, `sub_major_code`, `minor_code`.

### `bls/soc_2018`

- **Levels**: `major`, `minor`, `broad`, `detailed`, as BLS names them.
- **Parents.** SOC 2018 codes are not plain prefixes: a broad group `11-3110` sits under the minor group `11-3000`, but `15-1210` sits under `15-1200`. `parent_code` is the code one level up that shares the most leading digits: for a detailed occupation, its code with the last digit set to 0; for a broad group, its code with the last two digits set to 0 if that is a minor group, otherwise with the last three set to 0; for a minor group, its major group (`XX-0000`). `load` fails the run if any row below the top finds no parent at the level above. On 2026-11-03 the two-digit form alone misses 78 broad groups, and the rule above leaves none.
- **Text.** `description` is BLS's title. `definition` is BLS's definition, present on every detailed occupation and NULL above it, as BLS prints none there. `text_source = 'bls'` throughout.
- **Release.** BLS's file names no release, but its header reads "November 2017 (for reference year January 2018 - Updated April 15, 2020)". `release` is `2018/2020-04-15`, the edition and the date of its last update, parsed from that line in `load`; the run fails if the line does not parse.

### `ons/soc_2020`

- **Levels**: `major` (one digit), `sub_major` (two), `minor` (three), `unit` (four). Each code is a prefix of the codes below it, so parents are found by prefix, as in the HS lists.
- **Text.** `description` is ONS's group title as Volume 1's descriptions sheet writes it; the framework sheet's capitalised forms are not used. The list's own columns carry ONS's group description (`group_description`, on every row), and on unit groups `entry_routes`, and `tasks` and `related_job_titles` as lists of strings, split from ONS's `~ `-bulleted cells. `text_source = 'ons'`.
- **Release.** ONS names each workbook with the date it was issued (`…excel20260827.xlsx`), and a revision gets a new URL. `release` is `2020/2026-08-27`, read in `load` from the fetched file's name, so it cannot disagree with the file the rows came from. A refresh changes the URL literal in `arcform.yaml` and the source's path in the overrides.
- **Attribution.** The descriptor's `licenses` is `OGL-UK-3.0`, and its description carries ONS's attribution statement. How a published file and page carry an OGL notice is being settled separately; whatever it settles is applied at the publish.

## The job-title index contract

A job-title index is a table of names pointing at codes in a code list. Its leading columns, in order:

| Column | Type | Meaning |
|---|---|---|
| `entry_id` | VARCHAR, primary key | The issuer's id for the entry where it has one (ONS's `UNIQUE ID`); otherwise the code and title together. |
| `title` | VARCHAR, not null | The title as the issuer prints it. |
| `code` | VARCHAR, not null | The code it points at, in the code list's own form. |
| `code_description` | VARCHAR | That code's `description` in the code list, so a lookup needs no second join. |
| `release` | VARCHAR, not null | The release of the index. One distinct value per file. |
| `text_source` | VARCHAR | Whose text `title` is. |
| `corpus` | VARCHAR | `title` lowercased and accent-stripped, with its qualifiers, for the in-browser search. |

A descriptor marks the table with `"x-meridian-title-index": {"codeList": "<namespace>/<name>"}`. `check_titles` fails the run on no rows, a missing or misordered core column, a NULL or repeated `entry_id`, a NULL `title` or `code`, a `code` that is not a code in the named list built in the same Run, a `code` at a level the issuer does not code titles to (detailed occupations for BLS, unit groups for ONS), or more than one `release`.

### `bls/soc_2018_titles`

- One row per BLS direct-match title. `entry_id` is `<code>|<title>`. BLS's titles are unique across occupations (no title maps to two codes, measured after lowercasing).
- `illustrative` (BOOLEAN): BLS marks 2,400 titles as illustrative examples rather than direct matches.
- Three detailed occupations have no titles in BLS's file; they appear in the code list and not here.
- `release` is BLS's header date (`2018/2020-04-15`), parsed as for the code list.

### `ons/soc_2020_titles`

- One row per coded entry of ONS's coding index. `entry_id` is ONS's `UNIQUE ID`. Two pairs of entries repeat a title and both qualifiers, which is why the key is ONS's id and not the title.
- **Qualifiers as columns.** ONS codes a title by its qualifiers where they decide the code: `additional_qualifier` (2,700 entries) and `industry_qualifier` (10,675). `title_natural` is ONS's natural word order ("machine assistant" for "Assistant, machine").
- **The coding fields ONS publishes**: `default_flag` (ONS's `D` column, with its meanings from the file's specification in the field description), `soc_2020_ext` and its title (ONS's extended sub-unit group, `2439/99`), `soc_2010`, `soc_2000` and `soc_1990` (the codes each title had in earlier editions, as ONS records them), `isco_08` (ONS's mapping), `version_added_or_amended` and `change`.
- **Cross-references are not entries.** 231 index rows have no code and say "see Adviser" or "see also notes": they point at another title, not a code. They are left out, and `load` checks that every row left out is one of them.
- `release` is the index's version and file date, `v14/2026-08-27`, read from the INFO sheet and the file name; the run fails if either does not parse.

## The edition crosswalk contract

A crosswalk is a table of the issuer's own pairings between codes of two editions. Its leading columns, in order:

| Column | Type | Meaning |
|---|---|---|
| `from_code` | VARCHAR, not null | A code of the earlier edition. |
| `from_description` | VARCHAR | Its title, as the issuer prints it in the crosswalk. |
| `to_code` | VARCHAR, not null | A code of the later edition. |
| `to_description` | VARCHAR | Its title. |
| `from_edition`, `to_edition` | VARCHAR, not null | The editions, such as `2010` and `2018`. |
| `release` | VARCHAR, not null | The crosswalk's own release. |

`(from_code, to_code)` is the primary key. A descriptor marks the table with `"x-meridian-crosswalk": {"from": "<edition>", "to": "<edition>", "toCodeList": "<namespace>/<name>"}`. `check_crosswalk` fails the run on no rows, a missing or misordered core column, a repeated pair, a NULL code, a `to_code` that is not a code at the same level in the named list built in the same Run, a `from_code` of the wrong shape, or more than one `release`. The earlier edition is not hosted, so `from_code` is checked by shape only.

### `bls/soc_2010_to_2018`

- BLS's 900 pairings at the detailed level, unweighted, as BLS publishes them. Every 2018 code in BLS's file is in `bls/soc_2018`.
- `release` is BLS's header date, `2017-11`.

### `ons/soc_2010_to_2020`

- The unit-group sheet of ONS's SOC 2010 to SOC 2020 relationship tables (July 2021): for each 2010 unit group, the 2020 unit groups its jobs were recoded to, with the share that went to each.
- **ONS's weights as columns.** ONS gives each pairing four shares, men and women from the Labour Force Survey and from the Census, and each 2010 group's base counts (its "Total sample" rows). They are carried as `men_lfs_share`, `women_lfs_share`, `men_census_share`, `women_census_share` and the four bases, so an analyst can apportion 2010-coded counts to 2020 groups. The "Total sample" rows become the base columns, not rows. A pairing with every share 0 is kept, as ONS prints it.
- `release` is `2021-07`, ONS's issue of the tables.

## Where the build stops

The pull request is ready when its branch review and CI are clean, and both Protocols have run to completion with the committed descriptors those runs wrote. Nothing is published from it. The publish waits for arcform's publish step to write to openlake's R2 and to publish several tables from one Protocol; SOC is then published by `arc run` alone, and the registry entries, held namespaces, README rows and dataset pages follow then.

## Calls made in writing this design

- **One shared SQL file per new contract.** `check_titles` and `check_crosswalk` are written once (`datasets/contracts/check_titles.sql` and `check_crosswalk.sql`, or beside `check_code_list.py`), and each Protocol runs them by relative path after setting up the inputs they read: the exported table, the code list built in the same Run, and the level titles are coded to. `docs/code-lists.md` shares SQL only for a shared source quirk; a contract is a second reason to share, since the same rules must hold for every list, as `check_code_list.py` is one script.
- **Descriptors outside `datasets/*/datapackage.json` until the publish**, so CI's served-object sweeps stay green on an unpublished Protocol.
- **One pull request for both issuers.** Neither publishes from it, so the UK attribution question no longer needs the UK work held on a branch of its own.
- **Ancestor columns** on both code lists.
- **ONS's 2010 to 2020 unit-group sheet only**, not the reverse direction or the higher-level sheets.
- **The ONS index's 231 cross-reference rows left out.**

## Left to the builder

- The model self-tests' fixtures: cut-down copies of each workbook, built by the test or committed small.
- Each table's nominations and drift schema, following `docs/code-lists.md`.
- Column order after the core columns, and field descriptions, following the existing lists.
- Whether `load` is one model or one per issuer file, as long as each file is read once.
- Builds use arc 0.2.1 and DuckDB CLI 1.5.5, the pair CI holds the Parquet writer to.
