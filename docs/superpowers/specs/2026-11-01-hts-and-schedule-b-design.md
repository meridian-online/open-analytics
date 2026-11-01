# US tariff and export codes: `usitc/hts` and `census/schedule_b`

Design, 2026-11-01. Brainstormed with Hugh; his rulings are marked as his, and every other choice is marked as a call made on his behalf.

## Intent

Meridian hosts the Harmonized Tariff Schedule of the United States, issued by the U.S. International Trade Commission (USITC), at `usitc/hts`, and the Census Bureau's Schedule B export codes at `census/schedule_b`. They are the first two code lists Meridian hosts, and five more follow on the same pattern (HCPCS Level II, the NDC directory, SOC, FIPS state and county codes, and OurAirports' airports).

The reader is an analyst who searches for an HTS or HS code and would rather query a table with SQL than read a PDF or click through a lookup site. Measured demand (DataForSEO, US monthly, July 2026): "hts code" 22,200, "hs code" 14,800, "harmonized tariff schedule" 6,600, "schedule b codes" 590.

**Hugh's ruling on what a hosted dataset is.** "Any data analysis we prepare shouldn't be bound by the limitations of the source data - but prepared to present the best data asset for insight to an analyst." The preparation is done in the Protocol, in SQL on DuckDB wherever SQL can do it. Two limits hold the rule to its sources: every value brought in from a second source says where it came from, and a gap is filled only from a source cleared for redistribution.

## Goals

1. Two Protocols, `datasets/hts/` and `datasets/schedule_b/`, each fetching its issuer's published file and producing one Parquet that `arc run` rebuilds from the top.
2. Each dataset carries the full ladder of its hierarchy as rows (chapter, heading, subheading, and the national lines below them), each row with its parent, so an analyst can roll a ten-digit code up to its chapter without a second source.
3. `usitc/hts` carries each line's duty rates, units and footnotes as USITC prints them.
4. Each snapshot records the issuer's release, so a reader who pins a snapshot knows which revision of the codes it holds.
5. A code-list contract that the five later lists meet: shared core columns, a check script that holds a built list to them, and a written guide.
6. Descriptors, finetype contracts, registry entries, the new held namespace `usitc`, and the README catalogue rows, all passing this repository's checks.

## Non-goals

- The R2 publish, the catalogue entries `meridian.usitc.hts` and `meridian.census.schedule_b`, and the dataset pages on meridian.online. They follow this pull request, in infra and web.
- Parsing duty rates into numbers. Rates stay verbatim text; a rate parser is later work, if it is wanted.
- New arcform operations. Hugh asked whether `nest-by-indent` and `nest-by-prefix` would be used regularly. Laid against the seven lists, they would not: an indent outline serves HTS alone, and the prefix shape varies list by list (NAICS ranges, SOC's code pattern, FIPS's fixed widths). They do not earn a place in arc's public catalogue.
- A library of shared macros for all code lists. Hugh compared that to dbt-utils, a set of functions that are found only by reading them. SQL is shared only where two lists share a source quirk, as HTS and Schedule B do (below).
- The WCO's HS nomenclature and UN Comtrade. Their terms do not permit redistribution. The HS six-digit layer is taken from the US Government publications only.

## Anti-goals

- **A dataset shaped like the issuer's file.** Schedule B shipped as its ten-digit lines with no headings would technically work and fail Hugh's ruling.
- **Text whose source is hidden.** A heading row whose text came from USITC must say so on the row and in the descriptor, not read as Census's own words.
- **A rate that misleads.** The printed general rate is often not what an importer pays: Section 301, 232 and other additional duties sit in chapter 99 and are reached through footnotes. A rate column that does not say so is a defect.
- **A snapshot with no release.** A row whose `release` is NULL fails the run.

## The sources, as measured on 2026-11-01

**USITC.** `https://hts.usitc.gov/reststop/exportList?from=0100&to=9999&format=JSON&styles=false` returns the whole schedule as one JSON array, 12.6 MB: 35,805 rows, of which 29,861 carry a code (`htsno`, dotted, 4, 6, 8 or 10 digits, all distinct) and 5,944 are unnumbered text headings such as "Horses:" that group the rows beneath them. Each row has `indent` (a string; `"03"` and `"04"` occur and read as 3 and 4), `description`, `superior`, `units` (a list), `general`, `special`, `other` (the column 2 rate), `footnotes` (a list of structs), `quotaQuantity` and `additionalDuties`. 13,827 numbered rows print a general rate; ten-digit statistical lines mostly print none and take the rate of their eight-digit line. The export always serves the current revision and takes no release parameter. `https://hts.usitc.gov/reststop/currentRelease` returns its name: `{"name": "2026HTSRev22", "description": "2026 HTS Revision 22", ...}`. `releaseList` lists every revision. A single-subdivision heading is printed as one row with the longer code (heading 0409 appears only as `0409.00.00`, "Natural honey"), so the numbered rows alone do not hold every heading and subheading.

