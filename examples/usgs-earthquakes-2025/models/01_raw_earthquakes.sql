-- The USGS's own 2025 earthquakes of magnitude 4.5 and up, in seven columns.
--
-- Only events the USGS's own network reported (net = 'us') of type 'earthquake' are
-- kept: the other contributing networks' terms are their own, and quarry blasts and
-- other event types are not earthquakes. The identifiers, the networks' bookkeeping
-- and the error estimates are left out. `time` arrives as an ISO timestamp in UTC and
-- is kept as a UTC timestamp whatever the session's time zone.
CREATE OR REPLACE TABLE raw_earthquakes AS
SELECT
  time AT TIME ZONE 'UTC' AS time,
  latitude, longitude, depth, mag, magType, place
FROM read_csv('build/earthquakes_2025.csv')
WHERE net = 'us' AND type = 'earthquake';

-- The query asked for 2025 and magnitude 4.5 and up, so a row outside either is not
-- this query's answer. The count is checked loosely, because the USGS revises events
-- and a later run can differ by a few rows from the 8,502 kept on 2026-10-26; a count
-- outside 8,000 to 9,000 means the response or the filter has changed, not an event.
SELECT CASE
  WHEN v.outside > 0 THEN error(
    'raw_earthquakes: ' || v.outside || ' event(s) outside 2025 or below magnitude 4.5. '
    || 'The USGS query did not return what it asked for.')
  WHEN v.rows NOT BETWEEN 8000 AND 9000 THEN error(
    'raw_earthquakes: ' || v.rows || ' USGS earthquake(s) kept, against 8,502 on 2026-10-26. '
    || 'Check whether the USGS changed the response or the net and type columns.') END
FROM (
  SELECT count(*) AS rows,
         count(*) FILTER (WHERE year(time) <> 2025 OR mag < 4.5) AS outside
  FROM raw_earthquakes
) v;
