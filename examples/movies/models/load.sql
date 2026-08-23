-- Movies, from the vega-datasets distribution, into a shape a dashboard can read.
--
-- Three source quirks, each handled here rather than left for a reader to hit:
--   • `Title` arrives as DuckDB's JSON type, not VARCHAR, because the column is
--     type-HETEROGENEOUS: nine of the 3,201 titles are bare JSON numbers rather than
--     strings — 1776, 2012, 300 and their kind — so read_json_auto widens the column
--     to JSON. `->>'$'` takes the scalar out. vega-datasets documents this in its own
--     datapackage.json as a deliberate teaching feature of the dataset, alongside the
--     non-ISO date format below. (An earlier version of this comment blamed the single
--     null title. That is wrong: a column of strings and nulls stays VARCHAR.)
--     This matters beyond tidiness: the descriptor step types COLUMNS, and a JSON
--     column is not a thing it has a useful opinion about.
--   • `Release Date` is 'Jun 12 1998' — a real date wearing a string. All 3,201 parse
--     with '%b %d %Y', checked before this was written, so try_strptime never silently
--     drops one here. It stays `try_` so that a future vega-datasets version which
--     changes the format fails as NULLs a reader can count rather than as an error
--     mid-run.
--   • The source's column names carry spaces. They are renamed to snake_case, which is
--     what the rest of this repository's published tables use.
CREATE OR REPLACE TABLE movies AS
SELECT
  Title ->> '$'                                        AS title,
  try_strptime("Release Date", '%b %d %Y')::DATE       AS release_date,
  "MPAA Rating"                                        AS mpaa_rating,
  "Major Genre"                                        AS major_genre,
  "Creative Type"                                      AS creative_type,
  Source                                               AS source_material,
  Distributor                                          AS distributor,
  Director                                             AS director,
  "Running Time min"                                   AS running_time_min,
  "Production Budget"                                  AS production_budget,
  "US Gross"                                           AS us_gross,
  "Worldwide Gross"                                    AS worldwide_gross,
  "US DVD Sales"                                       AS us_dvd_sales,
  "IMDB Rating"                                        AS imdb_rating,
  "IMDB Votes"                                         AS imdb_votes,
  "Rotten Tomatoes Rating"                             AS rotten_tomatoes_rating
FROM read_json_auto('build/movies.json');
