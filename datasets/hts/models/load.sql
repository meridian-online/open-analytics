-- The HTS ladder: one row per code, every level present, each with its parent.
-- Runs after outline.sql, which makes `hts_outline` and `hts_levels`.
--
-- The ladder is every numbered USITC code plus every 2, 4, 6 and 8-digit prefix of
-- one: USITC prints a single-subdivision heading only through its longer code (heading
-- 0409 appears only as 0409.00.00, "Natural honey"), and has no chapter rows at all.
-- Levels: chapter (2 digits), heading (4), subheading (6), tariff_line (8),
-- statistical_line (10). Because every prefix is in the ladder, a row's parent is the
-- prefix one level up, and depth is the level's position.
--
-- text_source:
--   usitc           a row USITC prints with its own code; `description` is its text
--   usitc_derived   a level made for a prefix USITC prints only through longer codes,
--                   whose text is its node's in USITC's outline (hts_levels.text)
--   meridian_label  a made level USITC gives no text of its own, labelled by its level
--                   and code ("Heading 9903")
--   usitc_toc       a chapter; `description` is its title in chapters.csv, copied from
--                   USITC's table of contents
--
-- description_full is the chapter title, then the row's subchapter if it has one, then
-- the indent chain of the outline row behind it and that row's own text, joined with
-- ' › '. The row behind it is the row itself for a printed code and the node
-- (hts_levels.def_seq) for a made one; a label ends with the label, after its common
-- row's chain and text when it has one. Each outline row appears once, because a chain
-- lists distinct rows, and two rows with the same text ("Other:" under "Other:") both
-- appear.
--
-- Subchapters: an unnumbered row at an indent deeper than the indent-0 numbered row
-- right after it ("I. CHEMICAL ELEMENTS" before heading 2801) is a chapter subdivision,
-- on no indent chain. It applies from that numbered row to the next such row or the
-- chapter's end, and a ladder row takes the subchapter of the first USITC row under it.
--
-- Rates are as printed, blank as NULL. A row that prints none of general, special and
-- column 2 takes all three from its nearest ladder ancestor that prints a general rate,
-- and rate_from_code names that ancestor; a row that prints any of them keeps its own,
-- with rate_from_code = code. Only printed rows carry rates: made levels and chapters
-- have none.
--
-- The run fails if USITC's revision changed between the two currentRelease fetches,
-- or if a chapter in the data has no title in chapters.csv.

