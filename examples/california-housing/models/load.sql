-- California housing districts, into a shape a dashboard can read.
--
-- The source needs no repair — nine DOUBLE columns, no missing values, no string
-- coercion. The only work here is naming: the source uses the scikit-learn short
-- forms (MedInc, AveRooms) and this spells them out, because a tile in a generated
-- dashboard is labelled with the column name and 'AveOccup' is not a label.
--
-- ROW COUNT IS 16,640, NOT 20,640. This is the source's `train` split; the full
-- dataset has 20,640 districts. Stated because a reader who knows the dataset will
-- otherwise assume the difference is a bug here.
--
-- Latitude and Longitude are kept as plain measures. They are what makes this the
-- one example in the reference gallery whose map needs no embedding behind it: two
-- numeric columns are a map already.
CREATE OR REPLACE TABLE california_housing AS
SELECT
  MedInc       AS median_income,
  HouseAge     AS house_age,
  AveRooms     AS avg_rooms,
  AveBedrms    AS avg_bedrooms,
  Population   AS population,
  AveOccup     AS avg_occupancy,
  Latitude     AS latitude,
  Longitude    AS longitude,
  MedHouseVal  AS median_house_value
FROM read_parquet('build/california_housing.parquet.src');
