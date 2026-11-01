# Hosted code lists

How Meridian hosts a code list, and what the next one reuses. `usitc/hts` (the Harmonized Tariff Schedule of the United States) and `census/schedule_b` (the Census Bureau's export codes) are the first two. HCPCS Level II, the NDC directory, SOC, FIPS state and county codes, and OurAirports' airports follow on the same pattern. Read this before building any of them.

## What a hosted code list is

A code list is a ladder of codes, each pointing at its parent: a chapter, the headings in it, the subheadings in those, down to the lines other data carries. Meridian hosts one so an analyst can query it with SQL instead of reading a PDF or clicking through a lookup site.

The rule behind every hosted dataset: it is prepared as the best data asset for an analyst's insight, not limited to the shape or gaps of the issuer's file. Schedule B shipped as Census's ten-digit lines alone, with no headings, would technically work and would fail that rule. The preparation is done in the Protocol, in SQL on DuckDB wherever SQL can do it.

Two limits hold the rule to its sources. A gap is filled only from a source cleared for redistribution: the WCO's HS nomenclature and UN Comtrade are not, so the HS six-digit layer comes from US Government publications only. And every value brought in from a second source says where it came from, on the row and in the descriptor: a Schedule B heading whose text came from USITC carries `text_source = 'usitc'` and never reads as Census's own words.

## The contract

Every hosted code list leads with these ten columns, in this order, and adds its own after them.

| Column | Type | Meaning |
|---|---|---|
| `code` | VARCHAR, primary key | The code in the form other data carries it: digits only for HTS and Schedule B (`0101210010`, chapters `01`). |
| `code_display` | VARCHAR | The code as the issuer prints it (`0101.21.00.10` for HTS, `0101.21.0000` for Schedule B). |
| `level` | VARCHAR, closed set per list | The level's name. |
| `depth` | INTEGER | 0 for the top level, one more per level down. |
| `parent_code` | VARCHAR | The `code` of the row one level up; NULL only at depth 0. |
| `description` | VARCHAR | The row's own text. |
| `description_full` | VARCHAR | The row's text with its ancestors' in front, joined with ` › ` (space, U+203A, space), so a line printed as "Other" reads in context. |
| `release` | VARCHAR, not null | The issuer's release this row was built from. |
| `text_source` | VARCHAR, closed set per list | Whose text `description` is. |
| `corpus` | VARCHAR | `description_full` lowercased and accent-stripped, the field the in-browser search reads. |

A descriptor marks itself a code list with a package-level key naming its levels from the top down, set in its `descriptor.overrides.json`:

```json
"x-meridian-code-list": {"levels": ["chapter", "heading", "subheading", "commodity"]}
```

`scripts/check_code_list.py` holds a built Parquet to the contract, one SQL query per rule. It refuses a file with no rows; a missing core column or one out of order; a repeated or NULL `code`; a `parent_code` that is not a `code` in the same file; a NULL `parent_code` below the top level; a child whose `depth` is not its parent's plus one; a row whose `depth` is not its `level`'s position in the declared levels; a `level` outside the declared set; a NULL `release`, or more than one distinct `release` in the file; a NULL `description`.

It runs in two places. Each code-list Protocol runs it as its `check_code_list` step (`op: uv@1`, the script pinned by sha256, with `--parquet` and `--levels`), so a build that breaks the contract fails the run before it is validated and stamped. CI runs it with no arguments: it reads every `datasets/*/datapackage.json` carrying `x-meridian-code-list` and checks the object each one serves. Which datasets are code lists is read from their descriptors, never from a list in the script, so a new list opts in by declaring the key. A sweep that finds no code list exits 2, and CI counts that as a failure. `scripts/test_check_code_list.py` runs the check on deliberately broken fixtures.

Editing the script changes its digest, so every Protocol pinning it needs its `sha256` updated in the same commit (`shasum -a 256 scripts/check_code_list.py`).

## How a code-list Protocol is laid out

A code-list Protocol is a directory under `datasets/`, named after the dataset, holding `arcform.yaml`, `models/`, a `README.md` with the generated step list, `descriptor.overrides.json`, `nominations.finetype.json` and `schema.finetype.json`. Its steps, in order:

1. **Fetch, with the release recorded.** `http_fetch@1` of the issuer's file, plus whatever names the release (below), with the Meridian User-Agent on every fetch.
2. **Shared SQL, only where a source quirk is shared.** For the two lists here, `outline` runs `datasets/hts/models/outline.sql` (see "When SQL is shared").
3. **`load`**, a `models/load.sql` of the list's own. It reads the issuer's file, builds the ladder with a parent one level up for every row, the text and `description_full`, and the release, and fails the run with DuckDB's `error()` on any condition that would publish a wrong release or a wrong text.
4. **`package`** (`models/package.sql`) adds `corpus` in its place after `text_source` and hands off the terminal table.
5. **Export**: `parquet_export@1`, zstd, `order_by: code`, so the bytes before stamping are reproducible. (Schedule B's published file is not reproducible after stamping: see Known gaps.)
6. **`describe`**: `datapackage_describe@1` emits the descriptor from the Parquet, with the nominations and overrides applied.
7. **`check_code_list`**: the contract check above.
8. **`validate`**: `finetype_validate@1` against `schema.finetype.json`.
9. **`stamp_descriptor`**: writes the finished descriptor into the Parquet's footer.

**HTS** (`datasets/hts/`) follows this exactly, in eleven steps: three USITC fetches (`fetch_release_before`, `fetch_hts`, `fetch_release_after`), `outline`, `load`, `package`, `export_hts`, `describe`, `check_code_list`, `validate` and `stamp_descriptor`. Its `load.sql` takes chapter and section titles from the committed `chapters.csv`, derives subchapter rows from the outline, and fills ten-digit lines' rates from the line above that prints them.

**Schedule B** (`datasets/schedule_b/`) adds what its sources need, in seventeen steps: `fetch_schedule_b` and `extract_schedule_b` for Census's zip; `date_schedule_b`, which reads the date the zip records for `econcord.txt`; `fetch_index` for the year's index page, which holds the chapter and section titles; the same three USITC fetches as HTS; `outline` by relative path (`../hts/models/outline.sql`); its own `load` and `package`; and, after `describe`, `fetch_naics` and `derive_join_coverage`, which measure the NAICS join against the published `census/naics_2022` object and write its coverage into the descriptor. It keeps one writer per file: the export, describe and join steps write to `build/stage/`, and `stamp_descriptor` alone writes the published `build/schedule_b.parquet` and `datapackage.json`, so no step rewrites an earlier step's file and leaves it stale.

Each Protocol's SQL has a self-test that runs the shipped models through Python `duckdb` over a reduced copy of the source files and checks the result with `check_code_list.py`: `scripts/test_hts_models.py` and `scripts/test_schedule_b_models.py`, each a CI job. Publishing stays outside the Protocol, in the publish pipeline, which reads its terminal Parquet and descriptor.

### Describing, registering and publishing a list

Beside the Protocol, a new list needs these files and entries. HTS's and Schedule B's are the models to copy.

- **`descriptor.overrides.json`**, the curated half of the descriptor that `describe` lays over finetype's inference. It carries the package's name, title and description; `licenses` (for a US Government work, `other-pd`, titled for the issuer, such as "Public domain (U.S. Census Bureau)", pointing at the issuer's terms page); `sources` (the issuer's page for the list and the file fetched, plus every second source a value comes from, as Schedule B lists USITC's export for its heading text); `contributors` (the issuer as author, any second source as contributor, Meridian as publisher and wrangler); a `description` for every field, saying where its values come from and what NULL means on rows that lack it; `primaryKey` on `code`; and `x-meridian-code-list`. Where an issuer asks for a credit, the description gives it: Schedule B's credits the U.S. Census Bureau and says Census's bulk file carries no endorsement notice. A field whose shape is fixed declares it here as `constraints`, so the published descriptor states the shape the column holds rather than finetype's broader one.
- **`nominations.finetype.json`**, the columns whose finetype label is declared rather than inferred. Nominate a column only where inference gets it wrong, or cannot get it right from the values alone, and give each nomination a reason that is true of the data. Both lists nominate `code` as `geography.transportation.hs_code`: inferred from the values it is `unknown`, since a two-digit chapter and a ten-digit line share no shape. Where the label's pattern does not admit every shape the column holds, `describe` widens it, the overrides declare the column's own shape, and the mismatch is a finetype issue (see Known gaps).
- **`schema.finetype.json`**, the drift contract `validate` holds each run's Parquet to. It enforces the same shapes and lengths the descriptor publishes, so a run whose data has moved away from the published declaration fails instead of publishing it.
- **`x-joins`**, for a join to another hosted dataset, in the form `datasets/edgar_gleif` uses (`cardinality`, `coverage`, `evidence`, `measuredAgainst`, `package`, and a `where` filter naming the rows the join applies to). Schedule B's `naics` joins `census/naics_2022`'s `code` on rows `where level = 'commodity'`. It is not a `schema.foreignKeys` entry, because a foreign key asserts that every value exists on the other side, and some of Census's NAICS codes are not codes of the 2022 edition. `coverage` is not written by hand: `datasets/edgar_gleif/scripts/crosswalk_join.py --write` measures it at build against the other object's hash, and CI's `joins-run-against-their-data` job re-measures it against the published objects.
- **`datasets/registry.json`**: an entry with a new `uid`, the `schema` (namespace) and `name`, the title, the manifest URL and a `protocol` pointer to the `arcform.yaml`. A new issuer gets a held namespace (`kind: held`, the issuer's name, unclaimed), as `usitc` was added for these lists, and the namespace is added to `schemas` and `reserved`. `scripts/test_registry.py` checks the result.
- **`README.md`**: a row in the Datasets table and the address in the list above it.
- **`datasets/label-agreement.json`**: `scripts/check_label_agreement.py` reports every declaration that differs between packages sharing a label or a field name. Give a reason only for a difference that is true of both packages: a different population (Schedule B's `naics` holds Census's own codes and codes ending in X that `census/naics_2022` does not), a different storage choice for one concept (Schedule B's `agricultural` as a boolean, GLEIF's status as text), or one field name used for different concepts (`code`). A declaration looser than the data is a defect, and the fix is to tighten it, not to excuse it. Schedule B's `code` first carried finetype's widened `hs_code` pattern, which admits dotted forms and lengths no level has; it now declares digits only at 2, 4, 6 or 10 digits, bounded 2 to 10, and its line codes (`naics`, `sitc`, `end_use`, `hitech`, the units) declare their fixed shapes, with the drift contract enforcing the same.

**Publishing.** A list is published from its branch, before the branch merges, because CI checks every descriptor and every README row count against the object the descriptor serves, and stays red until the object exists. The publish runs through infra's publish pipeline, which calls `scripts/publish_dataset.py publish` with the Protocol's finished Run; a new address may need infra's pipeline to learn it, in a pull request of infra's own. After the publish, `scripts/publish_dataset.py restamp` writes the `bytes` and `hash` the endpoint serves into the descriptor, and that commit goes on the branch.

## How a release is recorded

Every row carries `release`, and a NULL fails the run, so a reader who pins a snapshot knows which revision of the codes it holds. How the release is found depends on what the issuer publishes.

**USITC's revision bracket.** USITC's export always serves the current revision and takes no release parameter; `currentRelease` returns the revision's name (`2026HTSRev22`). The Protocol fetches `currentRelease` before the export and again after it, and `load` refuses the run if the two names differ. A schedule that changed mid-download is never published under one revision's name. HTS's `release` is that name; Schedule B records the revision its heading text came from in `hts_release`.

**Schedule B's year and file date.** Census's file carries no release name. Each year's schedule sits in its own folder (`/schedules/b/2026/`), and `release` is the year folder and the date the zip records for `econcord.txt`: `2026/2026-07-27`. `archive_extract@1` does not set a member's date on the file it writes, so `date_schedule_b` (`datasets/schedule_b/scripts/member_date.py`) reads the date from the zip's own directory. It is the zip's recorded date, taken as recorded, because that is the one date that does not depend on who reads it. A zip records a member's date as a local date and time with no zone; Census's file records 2026-07-27 12:38, and 16:38 UTC in its extended timestamp, which agree on the day. A tool that shows the extended timestamp in local time far enough east shows the wrong day: `unzip -l` on an Australian clock shows 2026-07-28.

**The year guard.** The year is the Protocol's `year` param, default `2026`, which `load` stamps into `release` and checks against USITC's revision. arc does not substitute a param into an operator's `with:`, so the year appears in four places, and a new January folder changes all four: the param's default, `fetch_schedule_b`'s URL, `fetch_index`'s URL, and the year's index source in `descriptor.overrides.json` (its path and title). `datasets/schedule_b/scripts/test_protocol_year.py` fails in CI when they disagree. `load` also refuses a run whose index page title ("Schedule B 2026 - Browse Chapters") names a different year from the param, so `arc run --param year=2027` with the URLs left on 2026 stops rather than labelling 2026 data as 2027's.

**The edition guard and the January rollover.** USITC serves only its current revision, so around a new HS edition (the next takes effect on 1 January 2027) the Schedule B year and the USITC revision can belong to different editions. Schedule B's `load` refuses the run when the year in `hts_release` is not the Schedule B year, or when, outside chapter 98, USITC names fewer than 99% of the headings, or fewer than 99% of the subheadings, Schedule B implies. Each level is counted on its own, and a prefix USITC only labels counts as not named. A failed run publishes nothing: rerun once both issuers are on the same edition.

## When SQL is shared between lists

SQL is shared between lists only where two lists share a source quirk. Today there is one case: USITC's outline. The HTS export is an indented outline, not a table of parents, and both lists need it: HTS for its own rows, Schedule B for the heading and subheading text Census does not publish. So `datasets/hts/models/outline.sql`, owned by the HTS Protocol because HTS owns the source, reads USITC's JSON into two tables, `hts_outline` (one row per USITC row with its indent chain) and `hts_levels` (every 2, 4, 6, 8 and 10-digit prefix of a numbered code, with its text and `text_source`). The Schedule B Protocol runs the file by its relative path, which arc 0.2.1 and 0.2.2 were both checked to accept, and reads only those two tables, so their columns are a contract between the two Protocols. What only HTS needs, such as subchapter rows, is built in HTS's own `load.sql`, not in the shared file.

Every later list derives its own hierarchy in plain SQL in its own `load.sql`. What is reused across all of them is the contract, the check and this guide. A second shared file appears only when two later lists turn out to share a quirk.

Two other ways were considered and declined. A library of shared DuckDB macros for every code list reads like dbt-utils: functions that are found only by reading them, and not how a Protocol states what it does. New arcform operations, `nest-by-indent` and `nest-by-prefix`, would be the framework's own place for a reusable step, but laid against the seven lists they would not be used regularly: an indent outline serves HTS alone, and the prefix shape differs list by list (NAICS's ranges, SOC's code pattern, FIPS's fixed widths). They do not earn a place in arc's public catalogue.

## The decisions, and why

Made with the operator during the design:

- **Rates verbatim as text, not parsed.** USITC's rates mix percentages, specific duties and compound forms; a parser is later work if it is wanted. Each rate column's description says the rate is as printed and that chapter 99 provisions reached through footnotes (Section 301, 232 and others) can add duties.
- **Every level present as a row.** Both lists carry every chapter, heading and subheading their codes imply, HTS included: USITC prints a single-subdivision heading only through its longer code (heading `0409` appears only as `0409.00.00`, "Natural honey"), and the best-asset rule applies to that gap as much as to Schedule B's.
- **Digits-only `code`, the issuer's dotted form beside it.** Other data carries these codes both ways, and digits-only joins without a rewrite.
- **Parents by prefix, indent only for the path.** `parent_code` is the code one level up, which is always in the ladder, so `depth` is the level's position. USITC's indent is used only to build `description_full`.
- **Sections as columns, not rows.** `section` and `section_title` sit on every row; a Roman numeral is not a code other data carries.
- **Chapter titles from a committed copy.** USITC's export has no chapter rows, and its titles live only in its site's table of contents, which has no stable URL, so HTS carries them as `datasets/hts/chapters.csv`, copied with its date and source. The run fails if a chapter has no title there. HS chapter titles change at HS editions, so recopy the file at each one; the next takes effect on 1 January 2027. Schedule B's chapter titles come from Census's index page, which has a stable URL.
- **Schedule B reads USITC's export itself**, rather than Meridian's published `usitc/hts`, so it runs without the lake, and records the revision it used in `hts_release`.
- **Publish before merge.** CI checks every descriptor and README row count against the served object, so a new list is published from its branch before the branch merges.

Made during the build:

- **A made level's text comes from its outline node.** A level USITC prints only through longer codes takes the text of its node: the lowest outline row that is above every USITC row under the prefix and above none outside it, with the unnumbered headings between the parent's node and it in front (subheading `010511`: "Weighing not more than 185 g: Chickens"). `text_source` is `usitc_derived` in HTS and `usitc` in Schedule B.
- **The code label where no row belongs to the prefix alone.** Where the prefix's rows sit directly under a row shared with other prefixes, or under a numbered line of another prefix (`9824.01.01` above `982402`'s lines), the row is labelled by level and code ("Heading 9903", `text_source = 'meridian_label'`). Most such prefixes are in chapters 98 and 99, such as heading `9903`, but not all: subheading `8424.89`'s lines sit under "Other appliances:" together with `8424.82.00`. A first child's text is never used, because it describes one line and would read as the whole level's. The label applies only when there is no node; a single-line level that shares its node with its made parent keeps USITC's text (`040900`, "Natural honey"). When a made level's parent is a label, its text chain starts at the top of the chapter, so it can repeat a heading its siblings share (`982352`); that heading is a true ancestor.
- **`description_full` from outline rows.** It is the chapter title, then the row's subchapter where its chapter has them, then the defining USITC row's indent chain and its text. Each outline row appears once; two different rows with the same text ("Other:" under "Other:") both stay, because they are different levels of the outline. Subchapter rows (such as "I. CHEMICAL ELEMENTS") follow the chapter title for every row until the next subchapter or the chapter's end.
- **Rate inheritance.** A printed line that prints none of the three rates (general, special, column 2) takes all three from its nearest printed ancestor that prints a general rate, and `rate_from_code` names it, so an analyst joining ten-digit import codes gets the rate that applies. On 2026HTSRev22 only statistical lines do. A line that prints its own rate has `rate_from_code = code`.
- **Chapter 98 in Schedule B takes Census text.** USITC's chapter 98 rows are import provisions (US goods returned and similar); Schedule B's are export provisions (repairs, relief donations, military goods). Matching by prefix would give Schedule B's headings unrelated text, so they take the text of the Census line under them (`census_derived`).
- **Schedule B's multi-line levels are labelled.** A Census-derived level with several Census lines under it is labelled ("Heading 9802", `meridian_label`) rather than taking one line's text, by the same reasoning as the HTS label.
- **The edition guard per level at 99%.** Headings and subheadings are counted separately, so a shortfall at one level is not hidden by the other.
- **`agricultural` is true for `'0'`.** Census's layout prints `0` or `1` without saying which is which. Every line of chapters 1, 2 and 4 is `0`, and every line of chapter 3, fish, which USDA's definition of agricultural products leaves out, is `1`. `load` refuses any third value.
- **Indent is NULL on rows USITC does not print.** Chapters, derived levels and labels have no indent of their own, and the column's description says so.

## Measured on 2026-11-01

Against USITC's `2026HTSRev22` and Census's Schedule B `2026/2026-07-27`:

- `usitc/hts`: 42,036 rows. `census/schedule_b`: 16,727 rows.
- Outside chapter 98, USITC names 1,228 of the 1,229 headings and 5,610 of the 5,612 subheadings Schedule B implies. The heading left is the export-only `8800`; the subheadings left are `880000` and `842489`.
- The NAICS join: 9,464 of 9,779 Schedule B commodity lines carry a code `census/naics_2022` holds.

## Known gaps

- **finetype's `hs_code` pattern lacks the chapter and heading forms.** It admits 6, 8 and 10 digits but not the 2-digit chapter or 4-digit heading, so `describe` widens it and each dataset's overrides declare the shape `code` holds. This needs fixing in finetype.
- **Schedule B's published file is not reproducible byte for byte.** `describe` gives the `sitc` column a different `x-finetype-locale` from one run to the next. The stamped footer carries the descriptor, so the published Parquet's bytes and hash, and the descriptor's, change between runs over the same data. The data bytes before stamping do not move.
- **`describe` records pattern-fit from before an override.** A field whose pattern an override replaces still reports how finetype's own pattern fitted.
- **arc 0.2.1 does not re-run a step when only a param changes.** After changing `--param year=...`, run with `--force`.
- **`crosswalk_join.py` sends no User-Agent**, and openlake's edge refuses Python's default one, so the Schedule B Protocol fetches the NAICS object itself in `fetch_naics` and hands the script the local file.
