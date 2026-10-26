-- Every invoice line of both of UCI's sheets, each line once, with the Customer ID
-- replaced by what a dashboard needs from it.
--
-- The two sheets overlap: 'Year 2009-2010' runs to 9 December 2010, and
-- 'Year 2010-2011' starts on 1 December 2010. A line is taken from the first sheet only
-- when it is earlier than anything on the second. InvoiceDate is an Excel serial date,
-- days since 1899-12-30, rounded to the second.
--
-- Only sales and their cancellations are kept: an invoice is six digits, with a C in
-- front for a cancellation. The workbook also holds six bad-debt adjustments numbered
-- with an A, which are bookkeeping rather than anybody's purchase, and one of them
-- (A563185, about £11,062) would otherwise count as the largest order of its month.
--
-- The Customer ID is read here and nowhere else. It becomes `customer_id_recorded`,
-- `customer_first_month` (the month of the customer's first order that is not a
-- cancellation) and `customer_order_number` (the order's place in that customer's
-- sequence), and the ID itself is not kept.
CREATE OR REPLACE TABLE retail_lines AS
WITH sheets AS (
  SELECT 'Year 2009-2010' AS sheet, *
  FROM read_xlsx('build/uci/online_retail_II.xlsx', sheet = 'Year 2009-2010', all_varchar = true)
  UNION ALL
  SELECT 'Year 2010-2011' AS sheet, *
  FROM read_xlsx('build/uci/online_retail_II.xlsx', sheet = 'Year 2010-2011', all_varchar = true)
),
typed AS (
  SELECT
    sheet,
    Invoice AS invoice,
    StockCode AS stock_code,
    Description AS description,
    CAST(Quantity AS INTEGER) AS quantity,
    TIMESTAMP '1899-12-30'
      + to_microseconds(CAST(round(CAST(InvoiceDate AS DOUBLE) * 86400) AS BIGINT) * 1000000)
      AS invoice_time,
    CAST(Price AS DOUBLE) AS price,
    "Customer ID" AS customer,
    Country AS country
  FROM sheets
),
once AS (
  SELECT * FROM typed
  WHERE (sheet = 'Year 2010-2011'
         OR invoice_time < (SELECT min(invoice_time) FROM typed WHERE sheet = 'Year 2010-2011'))
    AND regexp_full_match(invoice, 'C?[0-9]{6}')
)
SELECT
  invoice,
  stock_code,
  description,
  quantity,
  invoice_time,
  price,
  country,
  customer IS NOT NULL AS customer_id_recorded,
  CASE WHEN customer IS NOT NULL THEN CAST(
    min(CASE WHEN invoice NOT LIKE 'C%' THEN date_trunc('month', invoice_time) END)
      OVER (PARTITION BY customer) AS DATE) END AS customer_first_month,
  CASE WHEN customer IS NOT NULL AND invoice NOT LIKE 'C%' THEN
    dense_rank() OVER (PARTITION BY customer, invoice LIKE 'C%' ORDER BY invoice_time, invoice)
  END AS customer_order_number
FROM once;
