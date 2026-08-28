-- SuperGPQA's single file, into the shape the embedder and the export read.
--
-- THE TEN COLUMNS AND THEIR TYPES ARE DECLARED, NOT INFERRED. `read_json`'s
-- auto-detection settles a schema from a sample of the file, which makes the type of
-- every column below a property of what the sampler happened to look at rather than of
-- this Protocol. `columns =` names all ten instead, so a source that grew a field or
-- changed one is a refusal at this step and not a silently different export.
--
-- `options` IS FLATTENED TO A DELIMITED STRING, AND THE DELIMITER IS ' || ' RATHER
-- THAN ../scienceqa's ' | '. A list column is not a shape the export/describe pair in
-- this directory has handled, so ../scienceqa/models/load.sql set the precedent of
-- joining one into a single string — but its delimiter is not safe over this corpus:
-- 31 of these 26,529 rows carry ' | ' INSIDE an option, so splitting on it would
-- return the wrong options for those 31 and nothing would say so. ' || ' appears
-- inside no option in this file, which is not an assertion here but the first gate
-- below: if it ever does, the run stops rather than shipping a column that cannot be
-- read back.
CREATE OR REPLACE TABLE corpus AS
SELECT
  uuid                              AS uuid,
  question                          AS question,
  array_to_string(options, ' || ')  AS answer_options,
  answer                            AS answer,
  answer_letter                     AS answer_letter,
  discipline                        AS discipline,
  field                             AS field,
  subfield                          AS subfield,
  difficulty                        AS difficulty,
  is_calculation                    AS is_calculation
FROM read_json(
  'build/supergpqa.src.jsonl',
  format = 'newline_delimited',
  columns = {
    uuid: 'VARCHAR',
    question: 'VARCHAR',
    options: 'VARCHAR[]',
    answer: 'VARCHAR',
    answer_letter: 'VARCHAR',
    discipline: 'VARCHAR',
    field: 'VARCHAR',
    subfield: 'VARCHAR',
    difficulty: 'VARCHAR',
    is_calculation: 'BOOLEAN'
  }
);

-- GATE 1 — THE FLATTEN IS REVERSIBLE. `answer_options` is only usable if splitting it
-- on ' || ' returns the options that went in. Checked against the source list rather
-- than against the joined string, so this catches a delimiter that collides with the
-- data whatever the delimiter is changed to.
SELECT CASE WHEN v.rows > 0 THEN error(
         'supergpqa: '' || '' appears inside an option on ' || v.rows || ' row(s), so '
         || 'answer_options cannot be split back into the options it was joined from. '
         || 'Pick a delimiter this corpus does not contain. e.g. ' || v.examples) END
FROM (
  SELECT count(*) AS rows,
         array_to_string(list_slice(list_sort(list(uuid)), 1, 5), ', ') AS examples
  FROM read_json(
    'build/supergpqa.src.jsonl',
    format = 'newline_delimited',
    columns = {uuid: 'VARCHAR', options: 'VARCHAR[]'}
  )
  WHERE list_bool_or(list_transform(options, x -> contains(x, ' || ')))
) v;

-- GATE 2 — `answer_letter` INDEXES `answer_options`. The source records the correct
-- answer twice, as text in `answer` and as a letter in `answer_letter`, and a chart
-- reading the letter is trusting that it selects the same option the text names. It
-- does on all 26,529 rows of this revision. If a future revision breaks that, a
-- reader would have two answer columns disagreeing and no signal, so the run stops.
SELECT CASE WHEN v.rows > 0 THEN error(
         'supergpqa: answer_letter does not select answer on ' || v.rows || ' row(s) — '
         || 'the letter and the text name different options, so neither can be trusted. '
         || 'e.g. ' || v.examples) END
FROM (
  SELECT count(*) AS rows,
         array_to_string(list_slice(list_sort(list(uuid)), 1, 5), ', ') AS examples
  FROM read_json(
    'build/supergpqa.src.jsonl',
    format = 'newline_delimited',
    columns = {uuid: 'VARCHAR', options: 'VARCHAR[]', answer: 'VARCHAR',
               answer_letter: 'VARCHAR'}
  )
  WHERE options[ascii(answer_letter) - 64] IS DISTINCT FROM answer
) v;

-- GATE 3 — THE LICENCE OBLIGATION, CHECKED RATHER THAN TRUSTED. ODC-BY requires credit
-- to the original creators of the third-party data SuperGPQA includes, and the source
-- card at revision 4430d445 names fifteen datasets. The fifteen below are transcribed
-- from that card; descriptor.overrides.json carries one `sources` entry per dataset
-- with the card's own link. This gate holds the two lists to each other and NAMES the
-- ones that have gone missing, so trimming the descriptor is a failed run rather than
-- a quiet breach. Attribution is the one thing in this Protocol that a reader cannot
-- check by looking at the data.
SELECT CASE WHEN v.missing > 0 THEN error(
         'supergpqa: descriptor.overrides.json names ' || (15 - v.missing) || ' of the '
         || '15 upstream datasets SuperGPQA''s ODC-BY licence requires credit for. '
         || 'Missing: ' || v.names) END
FROM (
  SELECT count(*) AS missing,
         array_to_string(list_sort(list(u.name)), ', ') AS names
  FROM (VALUES
    ('LawBench'), ('MedMCQA'), ('MedQA'), ('MMLU-Pro'), ('MMLU-CF'),
    ('ShoppingMMLU'), ('UTMath'), ('MusicTheoryBench'), ('Omni-Math'), ('U-MATH'),
    ('Putnam-AXIOM'), ('Short-form Factuality'), ('Chinese SimpleQA'), ('AIME-AOPS'),
    ('AIMO Validation AIME')
  ) AS u(name)
  WHERE NOT EXISTS (
    SELECT 1
    FROM (
      SELECT unnest(json_extract_string(content, '$.sources[*].title')) AS title
      FROM read_text('descriptor.overrides.json')
    ) d
    WHERE d.title LIKE 'SuperGPQA upstream: ' || u.name || ' %'
       OR d.title = 'SuperGPQA upstream: ' || u.name
  )
) v;
