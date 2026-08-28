# supergpqa — 26,529 graduate-level questions, and a map of what they are about

A demo Protocol. It fetches one JSONL file from Hugging Face by pinned URL and revision, shapes it in DuckDB, embeds the `question` column, reduces the vectors to two coordinates with UMAP, and writes `build/supergpqa.parquet` plus a Frictionless descriptor for it. **Nothing is published.** Meridian hosts none of this data and adds no second URL for a file that already has one; see [`../README.md`](../README.md) for why `examples/` and `datasets/` are different things.

```sh
cd examples/supergpqa && arc run
```

## The licence obligation is attribution, and it is checked on every run

SuperGPQA is **ODC-BY**. That licence asserts no copyright over the underlying content; what it asks for instead is *credit to the original creators of any third-party data included* and *compliance with the respective licences of the referenced datasets*. The source card names **fifteen** such datasets, so a line saying "and fifteen upstreams" discharges nothing — credit to fifteen creators means fifteen creators named.

All fifteen ride in `descriptor.overrides.json` under `sources`, each by name and by the card's own link, and **`models/corpus.sql` stops the run when one goes missing from that file**, naming the ones it could not find. The gate holds two independent copies of the list to each other: the fifteen names transcribed into the SQL from the card at revision `4430d445`, and the `sources` entries in the descriptor. Attribution is the one obligation in this Protocol that a reader cannot check by looking at the data, so it is the one that gets a gate rather than a sentence.

Anyone using this output carries those obligations forward. The licence travels with the data, not with the recipe.

## What `projection_x` and `projection_y` support — and what they do not

**Read this map as regions, not as distances.** UMAP preserves *which points fall together*; it does not preserve *how far apart* any two of them are. A dense patch of this map is a subfield — Circuits and Systems questions land near other Circuits and Systems questions — and the width of the gap between two patches is an artifact of the layout, not a measurement of how different two subjects are. Comparing two distances on this map is reading something that is not there.

**There is no `neighbors` column here, and you cannot recover one from these two coordinates.** Apple's Embedding Atlas gallery ships a `neighbors` column beside its `projection_x`/`projection_y` for this dataset, precomputed out of band as a k-nearest-neighbour graph over the *full-dimensional* vectors. Nearest-neighbour lookup is a question about those vectors, and the two coordinates are what is left after 256 dimensions were thrown away to draw a picture. The nearest point on the picture is frequently not the nearest question. If you want neighbours, run this Protocol and read `build/supergpqa.embedded.parquet`, which carries the 256-float vector per row that the coordinates were reduced from — the export deliberately does not, because a vector column is 27 MB of numbers no chart reads.

**`projection_fit_id` is what makes two of these files comparable.** It is one value repeated on every row, fingerprinting the exact feature matrix and knobs the fit consumed. Two exports carrying the same `projection_fit_id` came out of one fit and a position in one means the same thing as a position in the other. Two carrying different values are two maps: UMAP is anchored to no absolute frame, so the same question can sit in opposite corners of two fits and nothing is wrong.

## Three things about the data, each of them a gate rather than a claim

`models/corpus.sql` is the second step of eight, before the embed and the projection, so a corpus that is not what this Protocol says it is costs a second rather than a full run.

**The options list is flattened, and the delimiter is `' || '` rather than `' | '`.** A list column is not a shape the export/describe pair in this directory has handled, so [`../scienceqa/models/load.sql`](../scienceqa/models/load.sql) set the precedent of joining one into a single delimited string. Its delimiter is not safe over this corpus: **31 of these 26,529 rows carry `' | '` inside an option**, so splitting `answer_options` on it would hand back the wrong options for those 31 and nothing would say so. `' || '` appears inside no option in this file — and that is the gate, not an assertion: if a future revision introduces one, the run stops.

**`answer_letter` selects `answer` out of `answer_options`.** The source records the correct answer twice, once as text and once as a letter A–J, and a chart reading the letter is trusting they agree. They do on all 26,529 rows. If a revision breaks that, a reader would have two answer columns disagreeing and no signal, so the run stops instead.

**`uuid` is unique**, which is the premise behind byte-reproducibility: it makes `ORDER BY ALL` a total order on both exports, and the order of `build/supergpqa.corpus.parquet` is what the UMAP fit is pinned to.

## Types are declared, not inferred

`read_json`'s auto-detection settles a schema from a sample of the file, which makes each column's type a property of what the sampler happened to look at rather than of this Protocol. `models/corpus.sql` names all ten source columns and their types instead, so a source that grew a field or changed one is a refusal at step 2 and not a silently different export. Same move, same reason, as `datasets/edgar`.

## Before it will run: the embedding extension

`text_embed@1` names a loadable DuckDB extension the Protocol puts on disk. It never downloads or installs one — an artifact fetched invisibly would be a graph edge that does not appear in the graph — and there is no published URL for this one today, so it is the one input here that does not arrive from a URL. Build it and place it:

```sh
git clone https://github.com/meridian-online/staticembed && cd staticembed && make release
cp build/release/staticembed.duckdb_extension <this directory>/vendor/
```

`vendor/` is git-ignored. Absent, the run stops in milliseconds naming the file it looked for, before `uv` is spawned. When that artifact has a URL, step 4 below becomes an `http_fetch@1` like step 1 and nothing else in the manifest moves.

## Reproducibility

Two cold runs — `rm -rf build && arc run` — produce a byte-identical export, sha256 `74cd964ff62403eeac95c31e8fa987ec169b3cdb5fe1d94d348dcc5ef859f7a1`, the same comparison the other Protocols in this directory hold themselves to. Three things make the projection land in the same place twice: the seed is frozen in the operator's script rather than exposed in `arcform.yaml`, the thread count is pinned to one before numpy and numba load, and row order is pinned by reading each Parquet single-threaded with insertion order preserved. `ORDER BY ALL` on **both** exports is what feeds that last one a stable order to preserve.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 8 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_supergpqa` | `op: http_fetch@1` |
| 2 | `corpus` | `sql: models/corpus.sql` |
| 3 | `export_corpus` | `op: parquet_export@1` |
| 4 | `embed_questions` | `op: text_embed@1` |
| 5 | `project_questions` | `op: umap_project@1` |
| 6 | `load` | `sql: models/load.sql` |
| 7 | `export_supergpqa` | `op: parquet_export@1` |
| 8 | `describe` | `op: datapackage_describe@1` |

<!-- /protocol-steps -->
