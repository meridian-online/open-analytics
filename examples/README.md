# Demo Protocols — third-party data, referenced by URL, published nowhere

**Nothing in this directory is a Meridian dataset, and nothing here is a candidate to become one.**

Each subdirectory is an [arcform](https://github.com/meridian-online/arcform) Protocol that fetches a third-party file from its canonical URL, transforms it in DuckDB, exports a Parquet to the machine that ran it, and emits a Frictionless [Data Package](https://datapackage.org/) descriptor for what it built. They exist so that the analysis surface has real data to be demonstrated against, with a reproducible recipe behind it instead of a binary somebody made by hand.

**The distinction from `../datasets/` is the whole point of keeping them apart.** A Protocol under `datasets/` terminates in an object Meridian serves at `openlake.meridian.online`, and everything about the licensing of that act applies to it. A Protocol under `examples/` terminates on your disk. Meridian hosts none of this data, redistributes none of it, and adds no second URL for anything that already has one. Running one of these is the same act as running a shell script that curls a file — the licence question attaches to whoever runs it and what they do with the bytes, not to the recipe.

## The three

| Protocol | Source | Licence at source | Rows |
|---|---|---|---|
| `movies` | vega-datasets 3.2.1 | BSD-3-Clause | 3,201 |
| `census-income` | `scikit-learn/adult-census-income` @ `fbeef6ec` | CC0-1.0 | 32,561 |
| `california-housing` | `gvlassis/california_housing` @ `17110e60` | MIT | 16,640 |

**Each licence was read at its source, and for `movies` that turned out to mean no licence at all.** vega-datasets declares a per-resource licence for **58 of its 73 resources**, and `movies` is one of the 15 it does not — so the silence is a choice rather than an omission, and nothing is asserted here either. The BSD-3-Clause on that repository covers its code and infrastructure, and its own `datapackage.json` says so. An earlier version of this file labelled `movies` BSD-3-Clause and cited a `LICENSE` URL that returns 404; that was the repository's code licence applied to data it only redistributes, which is exactly the mistake this paragraph now exists to prevent.

Every `sha256:` in these manifests was computed from the fetched bytes here, and the two Hugging Face URLs address a 40-character commit revision rather than a default branch, so the revision is pinned as well as the content.

`census-income` describes individuals recorded by the 1994 US Census. It is used here as a *shape of data*, and its columns use that instrument's categories rather than ones anybody would choose today.

## Running one

```sh
cd examples/movies && arc run
```

You need `arc` on `PATH` and a DuckDB the manifest's `engine_version` accepts. Each run writes into that Protocol's `build/`, which is git-ignored.

## Two things worth knowing before you rely on a run

**The exports are byte-reproducible, and `ORDER BY ALL` is why.** Two cold runs of all three Protocols produced identical SHA-256 digests — `5ab2c92b…`, `3021b408…` and `43b5fbc7…`. That ordering is deliberately *not* claimed to be a total order: 24 of `census-income`'s 32,561 rows are exact duplicates of another row, so ties exist. A tie between two rows that are equal in every column cannot change the output bytes, because the rows themselves are identical — which is the weaker condition that actually holds, and it holds without inventing a surrogate key.

**A `sha256:` pin is provenance here, not an integrity gate.** On a genuine transfer it fails closed — corrupt a pin against an empty cache and the run stops, naming both hashes. But arcform's shared fetch cache is keyed by **URL**, and on a cache hit the pin is not consulted: with the cache warm, a manifest declaring `sha256: 0000…0` builds to exit 0 and reuses the cached bytes. Reproduced on `california-housing`. So the pin protects the first fetch on a cold machine and nothing after it, and changing a pin to name different bytes will not fetch them on a machine that already has the old ones.

**Rebuild by removing `build/`, not by deleting the exported Parquet.** Both work on a current `arc`: deleting the Parquet alone re-runs the export and describe steps and puts it back. On an `arc` built before 2026-08-19 it did not — the run reported every step fresh and left the file absent — so if a rebuild appears to do nothing, check `arc` is current before concluding anything about the Protocol.

## What finetype makes of them

The `describe` step types every column semantically, and these three are a fair sample of what that buys and what it still gets wrong. `california-housing`'s coordinates come back as `geography.coordinate.latitude` and `.longitude` rather than as two doubles, and `movies.release_date` as `datetime.date.iso` — which is the difference between a dashboard that can choose a map and a date axis, and one that draws nine histograms. `movies.director` types as `identity.person.full_name`.

It is not uniformly right, and the miss is recorded here rather than quietly accepted: `census-income.final_weight` types as `identity.person.weight`. It is a Census *sampling* weight — how many people in the population the record stands for — and not a property of anybody's body.
