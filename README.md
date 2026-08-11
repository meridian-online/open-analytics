# Meridian open datasets

Machine-readable descriptors, provenance and the request queue for the open
datasets published at **[meridian.online/datasets](https://meridian.online/datasets)**.

Every dataset is an immutable Parquet file on a public, zero-egress endpoint.
Query them from anywhere — no account, no key:

```sql
INSTALL ducklake; INSTALL httpfs;
ATTACH 'ducklake:https://openlake.meridian.online/catalog/open.ducklake' AS meridian (READ_ONLY);
SELECT * FROM meridian.gleif LIMIT 10;
```

Or read a single file directly:

```sql
SELECT * FROM read_parquet('https://openlake.meridian.online/naics.parquet');
```

## Datasets

| Dataset | Rows | License | Source | Descriptor |
|---|---|---|---|---|
| GLEIF — Legal Entity Identifiers | 3.36M | CC0-1.0 | [GLEIF](https://www.gleif.org/) | [datapackage.json](datasets/gleif/datapackage.json) |
| SEC EDGAR — Company Tickers | 10,415 | Public domain | [SEC](https://www.sec.gov/) | [datapackage.json](datasets/edgar/datapackage.json) |
| NAICS — Industry Classification (2022) | 2,125 | Public domain | [U.S. Census Bureau](https://www.census.gov/naics/) | [datapackage.json](datasets/naics/datapackage.json) |
| EDGAR ↔ GLEIF — company-to-LEI crosswalk | 6,570 | CC0-1.0 | SEC + GLEIF | [datapackage.json](datasets/edgar_gleif/datapackage.json) |

Each dataset carries a [Data Package](https://datapackage.org/) descriptor
(`datasets/<name>/datapackage.json`) with the canonical download URL, byte
size, SHA-256 hash, and a Table Schema. Column types and constraints are
inferred by [finetype](https://github.com/meridian-online/finetype) from the
published data — the `x-finetype-*` fields carry the semantic type and its
confidence.

Crosswalk datasets additionally declare their relationships as Frictionless
`foreignKeys` in the schema, each annotated with the resolution evidence:
`x-status` (confirmed / candidate / ambiguous, or per-row), `x-confidence`
(0–1, or a pointer to a per-row column), `x-evidence` (the match method,
blocking rules and precision), and `x-package` (the URL of the foreign
dataset's descriptor, since a Frictionless `reference.resource` resolves only
within a package). See
[`datasets/edgar_gleif`](datasets/edgar_gleif/datapackage.json).

## Checks

`scripts/check_descriptors.py` reads every `datasets/*/datapackage.json` back
against the file at its `resources[].path` and exits non-zero on any
disagreement — a declared field the Parquet does not have, a Parquet column the
descriptor does not declare, a Frictionless `type` the physical type cannot be
read as, a value outside `constraints.pattern` / `minLength` / `maxLength` /
`enum` / `required`, a wrong `bytes`, or a `foreignKey` whose two ends are
declared with incompatible types. Foreign keys resolve across every package in
this repository, so the cross-package references described above are checked
rather than skipped. A constraint keyword the script does not evaluate is an
error, not a pass.

```sh
pip install duckdb
python scripts/check_descriptors.py            # 0 conformant · 1 disagreement · 2 could not check
python scripts/test_check_descriptors.py       # the self-test: the check on deliberately broken fixtures
```

Both run on every push and pull request, and weekly, from
[`.github/workflows/descriptors.yml`](.github/workflows/descriptors.yml).

## Request a dataset

The catalog grows by request. **[Open a dataset request](../../issues/new?template=dataset-request.yml)**
— tell us what public data you keep reaching for and we'll tell you honestly
whether and when we can ship it.

Found a data error? **[Report it](../../issues/new?template=data-error.yml)** —
corrections are published in place.

## What's coming here

- Per-dataset build recipes: the pipeline that turns each official source
  release into the published Parquet, so every byte is reproducible.
- Descriptors generated as part of the publish pipeline (today they are
  produced with finetype and checked in by hand).

## License

Code and descriptors in this repository are MIT-licensed. The datasets
themselves are **not** covered by this repository's license — each carries its
own open license (CC0 or public domain today), stated in its descriptor and on
its dataset page.
