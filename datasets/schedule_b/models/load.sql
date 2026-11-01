-- The Schedule B ladder: one row per code, chapter, heading, subheading and the
-- ten-digit commodity, each with its parent. Runs after ../hts/models/outline.sql, which
-- makes `hts_outline` and `hts_levels` from USITC's export; this file reads only
-- `hts_levels`.
--
-- Inputs, relative to the Protocol directory:
--   build/sbsdf/econcord.txt     Census's Schedule B export codes, fixed width, CRLF
--   build/schedule_b_index.html  Census's index page for the year's folder
--   build/release_before.json    USITC's currentRelease, fetched before its export
--   build/release_after.json     and after it
--   build/econcord_date.json     {"date": "YYYY-MM-DD"}: econcord.txt's date as it
--                                stands in sbsdf.zip, or, where that is not carried
--                                through, the zip's Last-Modified date. An earlier step
--                                writes it, so this file does not depend on how the
--                                archive is unpacked.
--   ARC_PARAM_YEAR               the year folder (`2026`), the Protocol's `year` param,
--                                which arc passes to SQL as an environment variable and
--                                the DuckDB CLI reads with getenv.
--
-- econcord.txt has one ten-digit line per code and no upper levels. Its layout
-- (exp-stru.txt, by character position): 1-10 code, 15-65 short description, 70-219
-- long description, 225-227 first unit, 233-235 second unit, 241-245 SITC Rev. 4,
-- 251-255 end-use class, 261 agricultural flag, 266-271 NAICS, 277-278 high-technology
-- class. Each field is trimmed, its runs of spaces collapsed to one, and an empty field
-- read as NULL.
--
-- The agricultural flag prints `0` or `1`; `0` marks the lines USDA counts as
-- agricultural (all of chapters 1, 2 and 4) and `1` the rest (all of chapter 3, fish,
-- which USDA's definition leaves out). It is read as a boolean, and the run fails on
-- any other value, since a third value would mean the flag's meaning has changed.
--
-- Rows: every line, plus each 2, 4 and 6-digit prefix of one. Levels: chapter (2
-- digits), heading (4), subheading (6), commodity (10). parent_code is the prefix one
-- level up; depth is the level's position.
--
-- Text (description), and text_source:
--   census          a commodity: Census's long description, the short one in
--                   description_short. A chapter: its title in the index page.
--   usitc           a heading or subheading outside chapter 98 that USITC names: the
--                   text hts_levels gives the prefix. USITC names a prefix when
--                   hts_levels has it with a text_source other than meridian_label.
--   census_derived  a heading or subheading USITC does not name (no hts_levels row, or
--                   a meridian_label one), or any heading or subheading in chapter 98,
--                   with exactly one Census line under it: that line's text. Chapter 98
--                   never takes USITC's text: USITC's 9801-9803 are import provisions,
--                   while Schedule B's are export ones.
--   meridian_label  such a level with several Census lines under it: labelled by its
--                   level and code ("Heading 9802", "Subheading 9802.20"), since no one
--                   line's text stands for the others.
--
-- description_full is the row's ancestors' text and its own, top down, joined with
-- ' › '. Where levels take their text from the same source row (heading 0409 and
-- subheading 040900 both from USITC's 0409.00.00; 8800, 880000 and 8800000000 all from
-- Census's one line) the text appears once, at the lowest of them.
--
-- Chapter titles and sections come from the index's table cells: "Chapter 1: Live
-- Animals" and "Section 1 - Live Animals; Animal Products" once tags are stripped. A
-- chapter takes the section whose cell comes before it. Census numbers the first
-- section `1` and the rest in Roman numerals; every section is written in Roman
-- numerals (`I`).
--
-- release is the year folder and econcord.txt's date: `2026/2026-07-28`. hts_release
-- is USITC's revision (`2026HTSRev22`), the source of the usitc rows' text.
--
-- The run fails:
--   - if the year is not four digits, or econcord_date.json holds no ISO date;
--   - if USITC's revision changed between the two currentRelease fetches;
--   - 'HS edition mismatch: Schedule B <year>, USITC <revision>', when the revision's
--     year is not the Schedule B year: USITC serves only its current revision, so
--     around a new HS edition the two can belong to different editions;
--   - 'USITC names <n> of <m> headings outside chapter 98; 99% required', and the same
--     for subheadings, each level counted on its own;
--   - if a line's code is not ten digits, or a chapter has no title in the index.

CREATE OR REPLACE TEMP TABLE sb_run AS
WITH inputs AS (
  SELECT
    nullif(getenv('ARC_PARAM_YEAR'), '') AS year,
    b.name AS before_name,
    a.name AS after_name,
    d.date AS date
  FROM read_json('build/release_before.json', columns = {name: 'VARCHAR'}) b,
       read_json('build/release_after.json', columns = {name: 'VARCHAR'}) a,
       read_json('build/econcord_date.json', columns = {date: 'VARCHAR'}) d
),
checked AS (
  SELECT
    CASE WHEN regexp_full_match(coalesce(year, ''), '\d{4}') THEN year
         ELSE error('year must be a four-digit Schedule B year folder, not ' || coalesce('"' || year || '"', 'unset'))
    END AS year,
    CASE WHEN before_name IS DISTINCT FROM after_name
         THEN error('USITC revision changed during the run: ' || before_name || ' then ' || after_name)
         ELSE before_name END AS hts_release,
    CASE WHEN regexp_full_match(coalesce(date, ''), '\d{4}-\d{2}-\d{2}') AND TRY_CAST(date AS DATE) IS NOT NULL
         THEN date
         ELSE error('build/econcord_date.json holds no ISO date: ' || coalesce('"' || date || '"', 'null'))
    END AS date
  FROM inputs
)
SELECT
  year,
  CASE WHEN left(hts_release, 4) IS DISTINCT FROM year
       THEN error('HS edition mismatch: Schedule B ' || year || ', USITC ' || hts_release)
       ELSE hts_release END AS hts_release,
  year || '/' || date AS release
FROM checked;

-- A field by its first and last character positions. A temporary macro, left to go
-- with the session: arc's SQL reader does not parse DROP MACRO.
CREATE OR REPLACE TEMP MACRO sb_field(line, first, last) AS
  nullif(trim(regexp_replace(substr(line, first, last - first + 1), '\s+', ' ', 'g')), '');

CREATE OR REPLACE TEMP TABLE sb_lines AS
SELECT
  CASE WHEN regexp_full_match(substr(line, 1, 10), '\d{10}') THEN substr(line, 1, 10)
       ELSE error('econcord.txt: not a ten-digit code: "' || substr(line, 1, 10) || '"') END AS code,
  sb_field(line, 15, 65) AS description_short,
  sb_field(line, 70, 219) AS description_long,
  sb_field(line, 225, 227) AS unit_1,
  sb_field(line, 233, 235) AS unit_2,
  sb_field(line, 241, 245) AS sitc,
  sb_field(line, 251, 255) AS end_use,
  CASE sb_field(line, 261, 261)
    WHEN '0' THEN true
    WHEN '1' THEN false
    ELSE error('econcord.txt: agricultural flag is neither 0 nor 1 for ' || substr(line, 1, 10)
               || ': "' || coalesce(sb_field(line, 261, 261), '') || '"')
  END AS agricultural,
  sb_field(line, 266, 271) AS naics,
  sb_field(line, 277, 278) AS hitech
FROM (
  -- The file is read whole and split on line feeds, its carriage returns dropped: no
  -- CSV reader, so nothing in a description is taken for a delimiter or a quote.
  SELECT replace(u.line, chr(13), '') AS line
  FROM (SELECT string_split(content, chr(10)) AS lines FROM read_text('build/sbsdf/econcord.txt')) s,
       unnest(s.lines) AS u(line)
)
WHERE trim(line) <> '';

CREATE OR REPLACE TEMP TABLE sb_chapters AS
WITH cells AS (
  SELECT u.ord AS ord,
    trim(regexp_replace(
      replace(replace(regexp_replace(u.cell, '<[^>]*>', '', 'g'), '&nbsp;', ' '), '&amp;', '&'),
      '\s+', ' ', 'g')) AS text
  FROM (
    SELECT regexp_extract_all(content, '(?s)<td[^>]*>(.*?)</td>', 1) AS cells
    FROM read_text('build/schedule_b_index.html')
  ) s, unnest(s.cells) WITH ORDINALITY AS u(cell, ord)
),
parsed AS (
  SELECT ord,
    nullif(regexp_extract(text, '^Chapter (\d{1,2}) ?: ?(.+)$', 1), '') AS chapter,
    nullif(regexp_extract(text, '^Chapter (\d{1,2}) ?: ?(.+)$', 2), '') AS chapter_title,
    nullif(regexp_extract(text, '^Section (\d+|[IVXL]+) - (.+)$', 1), '') AS section_raw,
    nullif(regexp_extract(text, '^Section (\d+|[IVXL]+) - (.+)$', 2), '') AS section_title
  FROM cells
),
sections AS (
  SELECT ord, chapter, chapter_title, section_title,
    CASE
      WHEN section_raw IS NULL OR NOT regexp_full_match(section_raw, '\d+') THEN section_raw
      WHEN section_raw::INTEGER BETWEEN 1 AND 39
        THEN repeat('X', section_raw::INTEGER // 10)
             || ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX'][section_raw::INTEGER % 10 + 1]
      ELSE error('Schedule B index: no Roman numeral for Section ' || section_raw)
    END AS section
  FROM parsed
),
placed AS (
  SELECT ord, chapter, chapter_title,
    arg_max(section, ord) FILTER (WHERE section IS NOT NULL) OVER (
      ORDER BY ord ROWS UNBOUNDED PRECEDING) AS section,
    arg_max(section_title, ord) FILTER (WHERE section IS NOT NULL) OVER (
      ORDER BY ord ROWS UNBOUNDED PRECEDING) AS section_title
  FROM sections
)
SELECT lpad(chapter, 2, '0') AS chapter, chapter_title, section, section_title
FROM placed
WHERE chapter IS NOT NULL;

CREATE OR REPLACE TEMP TABLE sb_ladder AS
WITH ladder AS (
  -- Each code and prefix, the first Census line under it, and how many there are.
  SELECT left(code, n.len) AS code, min(code) AS first_line, count(*) AS lines
  FROM sb_lines, (VALUES (2), (4), (6), (10)) n(len)
  GROUP BY ALL
),
named AS (
  SELECT l.*,
    length(l.code) IN (4, 6) AND left(l.code, 2) <> '98'
      AND lv.text_source IS NOT NULL AND lv.text_source <> 'meridian_label' AS by_usitc,
    lv.text AS usitc_text,
    lv.def_seq
  FROM ladder l
  LEFT JOIN hts_levels lv ON lv.prefix = l.code AND length(l.code) IN (4, 6)
)
SELECT
  n.code,
  CASE length(n.code)
    WHEN 6 THEN left(n.code, 4) || '.' || substr(n.code, 5, 2)
    WHEN 10 THEN left(n.code, 4) || '.' || substr(n.code, 5, 2) || '.' || substr(n.code, 7, 4)
    ELSE n.code
  END AS code_display,
  CASE length(n.code) WHEN 2 THEN 'chapter' WHEN 4 THEN 'heading' WHEN 6 THEN 'subheading'
                      WHEN 10 THEN 'commodity' END AS level,
  CASE length(n.code) WHEN 2 THEN 0 WHEN 4 THEN 1 WHEN 6 THEN 2 WHEN 10 THEN 3 END AS depth,
  CASE length(n.code) WHEN 4 THEN left(n.code, 2) WHEN 6 THEN left(n.code, 4)
                      WHEN 10 THEN left(n.code, 6) END AS parent_code,
  CASE
    WHEN length(n.code) = 2 THEN
      CASE WHEN c.chapter_title IS NULL
           THEN error('chapter ' || n.code || ' has no title in the Schedule B index')
           ELSE c.chapter_title END
    WHEN n.by_usitc THEN n.usitc_text
    WHEN n.lines = 1 THEN coalesce(f.description_long, f.description_short)
    WHEN length(n.code) = 4 THEN 'Heading ' || n.code
    ELSE 'Subheading ' || left(n.code, 4) || '.' || substr(n.code, 5, 2)
  END AS description,
  CASE
    WHEN length(n.code) IN (2, 10) THEN 'census'
    WHEN n.by_usitc THEN 'usitc'
    WHEN n.lines = 1 THEN 'census_derived'
    ELSE 'meridian_label'
  END AS text_source,
  -- The row the text comes from, so levels sharing it are written once in
  -- description_full.
  CASE
    WHEN length(n.code) = 2 THEN 'chapter ' || n.code
    WHEN n.by_usitc THEN 'usitc ' || n.def_seq
    WHEN n.lines = 1 THEN 'census ' || n.first_line
    ELSE 'label ' || n.code
  END AS text_from,
  c.section,
  c.section_title
FROM named n
LEFT JOIN sb_lines f ON f.code = n.first_line
LEFT JOIN sb_chapters c ON c.chapter = left(n.code, 2);

-- The edition guard, each level counted on its own.
SELECT CASE WHEN named * 100 < total * 99
  THEN error('USITC names ' || named || ' of ' || total || ' headings outside chapter 98; 99% required') END
FROM (
  SELECT count(*) FILTER (WHERE text_source = 'usitc') AS named, count(*) AS total
  FROM sb_ladder WHERE level = 'heading' AND left(code, 2) <> '98'
);

SELECT CASE WHEN named * 100 < total * 99
  THEN error('USITC names ' || named || ' of ' || total || ' subheadings outside chapter 98; 99% required') END
FROM (
  SELECT count(*) FILTER (WHERE text_source = 'usitc') AS named, count(*) AS total
  FROM sb_ladder WHERE level = 'subheading' AND left(code, 2) <> '98'
);

CREATE OR REPLACE TABLE schedule_b AS
WITH paths AS (
  -- Each row's ancestors and itself, top down. Where several take their text from
  -- one source row, only the lowest of them is kept.
  SELECT r.code,
    list({text: a.description, text_from: a.text_from} ORDER BY a.depth) AS chain
  FROM sb_ladder r
  JOIN sb_ladder a ON starts_with(r.code, a.code)
  GROUP BY r.code
)
SELECT
  r.code,
  r.code_display,
  r.level,
  r.depth,
  r.parent_code,
  r.description,
  array_to_string(
    list_transform(
      list_filter(p.chain, lambda x, i: NOT list_contains(
        list_transform(list_slice(p.chain, i + 1, len(p.chain)), lambda y: y.text_from), x.text_from)),
      lambda x: x.text),
    ' › ') AS description_full,
  run.release,
  r.text_source,
  l.description_short,
  l.unit_1,
  l.unit_2,
  l.sitc,
  l.end_use,
  l.agricultural,
  l.naics,
  l.hitech,
  r.section,
  r.section_title,
  run.hts_release
FROM sb_ladder r
JOIN paths p USING (code)
LEFT JOIN sb_lines l ON l.code = r.code AND r.level = 'commodity'
CROSS JOIN sb_run run
ORDER BY r.code;

DROP TABLE sb_ladder;
DROP TABLE sb_chapters;
DROP TABLE sb_lines;
DROP TABLE sb_run;
