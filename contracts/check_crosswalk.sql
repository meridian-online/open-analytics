-- The edition crosswalk contract (see contracts/README.md), run as an arc `sql:` step.
-- Reads contract_crosswalk and the other inputs the README names; raises
-- error('crosswalk: <rule>: <first offending value>') on the first rule broken, else
-- writes contract_crosswalk_result.

-- No rows.
SELECT error('crosswalk: no rows') FROM (SELECT count(*) AS n FROM contract_crosswalk) WHERE n = 0;

-- The shape and level: exactly one row, neither NULL.
SELECT error('crosswalk: contract_crosswalk_shape must hold exactly one row with a non-NULL from_pattern and level')
FROM (SELECT count(*) AS n, count(from_pattern) AS p, count(level) AS l FROM contract_crosswalk_shape)
WHERE n != 1 OR p != 1 OR l != 1;

-- The core columns, in order, lead the table (extra columns may follow).
-- Read from information_schema, not (DESCRIBE ...): arc's SQL reader cannot parse DESCRIBE
-- as a subquery, and treats a file it cannot parse as an opaque step whose tables it
-- cannot see, so it reruns the step on every run.
-- IS DISTINCT FROM, so a table it finds no columns for (a TEMP view, in another schema)
-- fails the rule rather than passing it with found NULL.
SELECT error('crosswalk: core columns out of order, expected leading ' || ['from_code', 'from_description', 'to_code', 'to_description', 'from_edition', 'to_edition', 'release']::VARCHAR || ', found ' || coalesce(found::VARCHAR, 'no columns in the current schema'))
FROM (SELECT list(column_name ORDER BY ordinal_position) AS found FROM information_schema.columns
      WHERE table_catalog = current_database() AND table_schema = current_schema()
        AND table_name = 'contract_crosswalk' AND ordinal_position <= 7)
WHERE found IS DISTINCT FROM ['from_code', 'from_description', 'to_code', 'to_description', 'from_edition', 'to_edition', 'release'];

-- A repeated (from_code, to_code) pair.
SELECT error('crosswalk: repeated pair: ' || from_code || ' -> ' || to_code) FROM (
    SELECT from_code, to_code FROM contract_crosswalk
    WHERE from_code IS NOT NULL AND to_code IS NOT NULL
    GROUP BY from_code, to_code HAVING count(*) > 1 ORDER BY from_code, to_code LIMIT 1);

-- A NULL in a column the contract requires.
SELECT error('crosswalk: NULL from_code') FROM contract_crosswalk WHERE from_code IS NULL LIMIT 1;
SELECT error('crosswalk: NULL to_code') FROM contract_crosswalk WHERE to_code IS NULL LIMIT 1;
SELECT error('crosswalk: NULL from_edition') FROM contract_crosswalk WHERE from_edition IS NULL LIMIT 1;
SELECT error('crosswalk: NULL to_edition') FROM contract_crosswalk WHERE to_edition IS NULL LIMIT 1;
SELECT error('crosswalk: NULL release') FROM contract_crosswalk WHERE release IS NULL LIMIT 1;

-- A to_code that is not a code, at the same level, in the code list built in the same Run.
SELECT error('crosswalk: to_code not in code list: ' || to_code) FROM (
    SELECT DISTINCT x.to_code FROM contract_crosswalk x
    WHERE x.to_code IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM contract_code_list c WHERE c.code = x.to_code)
    ORDER BY x.to_code LIMIT 1);

SELECT error('crosswalk: to_code at the wrong level: ' || to_code) FROM (
    SELECT DISTINCT x.to_code FROM contract_crosswalk x JOIN contract_code_list c ON c.code = x.to_code
    WHERE c.level IS DISTINCT FROM (SELECT level FROM contract_crosswalk_shape)
    ORDER BY x.to_code LIMIT 1);

-- A from_code of the wrong shape (the earlier edition is not hosted, so shape only).
SELECT error('crosswalk: from_code of the wrong shape: ' || from_code) FROM (
    SELECT DISTINCT from_code FROM contract_crosswalk
    WHERE from_code IS NOT NULL
      AND NOT regexp_full_match(from_code, (SELECT from_pattern FROM contract_crosswalk_shape))
    ORDER BY from_code LIMIT 1);

-- More than one release.
SELECT error('crosswalk: more than one release: ' || string_agg(release, ', ' ORDER BY release))
FROM (SELECT DISTINCT release FROM contract_crosswalk WHERE release IS NOT NULL)
HAVING count(*) > 1;

CREATE OR REPLACE TABLE contract_crosswalk_result AS SELECT count(*) AS rows_checked FROM contract_crosswalk;
