-- Stage ONS's three SOC 2020 workbooks. Run by the `load` step (needs DuckDB's excel
-- extension); paths resolve from the Protocol directory.
--
-- Each workbook is fetched to a fixed path under build/ons/ and is read as text, with no
-- header row, from an explicit range. Measured 2026-11-03:
--   Volume 1 (volume1.xlsx), sheet "SOC2020 descriptions": row 1 is the headers, A1:K1,
--     data from row 2 (551 groups). Columns: A UNIQUE ID, B major group, C sub-major group,
--     D minor group, E unit group (each group sits in the one column of its level, the other
--     three hold the string '<blank>'; codes are numbers, so they are read as text), F title
--     (header "SOC2020\nGroup Title"), G the groups classified within, H group description,
--     I typical entry routes, J tasks, K related job titles. Empty cells hold '<blank>'. J
--     and K hold one `~ `-bulleted item per line; they are set only on unit groups.
--   Coding index (coding_index.xlsx), sheet "SOC2020 coding index": row 1 is the headers,
--     A1:V1 (W and X are unnamed '*' columns), data from row 2 (32,670 entries); the
--     sheet's formatting runs to row 1,048,575, so empty rows are dropped. Columns: A
--     UNIQUE ID, B RECNO, C D, D SOC_90, E SOC_2000, F SOC_2010, G SOC_2020, H SOC_2020_ext,
--     I INDEXOCC, J ADD, K IND, L SEE, M the ISCO-08 code, N VERSNO, O CHANGE, P the
--     extended group title, Q to U earlier editions' and ISCO-08's unit group titles,
--     V the title in natural word order. ONS writes '}}}', '}}}}' or '}}}}/}}' where a code
--     does not apply; staging reads those as NULL. Sheet "INFO", cell B2, reads "Version
--     <n> Of The Standard Occupational Classification 2020 Index".
--   Relationship tables (relationship_2010_2020.xlsx), sheet "soc2010-soc2020-map_Unit":
--     row 1 is the headers, A1:J1, data from row 2. A 2010 unit group opens with a "Total
--     sample" row (A "1115: <title>", B 'Total sample', C, E, G, I the four base counts),
--     then one row per 2020 unit group it was recoded to (A empty, B "1111: <title>", D,
--     F, H, J the four shares). The sheet ends with a stray row whose B is an empty string.
-- The header rows' text is checked before any data is read, so a sheet whose layout has
-- moved fails the run instead of staging the wrong cells.
--
-- Staging tables: ons_groups, ons_index, ons_relationship, and ons_release, one row holding
-- the release of each source, which `code_list.sql` and the other models stamp on their
-- rows. Each release is read from the `url:` line of the `.arcmeta` sidecar `http_fetch@1`
-- writes beside the workbook, so it is the one in the URL actually fetched. A missing
-- sidecar, or a URL that does not carry its date, fails the run.
INSTALL excel; LOAD excel;

CREATE OR REPLACE TEMP MACRO ons_cell(x) AS nullif(nullif(trim(x), '<blank>'), '');
CREATE OR REPLACE TEMP MACRO ons_code(x) AS
  CASE WHEN regexp_matches(trim(x), '^[}]+(/[}]+)?$') THEN NULL ELSE nullif(trim(x), '') END;

-- The header rows, each compared with the text ONS prints.
SELECT CASE WHEN [*COLUMNS(*)] IS NOT DISTINCT FROM
         ['UNIQUE ID', 'SOC2020 Major Group', 'SOC2020 Sub-Major Group', 'SOC2020 Minor Group',
          'SOC2020 Unit Group', E'SOC2020\nGroup Title', 'Groups Classified Within Sub-Groups',
          'Group Description', 'Typical Entry Routes And Associated Qualifications', 'Tasks',
          'Related Job Titles'] THEN 1
       ELSE error('ONS Volume 1 header row has moved: expected UNIQUE ID, SOC2020 Major Group ... Related Job Titles in A1:K1, found '
                  || array_to_string(list_transform([*COLUMNS(*)], x -> coalesce(x, 'NULL')), ', ')) END
