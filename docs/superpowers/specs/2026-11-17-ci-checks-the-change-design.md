# A pull request's CI checks the change, and the lake holds the descriptor

## Why

open-analytics is meant to be the fast, iterative repo, and merges here keep getting stuck. Every pull request's CI compares each committed `datapackage.json`, and each Protocol's DuckDB pin, with the object the open lake serves now. Four things follow, all seen in the publishes of November 2026:

1. A change to a descriptor or a pin cannot be green until it is published, which forces a second pull request after each publish, or publishing from an unmerged branch.
2. Publishing one dataset turns every other open branch red, since each still holds `main`'s old descriptor for it.
3. CI reads through the Cloudflare edge, so after a publish it stays red until the edge copy expires.
4. A real failure (stale README row counts) hid behind the cache errors until they cleared.

The cause is that the descriptor lives in two places, git and the lake, and CI on a pull request holds one to the other. The fix is to keep one copy, in the lake, and to check each thing where it can actually be known.

## Decisions taken in the brainstorm

- **`datapackage.json` is not committed for a Protocol that publishes.** The lake is the one stored copy: each table's descriptor is in `meta.descriptors` (one row per table, the full datapackage.org JSON) and stamped into its Parquet footer. The standard stays the interface; the descriptor is emitted on demand, by a command here and by plain SQL. Hugh: "Perhaps it doesn't need to be committed, but rather easily emitted. So, we support the standard and can render it quickly through CLI and other means but we just store it in the lake." This keeps to the rule that Meridian works in datapackage.org and creates no new formats: what is emitted is the datapackage.org descriptor, byte for byte the one stored.
- **web moves onto the lake in this work.** web's build scripts read each descriptor from the lake instead of an open-analytics checkout, so no committed copy is left anywhere to go stale.
- **In both repos, a pull request's CI checks only what the change makes**, and whether the lake serves what is declared becomes a check on `main` and on a schedule, reading the R2 origin rather than the edge. A drift it finds is reported (and in web, regenerated into a pull request), never a red on someone's branch.
- **A descriptor diff at publish is an option of arc's `publish` operator, not a step of its own.** `ducklake_write@1` already diffs the table's shape against the live table (`on_schema_change: fail`) and reads its stored key from `meta.fields`; comparing the new descriptor with the stored one is the same comparison one level deeper. Hugh found a separate step's name odd and asked whether it could be "an integrity check provided as an option within `publish`".
- **What that diff is for: a label or constraint that moved when no input moved.** A descriptor changes for four reasons: finetype's inference drifting on the same data (NDC's `depth` and `marketing_category` on DuckDB 2.0, Schedule B's `sitc` locale, GLEIF's `registration_status` confidence, FIPS's `text.word`), the data changing (EDGAR's `ticker` pattern), a person editing `descriptor.overrides.json` or `nominations.finetype.json`, or an engine or finetype version moving. The data speaks for itself and the committed inputs are reviewed in their pull request, so the diff catches the first: inference drift. Its fix stays what it was for NDC and FIPS, a nomination, which is a committed input.

## Goals

- One pull request carries a Protocol change from branch to merge; publishing happens from `main` after the merge.
- Publishing one dataset changes no other branch's CI result, in open-analytics or web.
- No pull-request job in either repo reads the live lake.
- A descriptor problem the old pull-request checks caught is still caught, before the publish where it can be known then, and after it otherwise.
- Anyone can get a dataset's `datapackage.json` in one command, without a credential.

## Non-goals

- Changing what any descriptor says, or any published table. No dataset is republished by this work.
- The demo Protocols under `examples/`. They publish nothing, have no lake copy, and keep their committed descriptors and their current checks.
- Retiring `manifest.json`, or the website reading the catalogue for anything beyond descriptors.
- Running Protocol builds or publishes in CI. Publishing stays a local `arc run --publish`, each live write after Hugh's go-ahead.
- An `arc` verb to emit a descriptor. It is filed on arcform as a follow-up; the script here does it until then.

## Anti-goals

- A check deleted rather than moved. Each current check's guarantee is either kept somewhere named below or retired with the reason it no longer applies.
- A new file format, or a Meridian-shaped projection standing in for the descriptor. Checks read the stored datapackage.org JSON (`meta.descriptors.descriptor`), not `meta.fields`.
- Moving the edge problem somewhere else: a scheduled check that reads the edge and goes red for an hour after each publish.

## Where each current check goes

The pull-request jobs in `.github/workflows/descriptors.yml` fall into three kinds.