CREATE OR REPLACE TABLE hts AS
WITH release AS (
  SELECT CASE WHEN b.name IS DISTINCT FROM a.name
              THEN error('USITC revision changed during the run: ' || b.name || ' then ' || a.name)
              ELSE b.name END AS release
  FROM read_json('build/release_before.json', columns = {name: 'VARCHAR'}) b,
       read_json('build/release_after.json', columns = {name: 'VARCHAR'}) a
),
chapters AS (
  SELECT chapter, nullif(trim(chapter_title), '') AS chapter_title, section, section_title
  FROM read_csv('chapters.csv', header = true, all_varchar = true)
),
raw AS (
  -- The rate and HTS columns, read from the same file in the same order as outline.sql.
  SELECT
    u.ord::BIGINT AS seq,
    u.r.units AS units,
    CASE WHEN trim(u.r.general) <> '' THEN u.r.general END AS general_rate,
    CASE WHEN trim(u.r.special) <> '' THEN u.r.special END AS special_rate,
    CASE WHEN trim(u.r.other) <> '' THEN u.r.other END AS column2_rate,
    list_transform(u.r.footnotes, lambda f: trim(f.value)) AS footnotes,
    CASE WHEN trim(u.r.quotaQuantity) <> '' THEN u.r.quotaQuantity END AS quota_quantity,
    CASE WHEN trim(u.r.additionalDuties) <> '' THEN u.r.additionalDuties END AS additional_duties
  FROM (
    SELECT from_json(content, '[{"units":["VARCHAR"],"general":"VARCHAR","special":"VARCHAR","other":"VARCHAR",
                                 "footnotes":[{"value":"VARCHAR"}],"quotaQuantity":"VARCHAR",
                                 "additionalDuties":"VARCHAR"}]') AS rows
    FROM read_text('build/hts.json')
  ) s, unnest(s.rows) WITH ORDINALITY AS u(r, ord)
),
printed AS (
  SELECT o.*, raw.* EXCLUDE (seq),
         general_rate IS NOT NULL OR special_rate IS NOT NULL OR column2_rate IS NOT NULL AS prints_rate
  FROM hts_outline o JOIN raw USING (seq)
  WHERE o.code IS NOT NULL
),
subchapters AS (
  -- Each outline row's subchapter text, if one applies (see the header).
  SELECT seq, last_value(sub IGNORE NULLS) OVER (
           PARTITION BY chapter ORDER BY seq ROWS UNBOUNDED PRECEDING) AS subchapter
  FROM (
    SELECT seq, chapter,
      CASE WHEN code IS NULL AND indent > 0 AND next_indent = 0 AND next_code IS NOT NULL
                AND next_chapter = chapter THEN description END AS sub
    FROM (
      SELECT seq, code, indent, description,
        first_value(left(code, 2) IGNORE NULLS) OVER (
          ORDER BY seq ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS chapter,
        lead(indent) OVER (ORDER BY seq) AS next_indent,
        lead(code) OVER (ORDER BY seq) AS next_code,
        left(lead(code) OVER (ORDER BY seq), 2) AS next_chapter
      FROM hts_outline
    )
  )
),
ladder AS (
  SELECT left(code, n.len) AS code, min(seq) AS first_seq
  FROM printed, (VALUES (2), (4), (6), (8), (10)) n(len)
  WHERE n.len <= length(code)
  GROUP BY ALL
),
rows AS (
  SELECT
    l.code,
    CASE
      WHEN p.code IS NOT NULL THEN p.code_display
      WHEN length(l.code) = 2 THEN l.code
      ELSE array_to_string(
             [left(l.code, 4)] || list_transform(range(5, length(l.code), 2), lambda i: substr(l.code, i, 2)),
             '.')
    END AS code_display,
    CASE length(l.code)
      WHEN 2 THEN 'chapter' WHEN 4 THEN 'heading' WHEN 6 THEN 'subheading'
      WHEN 8 THEN 'tariff_line' WHEN 10 THEN 'statistical_line' END AS level,
    length(l.code) // 2 - 1 AS depth,
    CASE WHEN length(l.code) > 2 THEN left(l.code, length(l.code) - 2) END AS parent_code,
    CASE WHEN t.chapter_title IS NULL
         THEN error('chapter ' || left(l.code, 2) || ' has no title in chapters.csv')
         ELSE t.chapter_title END AS chapter_title,
    CASE
      WHEN length(l.code) = 2 THEN 'usitc_toc'
      WHEN p.code IS NOT NULL THEN 'usitc'
      ELSE lv.text_source
    END AS text_source,
    CASE WHEN p.code IS NOT NULL THEN p.description ELSE lv.text END AS own_text,
    CASE WHEN p.code IS NOT NULL THEN p.seq ELSE lv.def_seq END AS def_seq,
    sc.subchapter,
    t.section, t.section_title
  FROM ladder l
  LEFT JOIN printed p ON p.code = l.code
  LEFT JOIN hts_levels lv ON lv.prefix = l.code
  LEFT JOIN chapters t ON t.chapter = left(l.code, 2)
  LEFT JOIN subchapters sc ON sc.seq = l.first_seq
),
rate_source AS (
  -- The nearest ladder ancestor that prints a general rate, for rows that print none.
  SELECT r.code, arg_max(a.code, length(a.code)) AS rate_from_code
  FROM rows r
  JOIN printed a ON a.general_rate IS NOT NULL
               AND length(a.code) < length(r.code)
               AND left(r.code, length(a.code)) = a.code
  JOIN printed own ON own.code = r.code
  WHERE NOT own.prints_rate
  GROUP BY r.code
)
SELECT
  r.code,
  r.code_display,
  r.level,
  r.depth,
  r.parent_code,
  CASE WHEN r.level = 'chapter' THEN r.chapter_title ELSE r.own_text END AS description,
  CASE WHEN r.level = 'chapter' THEN r.chapter_title
       ELSE array_to_string(full_list, ' › ')
  END AS description_full,
  (SELECT release FROM release) AS release,
  r.text_source,
  d.indent,
  r.section,
  r.section_title,
  p.units,
  coalesce(p.general_rate, src.general_rate) AS general_rate,
  coalesce(p.special_rate, src.special_rate) AS special_rate,
  coalesce(p.column2_rate, src.column2_rate) AS column2_rate,
  CASE WHEN p.prints_rate THEN r.code ELSE rs.rate_from_code END AS rate_from_code,
  p.footnotes,
  p.quota_quantity,
  p.additional_duties
FROM (
  SELECT r.*,
    list_filter([r.chapter_title, r.subchapter], lambda x: x IS NOT NULL)
      || coalesce(d.context || [d.description], [])
      || CASE WHEN r.text_source = 'meridian_label' THEN [r.own_text] ELSE [] END AS full_list
  FROM rows r LEFT JOIN hts_outline d ON d.seq = r.def_seq
) r
LEFT JOIN hts_outline d ON d.seq = r.def_seq AND r.text_source = 'usitc'
LEFT JOIN printed p ON p.code = r.code
LEFT JOIN rate_source rs ON rs.code = r.code
LEFT JOIN printed src ON src.code = rs.rate_from_code AND NOT coalesce(p.prints_rate, false)
ORDER BY r.code;
