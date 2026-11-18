# Test fixtures for `edgar_gleif`

Real rows of each source, cut from the inputs a full run fetched in November 2026, for the tests in `../tests.yaml`. Nothing in them is invented: each row is as the source published it, and only rows were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `edgar.parquet` | `fetch_edgar`, the lake's `sec/company_tickers_exchange.parquet` | The 10 ticker rows of seven companies: Boeing (12927), Coca-Cola (21344), WD-40 (105132), Apple (320193), Disney (1744489), Inflection Point Acquisition Corp. VII (2088805, filed earlier as Columbus Circle Capital Corp II) and Enviri (2104052) |
| `cik_lookup.txt` | `fetch_cik_lookup`, SEC's `cik-lookup-data.txt` | Every line for those seven CIKs, former names included, and the line for the T. Rowe Price registrant (1795351) N-CEN names |
| `gleif.parquet` | `fetch_gleif`, the lake's `gleif/lei.parquet` | 24 registrations: each company's own, and the near-misses GLEIF holds under its name. Boeing's two retirement trusts, Boeing Consulting GmbH and Boeing Way, LLC; Coca-Cola's lapsed Australian registration, its retirement trust, Coca-Cola Consolidated and a Greek bottler; Disney's Singapore subsidiary and retirement trust; Apple Ltd and APPLE CORP LIMITED, both lapsed; WD-40 COMPANY LIMITED, issued in the UK beside the lapsed US WD-40 Company; both issued US-DE registrations named ENVIRI CORPORATION; Columbus Circle Capital Corp I. And the three fund LEIs below, which the identifier gate needs GLEIF to publish |
| `gleif_ra_sec.jsonl` | `fetch_gleif_sec_registrations`, GLEIF's SEC registrations (RA000665), as its raw `lei-records` | One fund series, S000005113: its whole record, as the fetch writes it |
| `ncen.zip` | all four `fetch_ncen_*` steps, SEC's quarterly Form N-CEN data sets | `REGISTRANT.tsv` and `FUND_REPORTED_INFO.tsv` with one row each from 2026q2: the T. Rowe Price Exchange-Traded Funds registrant and its High Income Municipal ETF series |
| `scored_pairs_mixed_orientation.parquet` | `score_pairs`, in the orientation test | The 19 pairs Splink scores on these fixtures, with every other pair written GLEIF-first (`source_dataset_l = 'gleif'`), its `_l` and `_r` columns swapped |

The lake fixtures:

| Fixture | Stands in for | Kept |
|---|---|---|
| `lake_descriptors.json` | `emit_descriptors`, the bundle of every published table's descriptor | The same bundle as `../../naics/testdata/lake_descriptors.json`, as `python scripts/emit_descriptor.py --all --bundle <file>` emitted it. It needs regenerating when a table it holds is republished with different declarations |
| `described.json` | `describe`, in the test that runs the check before publish for real | The descriptor of `library.edgar_lei`, as it is in `lake_descriptors.json` |
| `check_before_publish.json` | `check_before_publish`, in the tests that run `describe` on the fixture | A stand-in report, which no step left in a test reads |

`scored_pairs_mixed_orientation.parquet` holds the `e<n>` row numbers `splink_inputs.sql` gives the EDGAR side of these fixtures. If a change to the EDGAR fixtures or to that numbering moves them, regenerate it from a run on the fixtures: take `build/scored_pairs.parquet` and swap the `source_dataset`, `uid` and `nname` columns of every other pair in `ORDER BY uid_l, uid_r`.
