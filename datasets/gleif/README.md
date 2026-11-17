# `gleif.lei` — GLEIF Legal Entity Identifiers

The Global Legal Entity Identifier Foundation's public register of legal entities —
every entity with its 20-character LEI, legal name, country, jurisdiction and
registration status. This directory holds the dataset's three orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `scripts/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `gleif.lei` (alias `source.gleif`), manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** (`scripts/fetch_gleif.sh`) — resolve the latest GLEIF golden-copy full
   CSV from the publishes API, download (~470 MB zip) and unzip it. The golden copy is
   republished daily.
2. **load / normalise** (`models/load.sql`) — read the ~200-column golden copy as
   text and project the published 10-field schema (`lei`, `legal_name`, `country`,
   `city`, `jurisdiction`, `legal_form`, `entity_status`, `registration_status`,
   `initial_registration`, `next_renewal`); slice the two ISO timestamps to plain
   dates. The register's own values pass through verbatim.
3. **package** (`models/package.sql`) — add the search `corpus`, sort by
   `country, legal_name` for row-group pruning, write the terminal zstd Parquet.
4. **search** (`models/search.sql`) and **export_search** — build the search index the website's typeahead reads: one row per word token of each entity's legal name and city, with the entity's identity columns, written sorted by token in 20,000-row groups so a prefix scan reads about one group.
5. **publish** — upsert `gleif.lei` into the open lake on `lei`, with its descriptor in the lake's `meta` schema, the readable copy at `gleif/lei.parquet` and the search index at `gleif/lei/search.parquet` (see *Publish*).

Produces `build/gleif.parquet` and its search index, `build/gleif_search.parquet`.

## Publish

The last step publishes `gleif.lei` into the open lake (<https://openlake.meridian.online/>), a DuckLake catalog on R2 that readers attach over https without a credential, and writes its readable copy, one Parquet file at <https://openlake.meridian.online/gleif/lei.parquet>. Each publish upserts the table on `lei`: entities the golden copy adds are inserted, changed rows updated, and entities it no longer carries deleted, all in one snapshot a reader can time-travel back through. The same step uploads the search index to <https://openlake.meridian.online/gleif/lei/search.parquet>, after the catalogue holds the snapshot, and names it as `searchUrls` in the table's `manifest.json` entry, which is where the website finds it. The index is a file beside the lake, not a table in it.

With all three of `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` set, from a `.env` here (`cp .env.example .env`, fill it in; `.env` and its variants are gitignored) or from the environment, every `arc run` publishes into the live lake. `arc run --publish` only makes a missing credential fail the run instead of falling back. With any of the three unset, a plain `arc run` still succeeds: the step says it published locally, names the missing variables, and writes a lake into `build/lake/open.ducklake` that attaches and reads back offline. For local work, keep `.env` absent and the variables unset. Better still, keep the credential in a file outside the Protocol folder and export it only for the publishing command, in a subshell: `(set -a; . /path/outside/repo/openlake.env; set +a; arc run --publish)`. Readers need DuckDB 2.0 or later. An unchanged rebuild commits nothing.

`gleif.lei` is already in the lake, so before a live publish check that the rebuilt table still has the live table's shape: `python scripts/check_lake_shape.py --catalog https://openlake.meridian.online/catalog/open.ducklake --table gleif.lei --parquet datasets/gleif/build/gleif.parquet` from the repository root must print `shape ok`. [`docs/publishing.md`](../../docs/publishing.md) says what else a publisher needs first.

## Run

Needs the `arc` binary, `duckdb`, `uv` (for the stamp) and `finetype` (for `describe` and `stamp_version`), plus `curl`, `jq` and `unzip` for the fetch: `arc run`.

## The step list

<!-- protocol-steps: generated from arcform.yaml by scripts/check_protocol_readme.py — do not edit this block by hand -->

10 steps, of which 2 run through an opaque `command:`/shell step: `fetch_gleif`, `stamp_version`. A shell step is invisible to the engine's staleness model and carries its interpreter and dependency pins inline, so what this Protocol guarantees stops short of it. Each shell step's row carries a fingerprint of its command line, so an edit to the line changes this block without the line being printed here.

| # | Step | How it runs |
|---|---|---|
| 1 | `fetch_gleif` | `command:` (shell, sha256 `68c676a0388d`) |
| 2 | `load` | `sql: models/load.sql` |
| 3 | `package` | `sql: models/package.sql` |
| 4 | `export_gleif` | `op: parquet_export@1` |
| 5 | `describe` | `op: datapackage_describe@1` |
| 6 | `stamp_version` | `command:` (shell, sha256 `68794fb4cbe9`) |
| 7 | `stamp_descriptor` | `op: uv@1` |
| 8 | `search` | `sql: models/search.sql` |
| 9 | `export_search` | `op: parquet_export@1` |
| 10 | `publish` | `op: ducklake_write@1` |

<!-- /protocol-steps -->
