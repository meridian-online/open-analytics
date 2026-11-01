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
-- hts_levels: for every 2, 4, 6, 8 and 10-digit prefix of a numbered code, the row that
-- defines it: the shallowest numbered row under the prefix, first by indent then by seq.
--   prefix    the digits
--   def_seq   the defining row's seq
--   def_code  the defining row's code
--   text      the text of every unnumbered heading on the defining row's chain below the
--             prefix's parent level, in order, then the defining row's own text, joined
--             by a space. Subheading 010511 reads "Weighing not more than 185 g: Chickens".
--             The parent level's place on the chain is its own defining row: when that
--             row is the prefix's defining row too, nothing lies between them (tariff
--             line 01012100 reads "Purebred breeding animals", its subheading 010121
--             having taken "Horses:"); when it is on the chain, the headings below it
--             count. Otherwise the place is the deepest numbered row on the chain no
--             longer than the parent level's code, and for a heading, whose parent is a
--             chapter and so on no chain, the chain is read from the top of the chapter.

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
WITH prefixes AS (
  SELECT left(o.code, n.len) AS prefix, n.len, o.seq, o.code, o.indent, o.description
  FROM hts_outline o, (VALUES (2), (4), (6), (8), (10)) n(len)
  WHERE o.code IS NOT NULL AND n.len <= length(o.code)
),
defining AS (
  SELECT prefix, any_value(len) AS len,
         arg_min(seq, (indent, seq)) AS def_seq,
         arg_min(code, (indent, seq)) AS def_code,
         arg_min(description, (indent, seq)) AS def_text
  FROM prefixes
  GROUP BY prefix
),
cut AS (
  -- Where the parent level sits on the defining row's chain: the seq after which the
  -- unnumbered headings count.
  SELECT d.prefix,
    CASE
      WHEN pd.def_seq = d.def_seq THEN d.def_seq
      WHEN pd.def_seq IN (SELECT anc_seq FROM hts_chain c WHERE c.seq = d.def_seq) THEN pd.def_seq
      ELSE (SELECT max(a.seq)
            FROM hts_chain c JOIN hts_outline a ON a.seq = c.anc_seq
            WHERE c.seq = d.def_seq AND a.code IS NOT NULL AND length(a.code) <= d.len - 2)
    END AS cut_seq
  FROM defining d
  LEFT JOIN defining pd ON d.len > 2 AND pd.prefix = left(d.prefix, d.len - 2)
),
headings AS (
  SELECT d.prefix, string_agg(a.description, ' ' ORDER BY a.seq) AS text
  FROM defining d
  LEFT JOIN cut USING (prefix)
  JOIN hts_chain c ON c.seq = d.def_seq
  JOIN hts_outline a ON a.seq = c.anc_seq
  WHERE a.code IS NULL AND a.seq > coalesce(cut.cut_seq, 0)
  GROUP BY d.prefix
)
SELECT d.prefix, d.def_seq, d.def_code, concat_ws(' ', h.text, d.def_text) AS text
FROM defining d LEFT JOIN headings h USING (prefix)
ORDER BY d.prefix;

DROP TABLE hts_chain;
DROP TABLE hts_rows;
