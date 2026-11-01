-- USITC's outline of the Harmonized Tariff Schedule, read from its JSON export.
-- Shared: the HTS Protocol runs this file, and the Schedule B Protocol runs it by its
-- relative path (../hts/models/outline.sql) and reads only the two tables it makes,
-- `hts_outline` and `hts_levels`. Keep their columns as they are.
--
-- `build/hts.json` is one array, one element per printed row, in schedule order. A row
-- with an `htsno` is numbered (4, 6, 8 or 10 digits, dotted); a row without one is an
-- unnumbered text heading such as "Horses:" that groups the rows indented beneath it.
-- `indent` is a string ("03" occurs and reads as 3).
--
-- hts_outline: one row per USITC row in file order.
--   seq           position in the file, from 1
--   code          htsno with the dots removed; NULL for an unnumbered heading
--   code_display  htsno as USITC prints it; NULL for an unnumbered heading
--   indent        USITC's indent as an integer
--   description   the row's text, whitespace collapsed
--   context       the descriptions of the rows above it on its indent chain, nearest
--                 last: each row's chain parent is the nearest earlier row in the same
--                 chapter with a smaller indent. A row's chapter is the chapter of the
--                 first numbered row at or after it, so an unnumbered heading at the top
--                 of a chapter belongs to that chapter, and no chain crosses a chapter.
--
-- hts_levels: for every 2, 4, 6, 8 and 10-digit prefix of a numbered code, the outline
-- row that names it and the text that row gives it.
--   prefix       the digits
--   def_seq      the naming row's seq (see below); NULL when there is none
--   def_code     the naming row's code; NULL when it is unnumbered or there is none
--   text         the prefix's text
--   text_source  whose text it is, so a reader can tell USITC's text from a label:
--     usitc           USITC prints the prefix as a code: that row, and its own text.
--     usitc_derived   USITC prints the prefix only through longer codes, and the
--                     prefix has a node: the lowest outline row, numbered or unnumbered,
--                     that is, or is above on the indent chain, every numbered row under
--                     the prefix, and is above no numbered row outside it. The text is
--                     every unnumbered heading on the node's chain strictly below the
--                     parent level's node, in order, then the node's own text, joined by
--                     a space (subheading 010511: "Weighing not more than 185 g:
--                     Chickens"; 080270, whose two lines sit under "Kola nuts (Cola
--                     spp.):" alone, takes that heading's text). The parent level's node
--                     is the lowest row above every numbered row under the parent
--                     prefix; for a heading, whose parent is a chapter and on no chain,
--                     and for any prefix whose parent is a label, the chain is read from
--                     the top of the chapter. The node row itself may sit outside the
--                     prefix: heading 3407 is the node of its only subheading, 340700.
--     meridian_label  no row belongs to the prefix alone: its rows sit side by side at
--                     the top of the chapter, or directly under a row that other
--                     prefixes' rows sit under too (chapters 98 and 99). The text is the
--                     level and code ("Heading 9903", "Subheading 9901.00"), and def_seq
--                     is the lowest row above all the prefix's rows, if there is one.
--   Two-digit prefixes are listed by the same rule; both ladders take chapter titles
--   from their own sources.

-- Two working tables, dropped at the end: the rows as read, and every row's chain
-- ancestors (seq, anc_seq), which both tables below are built from.
CREATE OR REPLACE TEMP TABLE hts_rows AS
WITH src AS (
  SELECT
    u.ord::BIGINT AS seq,
    nullif(replace(trim(u.r.htsno), '.', ''), '') AS code,
    nullif(trim(u.r.htsno), '') AS code_display,
    u.r.indent::INTEGER AS indent,
    trim(regexp_replace(u.r.description, '\s+', ' ', 'g')) AS description
  FROM (
    SELECT from_json(content, '[{"htsno":"VARCHAR","indent":"VARCHAR","description":"VARCHAR"}]') AS rows
    FROM read_text('build/hts.json')
  ) s, unnest(s.rows) WITH ORDINALITY AS u(r, ord)
)
SELECT *,
  first_value(left(code, 2) IGNORE NULLS) OVER (
    ORDER BY seq ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS chapter
FROM src;

CREATE OR REPLACE TEMP TABLE hts_chain AS
WITH RECURSIVE parent AS (
  SELECT r.seq, max(a.seq) AS parent_seq
  FROM hts_rows r
  JOIN hts_rows a ON a.chapter = r.chapter AND a.seq < r.seq AND a.indent < r.indent
  GROUP BY r.seq
),
walk(seq, anc_seq) AS (
  SELECT seq, parent_seq FROM parent
  UNION ALL
  SELECT w.seq, p.parent_seq FROM walk w JOIN parent p ON p.seq = w.anc_seq
)
SELECT * FROM walk;

CREATE OR REPLACE TABLE hts_outline AS
SELECT r.seq, r.code, r.code_display, r.indent, r.description,
       coalesce(c.context, []::VARCHAR[]) AS context
FROM hts_rows r
LEFT JOIN (
  SELECT ch.seq, list(a.description ORDER BY a.seq) AS context
  FROM hts_chain ch JOIN hts_rows a ON a.seq = ch.anc_seq
  GROUP BY ch.seq
) c USING (seq)
ORDER BY r.seq;

CREATE OR REPLACE TABLE hts_levels AS
WITH lineage AS (
  -- Each row with every row above it on its chain, and itself.
  SELECT seq, anc_seq FROM hts_chain
  UNION ALL
  SELECT seq, seq FROM hts_rows
),
prefixes AS (
  SELECT DISTINCT left(code, n.len) AS prefix, n.len
  FROM hts_rows, (VALUES (2), (4), (6), (8), (10)) n(len)
  WHERE code IS NOT NULL AND n.len <= length(code)
),
under AS (
  SELECT p.prefix, p.len, r.seq
  FROM prefixes p JOIN hts_rows r ON r.code IS NOT NULL AND left(r.code, p.len) = p.prefix
),
common AS (
  -- The lowest row above (or being) every numbered row under the prefix. Rows above a
  -- given row form a chain, so the common ones do too, and the lowest is the last.
  SELECT prefix, max(anc_seq) AS ca_seq
  FROM (
    SELECT u.prefix, l.anc_seq, count(*) AS n, any_value(t.total) AS total
    FROM under u
    JOIN lineage l ON l.seq = u.seq
    JOIN (SELECT prefix, count(*) AS total FROM under GROUP BY prefix) t USING (prefix)
    GROUP BY u.prefix, l.anc_seq
  )
  WHERE n = total
  GROUP BY prefix
),
leaks AS (
  -- Prefixes whose common row is above a numbered row outside the prefix. The common
  -- row itself does not count: a printed heading over its only subheading's lines is
  -- that subheading's node, though its own code is shorter.
  SELECT DISTINCT p.prefix
  FROM prefixes p
  JOIN common c USING (prefix)
  JOIN hts_chain l ON l.anc_seq = c.ca_seq
  JOIN hts_rows r ON r.seq = l.seq
  WHERE r.code IS NOT NULL AND left(r.code, p.len) <> p.prefix
),
kinds AS (
  SELECT
    p.prefix, p.len,
    own.seq AS own_seq,
    c.ca_seq,
    CASE
      WHEN own.seq IS NOT NULL THEN 'usitc'
      WHEN c.ca_seq IS NOT NULL AND lk.prefix IS NULL THEN 'usitc_derived'
      ELSE 'meridian_label'
    END AS text_source
  FROM prefixes p
  LEFT JOIN hts_rows own ON own.code = p.prefix
  LEFT JOIN common c ON c.prefix = p.prefix
  LEFT JOIN leaks lk ON lk.prefix = p.prefix
),
classified AS (
  -- Where the text's chain starts: below the parent level's node, or at the chapter
  -- top for a heading and for a level whose parent is a label.
  SELECT k.*,
    CASE WHEN k.len > 4 AND pk.text_source <> 'meridian_label' THEN pk.ca_seq END AS parent_ca_seq
  FROM kinds k
  LEFT JOIN kinds pk ON k.len > 2 AND pk.prefix = left(k.prefix, k.len - 2)
),
headings AS (
  -- The unnumbered headings on a node's chain strictly below the parent level's node.
  SELECT k.prefix, string_agg(a.description, ' ' ORDER BY a.seq) AS text
  FROM classified k
  JOIN hts_chain ch ON ch.seq = k.ca_seq
  JOIN hts_rows a ON a.seq = ch.anc_seq
  WHERE k.text_source = 'usitc_derived'
    AND a.code IS NULL AND a.seq > coalesce(k.parent_ca_seq, 0)
  GROUP BY k.prefix
)
SELECT
  k.prefix,
  coalesce(k.own_seq, k.ca_seq) AS def_seq,
  d.code AS def_code,
  CASE k.text_source
    WHEN 'usitc' THEN d.description
    WHEN 'usitc_derived' THEN concat_ws(' ', h.text, d.description)
    ELSE CASE k.len WHEN 2 THEN 'Chapter' WHEN 4 THEN 'Heading' WHEN 6 THEN 'Subheading'
                    WHEN 8 THEN 'Tariff line' ELSE 'Statistical line' END
         || ' ' || CASE WHEN k.len = 2 THEN k.prefix
                        ELSE array_to_string([left(k.prefix, 4)] || list_transform(
                               range(5, k.len, 2), lambda i: substr(k.prefix, i, 2)), '.') END
  END AS text,
  k.text_source
FROM classified k
LEFT JOIN hts_rows d ON d.seq = coalesce(k.own_seq, k.ca_seq)
LEFT JOIN headings h ON h.prefix = k.prefix
ORDER BY k.prefix;

DROP TABLE hts_chain;
DROP TABLE hts_rows;
