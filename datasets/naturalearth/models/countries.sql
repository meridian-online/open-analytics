-- The countries_110m table: one row per Natural Earth country, keyed on iso_a3 (see
-- load.sql for how the key is chosen), with its shape as GeoJSON text, so a reader
-- needs neither the spatial extension nor the network to draw it.
CREATE OR REPLACE TABLE countries_110m_out AS
SELECT
  iso_a3,
  iso_a2,
  NAME AS name,
  CONTINENT AS continent,
  ST_AsGeoJSON(geom) AS geom
FROM ne_countries;