**Census.** `https://www.census.gov/foreign-trade/schedules/b/2026/sbsdf.zip` (934,718 bytes, `Last-Modified: Thu, 22 Oct 2026`) holds `econcord.txt`, the Schedule B export codes, dated 2026-07-28 inside the zip, and `exp-stru.txt`, its layout. `econcord.txt` is fixed-width with CRLF line ends: 9,779 rows, every one a distinct ten-digit code. The layout: 1–10 code, 15–65 short description, 70–219 long description, 225–227 first unit, 233–235 second unit, 241–245 SITC Rev. 4, 251–255 end-use class, 261 agricultural flag, 266–271 NAICS, 277–278 high-technology class. The file has no chapter, heading or subheading rows and no release name; the year is the folder (`2026`) and the date is the file's. `https://www.census.gov/foreign-trade/schedules/b/2026/index.html` names all 97 chapters ("Chapter 1 : Live Animals") and 22 sections ("Section 1 - Live Animals; Animal Products") in its HTML. The year folder moves each January.

**Both are US Government works in the public domain** (17 U.S.C. §105). Census asks API users to display a no-endorsement notice; the bulk file carries none, and the dataset page credits Census.

**The measurement behind Schedule B's ladder.** For each four- and six-digit prefix of a Schedule B code, the USITC row that names it is the shallowest numbered row under that prefix, first in file order. Run in DuckDB over the two files above, outside chapter 98, that names 1,228 of the 1,229 headings and 5,611 of the 5,612 subheadings Schedule B implies; the one of each left is `8800` and `880000`, export-only codes. Chapter 98 is excluded because the two schedules use it differently: USITC's `9801`–`9803` rows cover US goods returned and similar import provisions, while Schedule B's seven chapter 98 lines are export provisions (repairs of imported articles, relief donations, military goods). Matching by prefix would give Schedule B's chapter 98 headings USITC's unrelated text, so they take their text from Schedule B's own lines.

## Approaches considered

- **A. Shared DuckDB macros for every code list, with a contract check.** Proposed first. Hugh declined it: a suite of macros is not the Meridian framework, and it reads like dbt-utils, functions found only by reading them.
- **B. Two arcform operations, `nest-by-indent` and `nest-by-prefix`.** Proposed second, since arcform's operation catalogue is the framework's own place for a reusable SQL step. Hugh asked whether they would be used regularly; measured against the seven lists, they would not (see Non-goals).
- **C. One shared SQL file where two lists share a source quirk, and a contract for the rest.** Hugh's compromise, and the approach taken. HTS and Schedule B both read USITC's outline, so one SQL file serves both. Each later list derives its own hierarchy in plain SQL in its own `load.sql`, and what is reused across all of them is the contract, its check and the guide. A second shared file appears only when two later lists turn out to share a quirk.

## The code-list contract

Every hosted code list leads with these columns, in this order, and adds its own after them.

| Column | Type | Meaning |
|---|---|---|
| `code` | VARCHAR, primary key | The code in the form other data carries it: digits only for HTS and Schedule B (`0101210010`, chapters `01`). |
| `code_display` | VARCHAR | The code as the issuer prints it (`0101.21.00.10` for HTS; `0101.21.0000` for Schedule B). |
| `level` | VARCHAR, closed set per list | The level's name. |
| `depth` | INTEGER | 0 for the top level, one more per level down. |
| `parent_code` | VARCHAR | The `code` of the row above; NULL only at depth 0. |
| `description` | VARCHAR | The row's own text. |
| `description_full` | VARCHAR | The row's text with its ancestors' in front, joined with ` › `, so a line printed as "Other" reads in context. |
| `release` | VARCHAR, not null | The issuer's release this row was built from. |
| `text_source` | VARCHAR, closed set per list | Whose text `description` is, for lists that fill gaps from a second source. |
| `corpus` | VARCHAR | Lowercased, accent-stripped `description_full`, the in-browser search field the NAICS dataset already carries. |

`scripts/check_code_list.py` holds a built Parquet to the contract. It fails on: a missing core column or one out of order; a repeated or NULL `code`; a `parent_code` that is not a `code` in the same file; a NULL `parent_code` below depth 0; a child whose `depth` is not its parent's plus one; a `level` outside the list's declared set; a NULL `release` or more than one distinct `release` in the file; a NULL `description`. It runs as an `op: uv@1` step in each code-list Protocol, pinned by sha256 as the crosswalk Protocol pins `stamp_descriptor.py`, and in CI against each published code list. A self-test runs it on deliberately broken fixtures, as the repository's other checks do. Which datasets are code lists is read from their descriptors, not from a list in the script.