FROM read_xlsx('build/ons/volume1.xlsx', sheet = 'SOC2020 descriptions', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A1:K1');

SELECT CASE WHEN [*COLUMNS(*)] IS NOT DISTINCT FROM
         ['UNIQUE ID', 'RECNO', 'D', 'SOC_90', 'SOC_2000', 'SOC_2010', 'SOC_2020', 'SOC_2020_ext',
          'INDEXOCC', 'ADD', 'IND', 'SEE', 'ISCO-08 code based on SOC2020', 'VERSNO', 'CHANGE',
          'SOC2020_ext_SUG_title', 'SOC90_unit_group_title', 'SOC2000_unit_group_title',
          'SOC2010_unit_group_title', 'SOC2020_unit_group_title', 'ISCO08_unit_group_title',
          'INDEXOCC_-_natural_word_order'] THEN 1
       ELSE error('ONS coding index header row has moved: expected UNIQUE ID, RECNO, D ... INDEXOCC_-_natural_word_order in A1:V1, found '
                  || array_to_string(list_transform([*COLUMNS(*)], x -> coalesce(x, 'NULL')), ', ')) END
FROM read_xlsx('build/ons/coding_index.xlsx', sheet = 'SOC2020 coding index', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A1:V1');

SELECT CASE WHEN [*COLUMNS(*)] IS NOT DISTINCT FROM
         ['SOC2010', 'SOC2020', 'men_lfs_base_2010', 'men_lfs_perc_2020', 'men_census_base_2010',
          'men_census_perc_2020', 'women_lfs_base_2010', 'women_lfs_perc_2020',
          'women_census_base_2010', 'women_census_perc_2020'] THEN 1
       ELSE error('ONS relationship tables header row has moved: expected SOC2010, SOC2020, men_lfs_base_2010 ... women_census_perc_2020 in A1:J1, found '
                  || array_to_string(list_transform([*COLUMNS(*)], x -> coalesce(x, 'NULL')), ', ')) END
FROM read_xlsx('build/ons/relationship_2010_2020.xlsx', sheet = 'soc2010-soc2020-map_Unit', header = false,
               all_varchar = true, stop_at_empty = false, range = 'A1:J1');

-- The releases. Volume 1 and the coding index carry the date they were issued in their
-- file names (`...excel20260827.xlsx`; a revision gets a new URL); the index's version is
-- on its INFO sheet. The relationship tables carry the month and year at the end of the
-- name (`...july2021.xlsx`).
CREATE OR REPLACE TABLE ons_release AS
WITH sidecars AS (
  SELECT
    (SELECT regexp_extract(content, '(?m)^url:[ \t]*(\S+)', 1) FROM read_text('build/ons/volume1.xlsx.arcmeta')) AS volume1_url,
    (SELECT regexp_extract(content, '(?m)^url:[ \t]*(\S+)', 1) FROM read_text('build/ons/coding_index.xlsx.arcmeta')) AS index_url,
    (SELECT regexp_extract(content, '(?m)^url:[ \t]*(\S+)', 1) FROM read_text('build/ons/relationship_2010_2020.xlsx.arcmeta')) AS relationship_url,
    (SELECT B FROM read_xlsx('build/ons/coding_index.xlsx', sheet = 'INFO', header = false, all_varchar = true,
                             stop_at_empty = false, range = 'A2:B2')) AS info_line
), parsed AS (
  SELECT *,
    try_strptime(regexp_extract(volume1_url, 'excel([0-9]{8})', 1), '%Y%m%d') AS volume1_date,
    try_strptime(regexp_extract(index_url, 'excel([0-9]{8})', 1), '%Y%m%d') AS index_date,
    regexp_extract(info_line, '^Version ([0-9]+) Of The Standard Occupational Classification 2020 Index', 1) AS index_version,
    list_position(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                   'september', 'october', 'november', 'december'],
                  regexp_extract(lower(relationship_url), '(january|february|march|april|may|june|july|august|september|october|november|december)([0-9]{4})\.xlsx$', 1)) AS relationship_month,
    regexp_extract(relationship_url, '([0-9]{4})\.xlsx$', 1) AS relationship_year
  FROM sidecars
)
SELECT
  CASE WHEN volume1_date IS NULL
       THEN error('ONS Volume 1 release: its sidecar url does not carry a date as "excel<yyyymmdd>": ' || coalesce(volume1_url, 'NULL'))
       ELSE '2020/' || strftime(volume1_date, '%Y-%m-%d') END AS code_list_release,
  CASE WHEN index_version = ''
       THEN error('ONS coding index version: INFO cell B2 is not "Version <n> Of The Standard Occupational Classification 2020 Index": ' || coalesce(info_line, 'NULL'))
       WHEN index_date IS NULL
       THEN error('ONS coding index release: its sidecar url does not carry a date as "excel<yyyymmdd>": ' || coalesce(index_url, 'NULL'))
       ELSE 'v' || index_version || '/' || strftime(index_date, '%Y-%m-%d') END AS index_release,
  CASE WHEN relationship_month IS NULL OR relationship_month = 0
       THEN error('ONS relationship tables release: its sidecar url does not end in "<month><yyyy>.xlsx": ' || coalesce(relationship_url, 'NULL'))
       ELSE relationship_year || '-' || lpad(relationship_month::VARCHAR, 2, '0') END AS crosswalk_release
FROM parsed;

-- Volume 1's groups. Each row carries exactly one code, in the column of its level, and
-- the code has that level's width. A repeated code fails the run.
CREATE OR REPLACE TABLE ons_groups AS
WITH cells AS (
  SELECT
    ons_cell(B) AS major, ons_cell(C) AS sub_major, ons_cell(D) AS minor, ons_cell(E) AS unit,
    ons_cell(F) AS title, ons_cell(H) AS group_description, ons_cell(I) AS entry_routes,
    ons_cell(J) AS tasks, ons_cell(K) AS related_job_titles, *
  FROM read_xlsx('build/ons/volume1.xlsx', sheet = 'SOC2020 descriptions', header = false, all_varchar = true,
                 stop_at_empty = false, range = 'A2:K')
  WHERE coalesce(A, B, C, D, E, F, G, H, I, J, K) IS NOT NULL
), coded AS (
  SELECT *,
    CASE WHEN (major IS NOT NULL)::INT + (sub_major IS NOT NULL)::INT + (minor IS NOT NULL)::INT + (unit IS NOT NULL)::INT = 1
         THEN coalesce(major, sub_major, minor, unit)
         ELSE error('ONS Volume 1 row "' || coalesce(title, 'NULL') || '" does not hold exactly one code among its major, sub-major, minor and unit group cells') END AS code,
    CASE WHEN major IS NOT NULL THEN 'major' WHEN sub_major IS NOT NULL THEN 'sub_major'
         WHEN minor IS NOT NULL THEN 'minor' ELSE 'unit' END AS level
  FROM cells
), checked AS (
  SELECT *,
    CASE WHEN regexp_matches(code, CASE level WHEN 'major' THEN '^[0-9]$' WHEN 'sub_major' THEN '^[0-9]{2}$'
                                              WHEN 'minor' THEN '^[0-9]{3}$' ELSE '^[0-9]{4}$' END)
         THEN code
         ELSE error('ONS Volume 1 ' || level || ' group code is not its level''s digits: ' || code) END AS valid_code,
    count(*) OVER (PARTITION BY code) AS times
  FROM coded
)
SELECT
  CASE WHEN times > 1 THEN error('ONS Volume 1 code repeated: ' || valid_code) ELSE valid_code END AS code,
  level,
  coalesce(title, error('ONS Volume 1 group ' || valid_code || ' has no title')) AS title,
  group_description,
  entry_routes,
  CASE WHEN tasks IS NULL THEN NULL::VARCHAR[]
       WHEN len(list_filter(regexp_split_to_array(tasks, '\r?\n'), l -> trim(l) <> '' AND NOT regexp_matches(l, '^\s*~'))) > 0
       THEN error('ONS Volume 1 tasks of ' || valid_code || ' are not one "~ " item per line')
       ELSE list_filter(list_transform(regexp_split_to_array(tasks, '\r?\n'), l -> trim(regexp_replace(l, '^\s*~\s*', ''))), l -> l <> '') END AS tasks,
  CASE WHEN related_job_titles IS NULL THEN NULL::VARCHAR[]
       WHEN len(list_filter(regexp_split_to_array(related_job_titles, '\r?\n'), l -> trim(l) <> '' AND NOT regexp_matches(l, '^\s*~'))) > 0
       THEN error('ONS Volume 1 related job titles of ' || valid_code || ' are not one "~ " item per line')
       ELSE list_filter(list_transform(regexp_split_to_array(related_job_titles, '\r?\n'), l -> trim(regexp_replace(l, '^\s*~\s*', ''))), l -> l <> '') END AS related_job_titles
FROM checked;

-- The coding index's coded entries. A row with no unit group code is left out only if it
-- is a cross-reference: its SOC_2020 cell is ONS's '}}}}' and its SEE cell starts "see".
-- Any other row without a four-digit code fails the run.
CREATE OR REPLACE TABLE ons_index AS
WITH src AS (
  SELECT *
  FROM read_xlsx('build/ons/coding_index.xlsx', sheet = 'SOC2020 coding index', header = false, all_varchar = true,
                 stop_at_empty = false, range = 'A2:X')
  WHERE coalesce(A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V) IS NOT NULL
), shaped AS (
  SELECT *,
    CASE WHEN trim(G) ~ '^[0-9]{4}$' THEN trim(G)
         WHEN trim(G) = '}}}}' AND regexp_matches(coalesce(trim(L), ''), '^(?i)see')
         THEN NULL
         ELSE error('ONS coding index entry ' || coalesce(A, 'NULL') || ' ("' || coalesce(I, 'NULL')
                    || '") has no unit group code and is not a cross-reference: SOC_2020 ' || coalesce(G, 'NULL')
                    || ', SEE ' || coalesce(L, 'NULL')) END AS unit_code
  FROM src
)
SELECT
  coalesce(nullif(trim(A), ''), error('ONS coding index entry has no UNIQUE ID: ' || coalesce(I, 'NULL'))) AS entry_id,
  coalesce(nullif(trim(I), ''), error('ONS coding index entry ' || A || ' has no title')) AS title,
  nullif(trim(J), '') AS additional_qualifier,
  nullif(trim(K), '') AS industry_qualifier,
  nullif(trim(V), '') AS title_natural,
  unit_code AS code,
  nullif(trim(C), '') AS default_flag,
  ons_code(H) AS soc_2020_ext,
  ons_code(P) AS soc_2020_ext_title,
  ons_code(F) AS soc_2010,
  ons_code(E) AS soc_2000,
  ons_code(D) AS soc_1990,
  ons_code(M) AS isco_08,
  nullif(trim(N), '') AS version_added_or_amended,
  nullif(trim(O), '') AS change
