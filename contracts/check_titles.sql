-- The title index contract (see contracts/README.md), run as an arc `sql:` step.
-- Reads contract_titles and the other inputs the README names; raises
-- error('titles: <rule>: <first offending value>') on the first rule broken, else
-- writes contract_titles_result.

-- No rows.
SELECT error('titles: no rows') FROM (SELECT count(*) AS n FROM contract_titles) WHERE n = 0;

-- The core columns, in order, lead the table (extra columns may follow).
SELECT error('titles: core columns out of order, expected leading ' || ['entry_id', 'title', 'code', 'code_description', 'release', 'text_source', 'corpus']::VARCHAR || ', found ' || found::VARCHAR)
FROM (SELECT list(column_name) AS found FROM (SELECT column_name FROM (DESCRIBE contract_titles) LIMIT 7))
WHERE found != ['entry_id', 'title', 'code', 'code_description', 'release', 'text_source', 'corpus'];

-- A repeated entry_id.
SELECT error('titles: repeated entry_id: ' || entry_id) FROM (
    SELECT entry_id FROM contract_titles WHERE entry_id IS NOT NULL
    GROUP BY entry_id HAVING count(*) > 1 ORDER BY entry_id LIMIT 1);

-- A NULL in a column the contract requires.
SELECT error('titles: NULL entry_id') FROM contract_titles WHERE entry_id IS NULL LIMIT 1;
SELECT error('titles: NULL title') FROM contract_titles WHERE title IS NULL LIMIT 1;
SELECT error('titles: NULL code') FROM contract_titles WHERE code IS NULL LIMIT 1;
SELECT error('titles: NULL release') FROM contract_titles WHERE release IS NULL LIMIT 1;

-- A code that is not in the code list built in the same Run.
SELECT error('titles: code not in code list: ' || code) FROM (
    SELECT DISTINCT t.code FROM contract_titles t
    WHERE t.code IS NOT NULL AND t.code NOT IN (SELECT code FROM contract_code_list)
    ORDER BY t.code LIMIT 1);

-- A code at a level the issuer does not code titles to.
SELECT error('titles: code at the wrong level: ' || code) FROM (
    SELECT DISTINCT t.code FROM contract_titles t JOIN contract_code_list c USING (code)
    WHERE c.level IS DISTINCT FROM (SELECT level FROM contract_title_level)
    ORDER BY t.code LIMIT 1);

-- More than one release.
SELECT error('titles: more than one release: ' || string_agg(release, ', ' ORDER BY release))
FROM (SELECT DISTINCT release FROM contract_titles WHERE release IS NOT NULL)
HAVING count(*) > 1;

CREATE OR REPLACE TABLE contract_titles_result AS SELECT count(*) AS rows_checked FROM contract_titles;