`docs/code-lists.md` is the guide: the contract, the check, how a new list's Protocol is laid out, how its release is recorded, and when SQL is shared between lists.

## `usitc/hts`

**Protocol, `datasets/hts/arcform.yaml`.** Steps, in order:

1. `fetch_release_before`: `http_fetch@1` of `currentRelease` to `build/release_before.json`.
2. `fetch_hts`: `http_fetch@1` of the `exportList` URL above to `build/hts.json`.
3. `fetch_release_after`: `http_fetch@1` of `currentRelease` again, to `build/release_after.json`, ordered after `fetch_hts`.
4. `outline`: `sql: models/outline.sql`, the shared file (below), building `hts_outline` from `build/hts.json`.
5. `load`: `sql: models/load.sql`. Fails the run (DuckDB `error()`) if the two release files name different revisions. Builds the ladder and the HTS columns.
6. `package`, `export_hts` (`parquet_export@1`, `order_by: code`), `describe` (`datapackage_describe@1`), `check_code_list` (`uv@1`), `validate` (`finetype_validate@1`), `stamp_descriptor`, as the NAICS Protocol has them.

**The shared outline, `datasets/hts/models/outline.sql`.** HTS owns the file; the Schedule B Protocol runs it by its relative path (`../hts/models/outline.sql`), which arc 0.2.2 was checked to accept on 2026-11-01. It reads USITC's JSON and makes one table, `hts_outline`, with one row per USITC row in file order: `seq`, `code` (digits only, NULL for an unnumbered heading), `code_display`, `indent` (integer), `description`, and `context`, the descriptions of the unnumbered and numbered rows above it on the indent chain, nearest last. From that it makes `hts_levels`: for every prefix of length 2, 4, 6, 8 and 10 that a numbered row's code has, the shallowest numbered row under that prefix, first by `indent` then by `seq`, with its description and context. Both Protocols build their ladders from `hts_levels`.

**Rows.** One row per code in the ladder: every numbered USITC code, plus a row for each chapter, heading and subheading that USITC prints only through a longer code. Levels: `chapter` (2 digits), `heading` (4), `subheading` (6), `tariff_line` (8), `statistical_line` (10). `parent_code` is the prefix one level up that is in the ladder: a ten-digit line whose eight-digit prefix is not a row hangs from its six-digit subheading, and so on up.

**`description` and `description_full`.** A row's `description` is USITC's text for it. A row made for a prefix USITC prints only through a longer code takes that longer row's text, with the unnumbered heading text on its chain in front where the subheading's meaning needs it ("Weighing not more than 185 g: Chickens"). `description_full` walks the parents and includes the unnumbered headings in between.

**`text_source`.** `usitc` for a row USITC prints with its own code; `usitc_derived` for a level made from a longer code's text; `usitc_toc` for a chapter.

**Chapter titles.** USITC's export has no chapter rows, and its chapter titles live only in its site's table of contents. The Protocol carries them as `datasets/hts/chapters.csv`, the 99 chapter titles and their sections as USITC's table of contents lists them, committed with the date and URL it was copied from. The run fails if a chapter in the data has no title in the file. HS chapter titles change at HS editions; the next takes effect on 1 January 2027, and the guide says to recopy the file then.

**Sections** are not rows; every row carries `section` (Roman numeral) and `section_title` from the same file.