FROM shaped
WHERE unit_code IS NOT NULL;

-- The unit-group sheet of the relationship tables, one row per pairing. A 2010 group's
-- "Total sample" row gives its four base counts, carried onto each of its pairings.
CREATE OR REPLACE TABLE ons_relationship AS
WITH src AS (
  SELECT row_number() OVER () AS rn, *
  FROM read_xlsx('build/ons/relationship_2010_2020.xlsx', sheet = 'soc2010-soc2020-map_Unit', header = false,
                 all_varchar = true, stop_at_empty = false, range = 'A2:J')
), kept AS (
  SELECT rn, nullif(trim(A), '') AS a, nullif(trim(B), '') AS b, C, D, E, F, G, H, I, J
  FROM src
  WHERE coalesce(nullif(trim(A), ''), nullif(trim(B), ''), C, D, E, F, G, H, I, J) IS NOT NULL
), filled AS (
  SELECT *,
    last_value(CASE WHEN b = 'Total sample' THEN a END IGNORE NULLS) OVER w AS group_2010,
    last_value(CASE WHEN b = 'Total sample' THEN C END IGNORE NULLS) OVER w AS men_lfs_base,
    last_value(CASE WHEN b = 'Total sample' THEN E END IGNORE NULLS) OVER w AS men_census_base,
    last_value(CASE WHEN b = 'Total sample' THEN G END IGNORE NULLS) OVER w AS women_lfs_base,
    last_value(CASE WHEN b = 'Total sample' THEN I END IGNORE NULLS) OVER w AS women_census_base
  FROM kept
  WINDOW w AS (ORDER BY rn)
), pairings AS (
  SELECT * FROM filled WHERE b IS DISTINCT FROM 'Total sample'
)
SELECT
  regexp_extract(CASE WHEN group_2010 ~ '^[0-9]{4}: .+' THEN group_2010
                      ELSE error('ONS relationship row ' || (rn + 1) || ' is not under a 2010 unit group "<code>: <title>" with a "Total sample" row') END,
                 '^([0-9]{4}): (.+)$', 1) AS code_2010,
  regexp_extract(group_2010, '^([0-9]{4}): (.+)$', 2) AS title_2010,
  regexp_extract(CASE WHEN b ~ '^[0-9]{4}: .+' THEN b
                      ELSE error('ONS relationship row ' || (rn + 1) || ' is not a 2020 unit group "<code>: <title>": ' || coalesce(b, 'NULL')) END,
                 '^([0-9]{4}): (.+)$', 1) AS code_2020,
  regexp_extract(b, '^([0-9]{4}): (.+)$', 2) AS title_2020,
  coalesce(try_cast(try_cast(men_lfs_base AS DOUBLE) AS BIGINT), error('ONS relationship base count missing for ' || group_2010)) AS men_lfs_base,
  coalesce(try_cast(D AS DOUBLE), error('ONS relationship share missing at row ' || (rn + 1))) AS men_lfs_share,
  coalesce(try_cast(try_cast(men_census_base AS DOUBLE) AS BIGINT), error('ONS relationship base count missing for ' || group_2010)) AS men_census_base,
  coalesce(try_cast(F AS DOUBLE), error('ONS relationship share missing at row ' || (rn + 1))) AS men_census_share,
  coalesce(try_cast(try_cast(women_lfs_base AS DOUBLE) AS BIGINT), error('ONS relationship base count missing for ' || group_2010)) AS women_lfs_base,
  coalesce(try_cast(H AS DOUBLE), error('ONS relationship share missing at row ' || (rn + 1))) AS women_lfs_share,
  coalesce(try_cast(try_cast(women_census_base AS DOUBLE) AS BIGINT), error('ONS relationship base count missing for ' || group_2010)) AS women_census_base,
  coalesce(try_cast(J AS DOUBLE), error('ONS relationship share missing at row ' || (rn + 1))) AS women_census_share
FROM pairings
ORDER BY rn;
