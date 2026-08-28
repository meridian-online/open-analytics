# Demo Protocols — third-party data, referenced by URL, published nowhere

**Nothing in this directory is a Meridian dataset, and nothing here is a candidate to become one.**

Each subdirectory is an [arcform](https://github.com/meridian-online/arcform) Protocol that fetches a third-party file from its canonical URL, transforms it in DuckDB, exports a Parquet to the machine that ran it, and emits a Frictionless [Data Package](https://datapackage.org/) descriptor for what it built. They exist so that the analysis surface has real data to be demonstrated against, with a reproducible recipe behind it instead of a binary somebody made by hand.

**The distinction from `../datasets/` is the whole point of keeping them apart.** A Protocol under `datasets/` terminates in an object Meridian serves at `openlake.meridian.online`, and everything about the licensing of that act applies to it. A Protocol under `examples/` terminates on your disk. Meridian hosts none of this data, redistributes none of it, and adds no second URL for anything that already has one. Running one of these is the same act as running a shell script that curls a file — the licence question attaches to whoever runs it and what they do with the bytes, not to the recipe.

## The six

| Protocol | Source | Licence at source | Rows | Map |
|---|---|---|---|---|
| `movies` | vega-datasets 3.2.1 | BSD-3-Clause | 3,201 | — |
| `census-income` | `scikit-learn/adult-census-income` @ `fbeef6ec` | CC0-1.0 | 32,561 | — |
| `california-housing` | `gvlassis/california_housing` @ `17110e60` | MIT | 16,640 | — |
| `scienceqa` | `derek-thomas/ScienceQA` @ `f18b0a70` (test split) | CC BY-NC-SA 4.0 — **NonCommercial** | 4,241 | — |
| `medmcqa` | `openlifescienceai/medmcqa` @ `91c6572c` (validation split) | **not asserted** — three surfaces disagree | 4,183 | `question` |
| `supergpqa` | `m-a-p/SuperGPQA` @ `4430d445` | ODC-BY — **fifteen upstreams to credit** | 26,529 | `question` |

**The last two compute the coordinates their map is drawn from; the first four have no text column and no map.** `medmcqa` and `supergpqa` embed the column named in the *Map* row and reduce the vectors to `projection_x` and `projection_y` with UMAP, so a dashboard has a scatter of what the questions are about and a recipe behind it. Apple's Embedding Atlas gallery joins the same two columns in for these two datasets from a Parquet it built out of band; here they are steps in the pipeline. **Read those coordinates as regions and not as distances**, and note there is no `neighbors` column and none can be recovered from two coordinates — each of those two Protocols has its own `README.md` saying so at length, which the older four do not.

**Those two also need one thing the older four do not: an embedding extension on disk.** `text_embed@1` names a loadable DuckDB artifact the Protocol puts there and never downloads, and there is no published URL for it today, so it is the one input in this directory that does not arrive from a URL. Each of the two READMEs says how to build and place it; absent, the run stops in milliseconds naming the file it looked for.

**Each licence was read at its source, and for `movies` that turned out to mean no licence at all.** vega-datasets declares a per-resource licence for **58 of its 73 resources**, and `movies` is one of the 15 it does not — so the silence is a choice rather than an omission, and nothing is asserted here either. The BSD-3-Clause on that repository covers its code and infrastructure, and its own `datapackage.json` says so. An earlier version of this file labelled `movies` BSD-3-Clause and cited a `LICENSE` URL that returns 404; that was the repository's code licence applied to data it only redistributes, which is exactly the mistake this paragraph now exists to prevent.

**`scienceqa`'s own mirror disagrees with itself, and that is the other way this goes wrong.** The Hugging Face mirror's YAML frontmatter tags the dataset `cc-by-sa-4.0` — no NonCommercial — and a third-party gallery that read only that tag has the dataset catalogued as plain CC BY-SA. That mirror's own card *body*, three headings down under "Licensing Information," says CC BY-NC-SA 4.0, and the licensor's project site (`scienceqa.github.io`) and source repository (`github.com/lupantech/ScienceQA`) both agree with the body, not the tag. So `scienceqa/arcform.yaml` and its descriptor both carry NonCommercial, and both say plainly what it restricts: referencing this URL and building the Parquet is not redistribution and is not restricted; Meridian using the *output* commercially — on the site, in launch material, on a pricing page — is.

**`medmcqa` is the third instance of the same class, with a third surface in it, and it is why no licence is asserted for that one at all.** Its Hugging Face repository tags the dataset `apache-2.0` in YAML frontmatter; the same card's own *Licensing Information* section, read at revision `91c6572c`, says `[Needs More Information]` — the licensor declining to state terms; and the authors' own repository, `github.com/medmcqa/medmcqa`, carries an MIT `LICENSE.md` over a tree of `train.py`, `model.py`, `dataset.py`, notebooks and a `requirements.txt` with no data in it at all. Both mistakes this file already records apply at once: a tag is not the licensor, and a code repository's licence is not a grant over data it links to. So all three readings go into that Protocol's descriptor and it relies on none of them — referencing the URL needs no grant, and the output should be treated as unlicensed third-party content until someone has terms in writing.

**`supergpqa`'s licence asks for something none of the others do, and it is checked rather than written.** ODC-BY asserts no copyright over the underlying content; what it requires is credit to the original creators of the third-party data included, and the source card names **fifteen** datasets. All fifteen are in that Protocol's descriptor by name and by link, and `supergpqa/models/corpus.sql` stops the run when one goes missing from that file, naming which. Attribution is the only obligation in this directory that a reader cannot verify by looking at the data, so it is the only one with a gate behind it.

Every `sha256:` in these manifests was computed from the fetched bytes here, and the five Hugging Face URLs address a 40-character commit revision rather than a default branch, so the revision is pinned as well as the content.

`census-income` describes individuals recorded by the 1994 US Census. It is used here as a *shape of data*, and its columns use that instrument's categories rather than ones anybody would choose today.

## Running one

```sh
cd examples/movies && arc run
```

You need `arc` on `PATH` and a DuckDB the manifest's `engine_version` accepts. Each run writes into that Protocol's `build/`, which is git-ignored.

## Two things worth knowing before you rely on a run

**The exports are byte-reproducible, and `ORDER BY ALL` is why.** Two cold runs of all six Protocols produced identical SHA-256 digests — `5ab2c92b…`, `3021b408…`, `43b5fbc7…`, `d7677afc…`, and `da378d78…` and `74cd964f…` for the two that compute a projection. **A UMAP fit reproducing is a stronger claim than a sort reproducing**, and three things buy it rather than the ordering alone: the seed is frozen in `umap_project`'s script rather than exposed in a manifest, the thread count is pinned to one before numpy and numba are imported, and each Parquet is read single-threaded with insertion order preserved. On those two the ordering IS a total order — `medmcqa.id` and `supergpqa.uuid` are distinct on every row, and each Protocol's `models/load.sql` stops the run if that stops being true. On the older four it is not, and: 24 of `census-income`'s 32,561 rows, and 64 of `scienceqa`'s 4,241, are exact duplicates of another row, so ties exist. A tie between two rows that are equal in every column cannot change the output bytes, because the rows themselves are identical — which is the weaker condition that actually holds, and it holds without inventing a surrogate key. Broken and reproduced on `scienceqa`: swapping `order_by: "ALL"` for `order_by: "random()"` turned two consecutive runs' digests different (`72962916…` vs. `b4c7d1e3…`) where the real manifest gives the same digest every time.

**A `sha256:` pin is provenance here, not an integrity gate.** On a genuine transfer it fails closed — corrupt a pin against an empty cache and the run stops, naming both hashes. But arcform's shared fetch cache is keyed by **URL**, and on a cache hit the pin is not consulted: with the cache warm, a manifest declaring `sha256: 0000…0` builds to exit 0 and reuses the cached bytes. Reproduced on `california-housing`. So the pin protects the first fetch on a cold machine and nothing after it, and changing a pin to name different bytes will not fetch them on a machine that already has the old ones.

**Rebuild by removing `build/`, not by deleting the exported Parquet.** Both work on a current `arc`: deleting the Parquet alone re-runs the export and describe steps and puts it back. On an `arc` built before 2026-08-19 it did not — the run reported every step fresh and left the file absent — so if a rebuild appears to do nothing, check `arc` is current before concluding anything about the Protocol.

## What finetype makes of them

The `describe` step types every column semantically, and the four tabular Protocols are a fair sample of what that buys and what it still gets wrong. `california-housing`'s coordinates come back as `geography.coordinate.latitude` and `.longitude` rather than as two doubles, and `movies.release_date` as `datetime.date.iso` — which is the difference between a dashboard that can choose a map and a date axis, and one that draws nine histograms. `movies.director` types as `identity.person.full_name`.

It is not uniformly right, and the miss is recorded here rather than quietly accepted: `census-income.final_weight` types as `identity.person.weight`. It is a Census *sampling* weight — how many people in the population the record stands for — and not a property of anybody's body. `scienceqa.grade` — a string like `grade5` — types as `finance.rate.basis_points`, which is a stranger miss than the census one and just as wrong: nothing about a school grade level resembles a rate.
