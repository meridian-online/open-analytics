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

## Ask a dataset what it is

**Not live yet — the mechanism is in place and no published object carries it.**
Each Protocol's last step stamps the descriptor into the Parquet it just built,
but every object on the endpoint was published before that step existed, so the
query below returns nothing today. It returns the descriptor for any dataset
rebuilt and republished from now on. `check_descriptors.py` prints how many of
the published resources carry their own description on every run, so this
paragraph stops being true visibly rather than silently.

A stamped dataset carries its description **inside the file**, in the Parquet
footer, so a URL is the only thing you need to know about it — no second file, no
registry, and no knowledge that this repository exists:

```sql
SELECT decode(value)
FROM parquet_kv_metadata('https://openlake.meridian.online/naics.parquet')
WHERE decode(key) = 'datapackage.json';
```

That returns the whole [Data Package](https://datapackage.org/) descriptor — the
Table Schema with every field's type and constraints, the primary key, any
foreign keys with their resolution evidence, the licence and the sources. It is a
**range read of the footer**, not a download: DuckDB fetches the last few
kilobytes and stops.

The same descriptor, without the SQL:

```sh
python scripts/stamp_descriptor.py read https://openlake.meridian.online/naics.parquet
```

The embedded copy omits the resource's `bytes` and `hash`, and only those. A file
cannot state its own size or its own digest — writing the value changes the value
— so those two stay in this repository's copy, which is measured against the
finished object. Everything else is identical, and
[`scripts/check_descriptors.py`](scripts/check_descriptors.py) holds the two
copies to each other.

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
against the file at its `resources[].path`, **and reads the description the file
itself carries back against both**, and exits non-zero on any disagreement — a declared field the Parquet does not have, a Parquet column the
descriptor does not declare, a Frictionless `type` the physical type cannot be
read as, a value outside `constraints.pattern` / `minLength` / `maxLength` /
`enum` / `required`, a wrong `bytes`, or a `foreignKey` whose two ends are
declared with incompatible types. Foreign keys resolve across every package in
this repository, so the cross-package references described above are checked
rather than skipped. A constraint keyword the script does not evaluate is an
error, not a pass.

The self-described half is checked by the same rules: a footer descriptor that
disagrees with this repository's copy, declares a field the file does not have,
declares a type its column contradicts, or states the file's own `bytes`, is a
disagreement. An object carrying no description is not — nothing published
carries one yet, and a check that demanded one would report every correct object
as wrong. `--require-self-description` turns that into a refusal for the publish
path, once a stamped object is what gets published.

`scripts/publish_dataset.py` reads in the other direction — from the endpoint
back into the descriptor. `verify` re-reads every published object in full and
refuses when a descriptor and its endpoint disagree on either `bytes` or `hash`.
`restamp` writes what the endpoint currently serves into the descriptor. Its
`publish` subcommand exists to make uploading and declaring one act rather than
two, but nothing calls it yet — see *What's coming here*.

`scripts/stamp_descriptor.py` writes the third direction — from the descriptor
into the object. `stamp` puts the finished descriptor in the Parquet's footer and
re-measures the resource's `bytes` and `hash` from the stamped file, because
stamping lengthens it past the figures the describe step took off the unstamped
build. Everything before the footer is compared byte for byte, so the `order_by`
clause that makes each export reproducible still decides the data bytes and a
stamp that moved one is refused with the original left in place. `read` is the
consumer's half.

```sh
pip install duckdb
python scripts/check_descriptors.py            # 0 conformant · 1 disagreement · 2 could not check
python scripts/test_check_descriptors.py       # the self-test: the check on deliberately broken fixtures
python scripts/test_stamp_descriptor.py        # the self-test: the stamp lands, the data does not move
python scripts/test_publish_dataset.py         # the self-test: publish against a scratch object store
python scripts/publish_dataset.py verify       # every declared bytes + hash against the object served
```

`scripts/test_self_description_needs_no_repository.py` is the one that cannot be
run casually: it asks a stamped object what it is with `github.com` and
`raw.githubusercontent.com` unreachable, watches the repository copy stop being
fetchable, and asserts the object still answers. It **refuses to run** while a
repository host is still reachable, because a blocked-host demonstration that
quietly ran unblocked would pass forever and prove nothing. CI blackholes those
hosts in `/etc/hosts` after checkout and runs it there.

The first four run on every push and pull request, and weekly, from
[`.github/workflows/descriptors.yml`](.github/workflows/descriptors.yml).
`verify` re-hashes every file, so it runs on merge, weekly and on demand rather
than per pull request — `bytes` is covered per pull request by a one-byte range
read, and `hash` cannot be.

## Request a dataset

The catalog grows by request. **[Open a dataset request](../../issues/new?template=dataset-request.yml)**
— tell us what public data you keep reaching for and we'll tell you honestly
whether and when we can ship it.

Found a data error? **[Report it](../../issues/new?template=data-error.yml)** —
corrections are published in place.

## What's coming here

- Per-dataset build recipes: the pipeline that turns each official source
  release into the published Parquet, so every byte is reproducible.
- Descriptors generated as part of the publish pipeline. Today the schema half
  is produced with finetype and checked in by hand, and `bytes` and `hash` are
  written by a hand-run `publish_dataset.py restamp` against the endpoint.
- **A published object that carries its own description.** The stamping step is
  wired into all four Protocols and nothing has been rebuilt and republished
  through it yet, so `parquet_kv_metadata` on a live URL still returns nothing.
  See *Ask a dataset what it is* above.
- **The publish pipeline calling `publish_dataset.py publish`.** The seam that
  makes uploading an object and declaring its size one act is written and
  tested, but the out-of-repo pipeline that actually uploads does not call it.
  Until it does, a republish can still move an object without its descriptor —
  which is how `edgar` came to advertise a size the endpoint did not serve.

## License

Code and descriptors in this repository are MIT-licensed. The datasets
themselves are **not** covered by this repository's license — each carries its
own open license (CC0 or public domain today), stated in its descriptor and on
its dataset page.
