-- January 2026's yellow taxi trips as the TLC publishes them.
CREATE OR REPLACE TABLE raw_trips AS
SELECT * FROM read_parquet('build/yellow_tripdata_2026-01.parquet');
