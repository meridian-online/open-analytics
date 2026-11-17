# census/naics_2022 onto the code-list contract

## Goal

`census/naics_2022` was hosted before the code-list contract (`docs/code-lists.md`) and carries five columns: `code`, `title`, `level`, `description` (Census's paragraph about the industry) and `corpus`. It has no `code_display`, `depth`, `parent_code`, `description_full`, `release` or `text_source`, and no `x-meridian-code-list` declaration, so `scripts/check_code_list.py` does not check it. This change brings it onto the contract: the Protocol builds the ten core columns, declares its levels, runs the contract check as a step, and is tested by `arc test` against fixture files in CI, as the NAICS index and concordance are.

## Non-goals

- Publishing. The branch builds and checks with `arc run --force` and no credential; the republish from `main` is Hugh's.
- The dataset page on the website, which describes the old columns. It is a change in the web repository, raised separately.
- A `census/naics_2017` code list, or changes to the index's or concordance's tables.

## The ladder

Five levels, Census's names as the table already publishes them: `sector`, `subsector`, `industry_group`, `naics_industry`, `national_industry`, depth 0 to 4. Below the subsector each code is its parent's with one more digit, so the parent is the code with its last digit dropped. A subsector's parent is the sector whose code is its first two digits, or, for the three range sectors, the sector whose range covers them: 311 sits under 31-33, 441 under 44-45, 481 under 48-49. `load` fails the run when a row below the top finds no parent at the level above, or more than one sector covers a subsector.

Every row also carries the code of each level above it (`sector_code`, `subsector_code`, `industry_group_code`, `naics_industry_code`), NULL at the row's own level and below, as both SOC lists do, so a national industry rolls up to its sector in one GROUP BY without walking `parent_code`.

## Calls made on Hugh's behalf

Hugh was away for this build. Each call below can be undone before the republish, and none changes what is built beyond the columns named.

1. **The range sectors keep Census's code, `31-33`.** `code` and `code_display` are both `31-33`, as the table publishes today. The contract's rule is the form other data carries, and data coded at the sector level carries the range as Census prints it (County Business Patterns, BLS's QCEW); finetype's NAICS pattern admits the three ranges and nothing else of that shape. No `31`, `32` or `33` rows are invented, since Census prints none.
2. **`description` becomes Census's title; `title` is kept; the paragraph moves to `definition`.** The contract's `description` is the row's own text, from which `description_full` is built, so it has to be the title. `title` stays as a column after the core, holding the same value, so a query that names it keeps working. Census's paragraph, today's `description`, moves to `definition`: Census calls these texts definitions, and `bls/soc_2018` names the same thing `definition`. This is the one column the contract forces to change meaning: a query that reads `description` for the paragraph gets the title instead. It is carried by a new snapshot, so time travel still reaches the old shape, and the descriptor's description says what moved.
3. **`corpus` follows the contract**: `description_full`, lowercased and accent-stripped, as every other code list's is. Today's corpus carries the paragraph's words too; the in-browser search on the dataset page then matches names only, and free-text business descriptions are what `census/naics_2022_titles`' twenty thousand index entries are for.
4. **`release` is `2022`, the edition, and `text_source` is `census`**, as the index's and concordance's are, for the same reason: Census's workbook states no revision.
5. **The publish replaces the table whole** (`on_schema_change: replace`). arc's publish refuses a reordered table, and the contract puts ten columns first. A replace shows every row deleted and reinserted in the change feed. The lake's compatibility views `naics` and `source.naics` are `SELECT *` over the table and carry the new columns. The setting comes out again in the change after the republish, so a later accidental reorder is refused.
6. **The Protocol takes the one-writer layout** the code lists after it use: the export and describe write to `build/stage/`, and the stamp alone writes `build/naics.parquet` and `build/datapackage.json`. It joins `scripts/test_manifest_one_writer.py`'s list.

## Done on this branch when

- `arc run --force` with no credential builds the table, `check_code_list` passes on it, and `check_before_publish` passes against the live lake's descriptors.
- `datasets/naics/tests.yaml` passes under `arc test` and is in CI's `protocol-tests` matrix; the index's and concordance's `arc test` still pass, since they run this Protocol's `load.sql`.
- `scripts/check_lake_shape.py` against the live table reports the expected reorder and nothing else, so the replace is the only thing the publish needs.
- Schedule B's NAICS join, measured against the built table, gives the coverage it gives against the live one, since `code` does not change.
- `docs/code-lists.md` describes the Protocol and the calls above.
