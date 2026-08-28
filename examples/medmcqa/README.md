# medmcqa — 182,822 medical exam questions, and a map of what they are about

A demo Protocol. It fetches one Parquet from Hugging Face by pinned URL and revision, embeds the `question` column, reduces the vectors to two coordinates with UMAP, and writes `build/medmcqa.parquet` plus a Frictionless descriptor for it. **Nothing is published.** Meridian hosts none of this data and adds no second URL for a file that already has one; see [`../README.md`](../README.md) for why `examples/` and `datasets/` are different things.

```sh
cd examples/medmcqa && arc run
```

## What `projection_x` and `projection_y` support — and what they do not

**Read this map as regions, not as distances.** UMAP preserves *which points fall together*; it does not preserve *how far apart* any two of them are. So a dense patch of this map is a topic — Microbiology questions land near other Microbiology questions — and the width of the gap between two patches is an artifact of the layout, not a measurement of how different the two topics are. Comparing two distances on this map is reading something that is not there.

**There is no `neighbors` column here, and you cannot recover one from these two coordinates.** Apple's Embedding Atlas gallery ships a `neighbors` column beside its `projection_x`/`projection_y` for this dataset, precomputed out of band as a k-nearest-neighbour graph over the *full-dimensional* vectors. Nearest-neighbour lookup is a question about those vectors, and the two coordinates are what is left after 256 dimensions were thrown away to draw a picture. The nearest point on the picture is frequently not the nearest question. If you want neighbours, run this Protocol and read `build/medmcqa.embedded.parquet`, which carries the 256-float vector per row that the coordinates were reduced from — the export deliberately does not, because a vector column is 187 MB of numbers no chart reads.

**`projection_fit_id` is what makes two of these files comparable.** It is one value repeated on every row, fingerprinting the exact feature matrix and knobs the fit consumed. Two exports carrying the same `projection_fit_id` came out of one fit and a position in one means the same thing as a position in the other. Two carrying different values are two maps: UMAP is not anchored to any absolute frame, so the same question can sit in opposite corners of two fits and nothing is wrong.

## The source, and what is unresolved about it

`openlifescienceai/medmcqa` at revision `91c6572c`, the **`train`** split — 182,822 rows of 182,822 train + 4,183 validation + 6,150 test.

**`train` is the split the Embedding Atlas gallery itself fetches**, which is why it is the one here: that gallery's cached projection Parquet for this dataset is named `b119434b`, and `b119434ba551517a6ec0ba1f7e0b4c029165ed284a4704f262ce37c791c493c5` is the sha256 of the train file. A reader comparing this output against that gallery is looking at the same corpus.

**`test` is not usable as a demo at all, and it is one URL and one hash away from here.** All 6,150 of its rows carry `cop = -1` — the answers withheld for leaderboard scoring — so the correct-option column is a constant and `answer_text` would be NULL on every row. `models/load.sql` gates on it, so making that swap is a failed run rather than a demo with no answers in it.

**What the full split costs, measured rather than feared: 4 minutes 1 second** wall for a cold end-to-end run on an 8-core M-series laptop, most of it the projection. `umap_project` is single-threaded by design — it pins the thread count before numpy and numba are imported, since a multi-threaded BLAS reduction is not reproducible and reproducibility is what this Protocol claims — so that cost grows with the corpus and does not fall to more cores. It is still four minutes, and an honest 182,822-point map is worth more than a fast 4,183-point one.

**The licence is not settled and this Protocol asserts none.** Three official surfaces say three things:

| surface | what it says |
|---|---|
| the Hugging Face repository's YAML frontmatter | `license: apache-2.0` |
| the same card's own *Licensing Information* section, at `91c6572c` | `[Needs More Information]` |
| `github.com/medmcqa/medmcqa`, the authors' own repository | an MIT `LICENSE.md`, over a tree of `train.py`, `model.py`, `dataset.py`, notebooks and `requirements.txt` — code, and no data |

A repository tag is not the licensor speaking, which is the finding [`../scienceqa/arcform.yaml`](../scienceqa/arcform.yaml) records against a mirror whose tag contradicted its own card body; and a code repository's licence is not a grant over data it links to, which is the finding [`../movies/arcform.yaml`](../movies/arcform.yaml) records against a BSD-3-Clause covering vega-datasets' infrastructure. Both apply here at once. So all three readings are written into the descriptor and none is relied on. Referencing a URL an end user may lawfully fetch is not redistribution and needs no grant; **treat the output as unlicensed third-party content** until someone has terms in writing from the licensor.

## Before it will run: the embedding extension

`text_embed@1` names a loadable DuckDB extension the Protocol puts on disk. It never downloads or installs one — an artifact fetched invisibly would be a graph edge that does not appear in the graph — and there is no published URL for this one today, so it is the one input here that does not arrive from a URL. Build it and place it:

```sh
git clone https://github.com/meridian-online/staticembed && cd staticembed && make release
cp build/release/staticembed.duckdb_extension <this directory>/vendor/
```

`vendor/` is git-ignored. Absent, the run stops in milliseconds naming the file it looked for, before `uv` is spawned. When that artifact has a URL, step 2 below becomes an `http_fetch@1` like step 1 and nothing else in the manifest moves.

## Reproducibility

Two cold runs — `rm -rf build && arc run` — produce a byte-identical export, sha256 `b008a789aa14db7819d7ab8c90226127427674e747b5dcfb529f627d1853082b`, the same comparison the other Protocols in this directory hold themselves to. Three things make the projection land in the same place twice: the seed is frozen in the operator's script rather than exposed in `arcform.yaml`, the thread count is pinned to one before numpy and numba load, and row order is pinned by reading each Parquet single-threaded with insertion order preserved. `ORDER BY ALL` on the export is a **total** order here — `id` is the source's UUID and all 182,822 are distinct — so this Protocol does not need the weaker tie-between-identical-rows argument [`../scienceqa/arcform.yaml`](../scienceqa/arcform.yaml) makes, and `models/load.sql` gates on that premise rather than asserting it.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

All 6 steps are `sql:` models or typed `op:` operators from the arcform catalog. This Protocol runs no opaque `command:`/shell step.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_medmcqa` | `op: http_fetch@1` |
| 2 | `embed_questions` | `op: text_embed@1` |
| 3 | `project_questions` | `op: umap_project@1` |
| 4 | `load` | `sql: models/load.sql` |
| 5 | `export_medmcqa` | `op: parquet_export@1` |
| 6 | `describe` | `op: datapackage_describe@1` |

<!-- /protocol-steps -->