**HTS columns after the contract.** `indent` (USITC's, as an integer); `units` (VARCHAR[]); `general_rate`, `special_rate`, `column2_rate` (VARCHAR, verbatim); `rate_from_code`; `footnotes` (the footnote texts, VARCHAR[]); `quota_quantity`; `additional_duties`. A ten-digit line that prints no rate takes the rates of the line above it that does, and `rate_from_code` names that line, so an analyst joining ten-digit import codes gets the rate that applies; a line that prints its own rate has `rate_from_code = code`. Each rate column's description says the rate is as printed, that chapter 99 provisions reached through footnotes can add duties, and where to read them.

**`release`** is the `name` from `currentRelease`, for example `2026HTSRev22`.

## `census/schedule_b`

**Protocol, `datasets/schedule_b/arcform.yaml`.** Steps:

1. `fetch_schedule_b`: `http_fetch@1` of `sbsdf.zip`; `archive_extract@1` to `build/sbsdf/`.
2. `fetch_index`: `http_fetch@1` of the year's `index.html` to `build/schedule_b_index.html`.
3. `fetch_hts`, with the HTS Protocol's release bracket: the same three USITC fetches as above.
4. `outline`: `sql: ../hts/models/outline.sql`.
5. `load`: `sql: models/load.sql`. Reads `econcord.txt` by fixed positions, the chapter and section titles from the index HTML by regular expression in SQL, and builds the ladder.
6. `package`, `export_schedule_b`, `describe`, `check_code_list`, `validate`, `stamp_descriptor`.

The year in both Census URLs is a Protocol parameter, defaulting to the current year folder (`2026`); a new January folder is a one-line change.

**Rows.** The 9,779 ten-digit lines, plus a row for each chapter, heading and subheading their codes imply. Levels: `chapter`, `heading`, `subheading`, `commodity` (the ten-digit Schedule B number). No eight-digit level: Schedule B has none.

**Text.** A ten-digit line's `description` is Census's long description, with the short one in `description_short`. A chapter's is Census's title from the index page. A heading's or subheading's is the USITC text `hts_levels` names, except in chapter 98, where USITC's text describes other provisions; in chapter 98, and for a prefix USITC does not name, it is the text of the first Schedule B line under it. `text_source`: `census` (ten-digit lines and chapters), `usitc` (headings and subheadings from USITC), `census_derived` (chapter 98's upper levels and the export-only prefixes).

**Schedule B columns after the contract.** `description_short`; `unit_1`, `unit_2`; `sitc` (SITC Rev. 4); `end_use`; `agricultural` (Census's flag, as a boolean if its values are a closed pair, else as printed); `naics` (six digits); `hitech`; `section`, `section_title`; `hts_release`, the USITC revision the heading and subheading text came from. The upper rows carry NULL in the ten-digit-only columns.

**`release`** is `econcord.txt`'s date as it stands in the zip, as an ISO date (`2026-07-28`), with the year folder in front: `2026/2026-07-28`. If `archive_extract@1` does not carry a member's date through, the release is the zip's `Last-Modified` date and the guide says so.

**The NAICS join.** `naics` is declared as a foreign key to `census/naics_2022`'s `code`, in the repository's existing cross-package form (`x-package`, `x-status`, `x-evidence`), with its resolution measured at build: how many Schedule B lines carry a NAICS code that `naics_2022` holds.

## Descriptors and registry

- Each dataset has `datapackage.json`, `descriptor.overrides.json`, `nominations.finetype.json` and `schema.finetype.json`, made the way NAICS's are.
- `licenses`: `other-pd`, titled for USITC and for Census, each pointing at the issuer's terms page. `sources`: USITC's schedule and its download page for HTS; Census's Schedule B page and the year's index for Schedule B, plus USITC's export as the source of the heading and subheading text.
- The `code` columns are nominated as finetype's `geography.transportation.hs_code` if finetype does not infer it and its pattern admits the digits-only ten-digit form; if its pattern does not, the column is nominated as it infers and the mismatch is recorded for a finetype issue.
- `datasets/registry.json`: a held namespace `usitc` (issuer "U.S. International Trade Commission", unclaimed), `usitc` added to `schemas` and `reserved`, and two entries with new `uid`s, `usitc`/`hts` and `census`/`schedule_b`, each with its `protocol` pointer. `scripts/test_registry.py` passes over the result.
- README: two rows in the Datasets table and the address list.

## Calls made on Hugh's behalf

- Digits-only `code` as the key, with the dotted form beside it: other data carries HTS codes both ways, and digits-only joins without a rewrite.
- Every level present as a row in both datasets, HTS included, using the same rule that fills Schedule B's gaps: Hugh's ruling applies to HTS's single-subdivision headings as much as to Schedule B's.
- Parents by code prefix, with indent used only for `description_full`.
- Sections as columns, not rows: a Roman numeral is not a code that other data carries.
- Ten-digit HTS lines inherit the rate of the line that prints it, with `rate_from_code` naming that line.
- Chapter titles for HTS from a committed copy of USITC's table of contents rather than its site script, which has no stable URL; Schedule B's from Census's index page, which does.
- The Schedule B Protocol reads USITC's export itself rather than Meridian's published `usitc/hts`, so it runs without the lake, and records the revision it used in `hts_release`.
- The shared outline file lives in the HTS Protocol, which owns the source it reads.
- Protocol directories `datasets/hts/` and `datasets/schedule_b/`, after each dataset's name.

## Left to the builder

- The exact SQL for the outline chain and the ladder, the regular expressions for the index page, and the fixed-width read.
- Column descriptions, the descriptor prose and the `README.md` beside each Protocol.
- The finetype contract's enforced keywords, made the way NAICS's is.
- How CI selects code-list datasets for `check_code_list.py`, within the rule that it reads the descriptors.
