-- The release, and proof that the three fetched files are the ones at that commit.
--
-- OurAirports' files carry no date. Two fetches of the data repository's latest commit
-- on main bracket the data: release_before.json and release_after.json, GitHub's answer
-- before and after. Inside the bracket, tree.json is GitHub's git tree of the
-- repository's top folder at main, which gives each file's git blob sha.
-- raw.githubusercontent.com caches each file for minutes, so a run just after a commit
-- can see the new commit in the API and still be served the previous files. This model
-- hashes each fetched file as git does and holds it to the tree. GitHub's API answers are
-- cached for a minute as well, so the commit and the tree can come from different
-- moments: the model also hashes the tree as git does, from its entries, and holds it to
-- the tree sha each commit names.
--
-- It fails the run when the two commits differ, when a commit date does not parse, when
-- the tree lacks one of the three files, when a fetched file's blob sha is not the
-- tree's, or when the tree is not the one the commit names (or GitHub truncated it).
-- Otherwise it writes one row, the commit's sha and its UTC date, to
-- build/ourairports/release.csv, which load reads. GitHub gives the date in UTC with a
-- trailing Z, so it is parsed as written, with no time zone conversion.

COPY (
  WITH release_before AS (
    SELECT sha, "commit".committer.date AS committed, "commit".tree.sha AS tree_sha
    FROM read_json('build/ourairports/release_before.json',
                   columns = {'sha': 'VARCHAR', 'commit': 'STRUCT(committer STRUCT(date VARCHAR), tree STRUCT(sha VARCHAR))'})
  ),
  release_after AS (
    SELECT sha, "commit".committer.date AS committed, "commit".tree.sha AS tree_sha
    FROM read_json('build/ourairports/release_after.json',
                   columns = {'sha': 'VARCHAR', 'commit': 'STRUCT(committer STRUCT(date VARCHAR), tree STRUCT(sha VARCHAR))'})
  ),
  tree_file AS (
    SELECT tree, truncated
    FROM read_json('build/ourairports/tree.json',
                   columns = {'tree': 'STRUCT(path VARCHAR, mode VARCHAR, type VARCHAR, sha VARCHAR)[]',
                              'truncated': 'BOOLEAN'})
  ),
  tree_entries AS (
    SELECT e.path AS name, e.mode, e.type, e.sha
    FROM (SELECT unnest(tree) AS e FROM tree_file)
  ),
  -- The tree object as git writes it, '<mode> <name>' NUL and the 20-byte sha for each
  -- entry in git's order (a folder sorts as if its name ended in '/'), and its sha.
  tree_hash AS (
    SELECT sha1(('tree ' || octet_length(body) || chr(0))::BLOB || body) AS sha
    FROM (SELECT unhex(string_agg(hex(mode || ' ' || name) || '00' || sha, ''
                                  ORDER BY (name || CASE WHEN type = 'tree' THEN '/' ELSE '' END)::BLOB)) AS body
          FROM tree_entries)
  ),
  fetched AS (
    SELECT regexp_extract(filename, '[^/]+$') AS name,
           sha1(('blob ' || octet_length(content) || chr(0))::BLOB || content) AS sha
    FROM read_blob(['build/ourairports/airports.csv', 'build/ourairports/countries.csv',
                    'build/ourairports/regions.csv'])
  ),
  bracket AS (
    SELECT CASE
             WHEN (SELECT count(*) FROM release_before) <> 1
               OR (SELECT count(*) FROM release_after) <> 1
               THEN error('expected one commit in each OurAirports release file')
             WHEN (SELECT sha FROM release_before) IS DISTINCT FROM (SELECT sha FROM release_after)
               THEN error('OurAirports changed during the fetch: commit '
                          || coalesce((SELECT sha FROM release_before), 'NULL') || ' before, '
                          || coalesce((SELECT sha FROM release_after), 'NULL') || ' after')
             WHEN try_strptime((SELECT committed FROM release_before), '%Y-%m-%dT%H:%M:%SZ') IS NULL
               THEN error('OurAirports commit date does not parse: '
                          || coalesce((SELECT committed FROM release_before), 'NULL'))
             WHEN try_strptime((SELECT committed FROM release_after), '%Y-%m-%dT%H:%M:%SZ') IS NULL
               THEN error('OurAirports commit date does not parse: '
                          || coalesce((SELECT committed FROM release_after), 'NULL'))
             ELSE (SELECT sha FROM release_before)
           END AS commit_sha,
           strftime(try_strptime((SELECT committed FROM release_before), '%Y-%m-%dT%H:%M:%SZ'),
                    '%Y-%m-%d') AS release
  ),
  -- Each file against the tree, after the bracket, so a changed commit is reported
  -- as one rather than as a file that does not match.
  files AS (
    SELECT CASE
             WHEN (SELECT commit_sha FROM bracket) IS NULL THEN NULL
             WHEN l.sha IS NULL
               THEN error('OurAirports tree at main lacks ' || f.name)
             WHEN l.sha <> f.sha
               THEN error(f.name || ' is not the file at commit ' || (SELECT commit_sha FROM bracket)
                          || '; GitHub''s cache may still hold the previous version, so rerun later'
                          || ' (fetched blob ' || f.sha || ', tree ' || l.sha || ')')
             ELSE true END AS ok
    FROM fetched f LEFT JOIN tree_entries l USING (name)
  ),
  checked AS (
    SELECT CASE WHEN (SELECT count(*) FROM files WHERE ok) <> 3
                THEN error('expected three fetched OurAirports files to check')
              WHEN (SELECT count(*) FROM tree_file) <> 1 OR (SELECT truncated FROM tree_file) IS NOT false
                THEN error('OurAirports tree at main is missing or truncated')
              WHEN (SELECT sha FROM tree_hash) IS DISTINCT FROM (SELECT tree_sha FROM release_before)
                OR (SELECT sha FROM tree_hash) IS DISTINCT FROM (SELECT tree_sha FROM release_after)
                THEN error('the fetched tree is not the tree of commit ' || (SELECT commit_sha FROM bracket)
                           || ': tree ' || coalesce((SELECT sha FROM tree_hash), 'NULL') || ', commit names '
                           || coalesce((SELECT tree_sha FROM release_before), 'NULL')
                           || '; GitHub''s API cache may hold an answer from another moment, so rerun later')
                ELSE true END AS ok
  )
  SELECT b.commit_sha, b.release FROM bracket b, checked c WHERE c.ok
) TO 'build/ourairports/release.csv' (FORMAT csv, HEADER true);
