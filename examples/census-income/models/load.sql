-- The 1994 US Census "Adult" extract, into a shape a dashboard can read.
--
-- Two source quirks:
--   • Missing values are the literal string '?' in three columns — 1,836 workclass,
--     1,843 occupation and 583 native.country, counted rather than estimated. They
--     become real NULLs here, because '?' would otherwise arrive as a category and be
--     drawn as one: a count-plot would show a fourth-largest "occupation" that is not
--     an occupation.
--   • The source column names are dot-separated ('education.num'), which every
--     downstream reader has to quote. Renamed to snake_case.
--
-- `income` is kept as the source's '<=50K' / '>50K' strings rather than being folded
-- to a boolean: it is the label this dataset is famous for, and a reader recognising
-- it by its own values is worth more than a tidier type.
CREATE OR REPLACE TABLE census_income AS
SELECT
  age,
  nullif(workclass, '?')             AS workclass,
  fnlwgt                             AS final_weight,
  education,
  "education.num"                    AS education_num,
  "marital.status"                   AS marital_status,
  nullif(occupation, '?')            AS occupation,
  relationship,
  race,
  sex,
  "capital.gain"                     AS capital_gain,
  "capital.loss"                     AS capital_loss,
  "hours.per.week"                   AS hours_per_week,
  nullif("native.country", '?')      AS native_country,
  income
FROM read_csv_auto('build/adult.csv');