| Check | Reads today | After |
|---|---|---|
| self-tests of every checker, model tests, `arc test` per Protocol, `arc-manifest-load`, `protocol-readme`, `one-writer-per-file`, `registry-agrees-with-itself`, `contracts-sql`, `publish-seam` | fixtures and the repo | unchanged, on every pull request |
| constraint ratchet | `descriptor.overrides.json` at `origin/main` against the branch | unchanged in kind: it compares the committed overrides, which stay committed |
| descriptor against bytes (`check_descriptors.py`: fields, types, constraints, key, bytes, self-description, foreign keys) | committed descriptor against the served Parquet | the scheduled lake check on `main`; and its rule for hand-written claims (an override's constraint the data breaks) runs in the Run beside `validate` |
| README row counts (`check_descriptors.py` catalogue) | README against the served object | the scheduled lake check |
| engine version | served footer against the Protocol's pin | the scheduled lake check |
| declared joins | served objects | the scheduled lake check |
| code-list contract | served Parquet | already a step in each code-list Protocol (`check_code_list`); the CI job moves to the scheduled check |
| frictionless, licence rule | committed descriptor | run on the built descriptor in the Run, before `publish`; and in the scheduled check on what the lake stores |
| label agreement | every committed descriptor | a step before `publish`, reading the built descriptor and every other table's from the lake; and in the scheduled check over what the lake stores |
| `endpoints-serve-what-is-declared` | served objects, full re-hash | already `main` and schedule only; joins the scheduled lake check |

The scheduled lake check runs on push to `main`, weekly, and on `workflow_dispatch`. It emits every table's descriptor from the lake, then runs the lake-reading checks above against those descriptors and the served objects. It reads the origin: a readable copy's URL with a query string, which bypasses the edge copy (as `docs/publishing.md` already does by hand), and the catalogue, whose data files are new keys on each publish. It gates no pull request. It tells two findings apart:

- **Awaiting publish**: `main`'s committed inputs are ahead of the lake (a README row count, a DuckDB pin, a `registry.json` entry, a label-agreement reason, a dataset with no table yet). Reported in the step summary; the run stays green. This is the expected gap between a merge and its publish.
- **Drift**: the lake contradicts itself (a served readable copy whose bytes or hash differ from its stored descriptor's, a footer descriptor that differs from `meta.descriptors`, a served footer written by an engine other than the Protocol's pin of the snapshot that wrote it, a declared join that fails). The run fails.

Hugh ruled this in review: report the gap, fail on drift, so `main` is not red after every merge.

## The parts

### open-analytics

- **Each publishing Protocol writes its descriptor under `build/`.** `stamp_descriptor`'s `--out-descriptor` and `publish`'s `descriptor:` move from `datapackage.json` (or `<table>.datapackage.json`) to `build/…`. The fourteen Protocols' committed descriptors are deleted; their Protocol `.gitignore`s already cover `build/`. `arcform.yaml` edits that change a `uv@1` script's arguments do not change its `sha256`, since that addresses the script's bytes.
- **`scripts/emit_descriptor.py <namespace.name>`** writes a table's `datapackage.json` from the lake: the public catalogue by default, `--catalog <path>` for a Protocol's local `build/lake/open.ducklake`, `--all --out <dir>` for every table. It needs no credential. The lake stores each descriptor without its volatile fields (arc drops `created`, and the resource's `bytes` and `hash`, before writing `meta.descriptors`, and a footer cannot state its own `bytes` and `hash`), so the emitter restores them from the table's `manifest.json` entry: `bytes` and `sha256` as `bytes` and `hash`, and `published` as `created`. When the manifest retires, the same three come from the catalogue (the readable copy's size and hash, and the snapshot that last changed the table). *Builder's call, made in review.* `stamp_descriptor.py --carry-created` then carries `created` from the emitted descriptor rather than from a committed file. `docs/publishing.md` also gives the SQL (`SELECT descriptor FROM meridian.meta.descriptors WHERE table_name = '<namespace.name>'`) and reading a readable copy's footer.
- **Checks that read `datasets/*/datapackage.json` take a descriptor source instead**: a directory of emitted descriptors (`--descriptors-dir`), which the scheduled check fills with `emit_descriptor.py --all`, and which a step fills with the built descriptor plus the other tables emitted from the lake.
- **A Protocol that needs other tables' descriptors gets them from an `emit_descriptors` step**, a `uv@1` step running `emit_descriptor.py` into `build/lake_descriptors/`. edgar_gleif's join measurement reads its siblings' descriptors (`edgar`, `gleif`, `naics`) this way instead of from `../../datasets/*/datapackage.json`, and so does the pre-publish step below. Under `arc test` the step is fixtured with descriptors under the Protocol's `testdata/`, as fetch steps are, so no pull-request job reads the lake. *My call, made in review.*
- **A `label_agreement` step before `publish`** in each Protocol whose tables carry finetype labels, as a `uv@1` step running `check_label_agreement.py --strict` on the built descriptor against the lake's others, with `datasets/label-agreement.json` as now. A failure stops the Run before anything is written. This is the check FIPS's first publish needed and did not have.
- **Frictionless and the licence rule** run in that same step on the built descriptor, so a refusal stops the publish.
- **Overrides against the data**: the constraint-evaluation part of `check_descriptors.py`, run on the built descriptor and the built Parquet, as part of the same step.
- **`.github/workflows/descriptors.yml`** splits: pull-request jobs as the table says, and a `lake` workflow (push to `main`, schedule, dispatch) for the rest. Its header comments are rewritten to say where each guarantee now lives.
- **The README catalogue** keeps its row counts, held to the lake by the scheduled check. Its last column links each row's readable copy (`https://openlake.meridian.online/<namespace>/<name>.parquet`, which carries the descriptor in its footer) instead of `datasets/<slug>/datapackage.json`, and the catalogue rule in `check_descriptors.py` finds the object from that link. *My call, made in review.*
- **The SOC models' descriptor tests** (`scripts/test_soc_2018_models.py`, `test_soc_2020_models.py`), which hold each committed descriptor to the table the fixtures build, are retired for the descriptor part: `describe` derives the descriptor from the built table, so the comparison is circular once nothing is committed, and the overrides those tests ultimately guard stay committed and are held by the constraint ratchet and by the overrides-against-data check in the Run. Their model tests stay. *My call, made in review.*
- **`docs/publishing.md`** loses the two-pull-request pattern and the "run the check by hand before the merge" advice, gains the one path (build, merge, publish from `main`, emit), and records these rulings.

### web

- `build-dataset-profiles.mjs`, `build-dataset-joins.mjs`, `build-dataset-summaries.mjs`, `build-protocol-contracts.mjs` and `dataset-addresses.mjs` read a dataset's descriptor from the lake (DuckDB attaching the public catalogue, or `emit_descriptor.py`'s output) instead of `<open-analytics>/datasets/<slug>/datapackage.json`. Example Protocols still read from the checkout, since they keep their files.
- web's pull-request CI stops comparing `src/data/` with the live lake. The parity checks run on `main` and on a schedule and report drift in the run's summary; regenerating is the existing build commands, run by hand. No bot opens a pull request, since one opened with the workflow's own token triggers no CI and could not go green, and the hand-transcribed claims in `lib/datasets.ts` cannot be regenerated anyway. *My call, made in review.*
- web's research data (`src/data/protocol-research/*.json`, `dataset-research/*.json`) cites `blob/main/datasets/<slug>/datapackage.json` as evidence; those citations move to the readable copy's URL, so the research checks find no dead link.
- Lands before open-analytics deletes its descriptors, because web's CI clones open-analytics' `main`.

### arcform

- `ducklake_write@1` gains `on_descriptor_change`: `report` (the default) prints every difference between the new descriptor and the one stored in `meta.descriptors`, ignoring `bytes`, `hash`, `created` and finetype confidences; `fail` refuses the publish, with nothing written, when a field's `x-finetype-label` moved and the new descriptor does not mark it `x-finetype-nominated`. A table with no stored descriptor reports nothing.
- open-analytics moves `ARC_REV` and sets the option once arcform's change has merged. Until then the open-analytics change is complete without it.

## Order

1. web reads descriptors from the lake (its pull request).
2. open-analytics stops committing descriptors and moves its checks (this repo's pull request). Merges after web's.
3. arcform's `on_descriptor_change` (its pull request), then a small open-analytics change to move `ARC_REV` and set the option.

## Approaches considered

- **A step of its own before `publish` running every descriptor check** (`check_descriptor`). Dropped: most of `check_descriptors.py` guards the gap between a committed copy and a separately published object, and inside a Run that gap does not exist (`describe` derives the descriptor from the Parquet it then stamps). Its remaining reasons are named and placed above.
- **A pre-publish script the publisher runs by hand.** Dropped: unenforced, and the same advice already failed once (OurAirports' coordinate constraints were found after its merge).
- **Keep `datapackage.json` committed, and move only the live comparison to `main`.** Dropped for Hugh's "store it in the lake": the committed copy would go stale between merge and publish, and every check would keep reading a second copy.
- **Put Meridian's label-agreement policy into arc's `publish`.** Dropped: its reasons file is Meridian's policy, not the engine's. The engine gets the generic diff; the policy stays a step here.

## Left to the builder

- The exact `build/` paths per Protocol, and whether the step that runs label agreement, frictionless, the licence rule and overrides-against-data is one script or several invoked by one step.
- How `check_*.py` scripts take their descriptor source, provided every existing self-test still proves each check can fail.
- How web's scripts reach the lake (Node DuckDB, or a pre-step that runs `emit_descriptor.py`), within web's existing dependencies where possible.
- The `lake` workflow's job layout.
