-- The trips a dashboard reads: those picked up in January 2026, with the hour and day
-- of the week, the distance capped at 30 miles, whether the pickup was at one of the
-- three airports, and whether the record is complete.
--
-- The month's file carries a few trips whose pickup falls outside January, which is
-- why the WHERE clause is there. Zones 132, 138 and 1 are JFK, LaGuardia and Newark in
-- the TLC's taxi zone lookup.
CREATE OR REPLACE TABLE trips AS
SELECT
  tpep_pickup_datetime                    AS pickup_time,
  CAST(tpep_pickup_datetime AS DATE)      AS pickup_date,
  hour(tpep_pickup_datetime)              AS pickup_hour,
  CAST(isodow(tpep_pickup_datetime) AS VARCHAR) || ' '
    || strftime(tpep_pickup_datetime, '%a') AS weekday,
  least(trip_distance, 30)                AS trip_miles,
  CASE PULocationID
    WHEN 132 THEN 'JFK Airport'
    WHEN 138 THEN 'LaGuardia Airport'
    WHEN 1 THEN 'Newark Airport'
    ELSE 'elsewhere'
  END AS pickup_place,
  CASE
    WHEN total_amount < 0 THEN 'negative fare'
    WHEN trip_distance = 0 THEN 'zero distance'
    WHEN passenger_count IS NULL THEN 'no passenger count'
    ELSE 'complete'
  END AS trip_record
FROM raw_trips
WHERE tpep_pickup_datetime >= TIMESTAMP '2026-01-01'
  AND tpep_pickup_datetime < TIMESTAMP '2026-02-01';
