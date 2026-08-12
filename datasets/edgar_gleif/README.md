# `crosswalk.edgar_gleif` — the EDGAR ↔ GLEIF crosswalk

The resolved link between SEC EDGAR registrants (CIK / ticker / fund series) and
their GLEIF Legal Entity Identifier (LEI). This directory holds the dataset's three
orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `descriptor.overrides.json`, `scripts/test_ingest_lei.py`) | **Protocol** — how it's made | run `arc run` → produces the Dataset **and** its descriptor; the test runs the models against scratch sources and pins what they refuse |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, `x-joins`/evidence — **emitted** by the `describe` step, no longer hand-authored |
| `scripts/crosswalk_join.py` (+ `test_crosswalk_join.py`) | **Proof** — that the join runs | executes every declared `x-joins` against the published objects and checks the coverage each one claims |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `crosswalk.edgar_gleif`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The Protocol is **all-config**: every step is a `sql:` model or a
typed `op:` operator from the arcform catalog (`http_fetch`, `gleif_ra_fetch`,
`archive_extract`, `parquet_export`, `splink_resolve`, `datapackage_describe`) — there
are **no opaque `command:`/shell steps**. The step DAG:

1. **fetch** the source Datasets (`source.edgar`, `source.gleif`) from the openlake.
2. **fetch** the deterministic backbone (no guessing) — **official sources only
   (SEC + GLEIF)**: GLEIF SEC registrations (RA000665 → `CIK/series ↔ LEI` +
   `entity.category`) and **SEC Form N-CEN** (the annual fund filing → registrant
   `CIK ↔ LEI` and series `SERIES_ID ↔ LEI`). No crowd-sourced data
   enters the published dataset; Wikidata is an out-of-band validation cross-check only.
   The N-CEN LEI column is filer-typed free text, so **how full it is says nothing
   about how much of it is usable**: a filer with no LEI to report may leave the field
   empty, and may instead write a row of zeroes, which counts as filled. See *The
   identifier* below.
3. **load / normalise** — type the tables, normalise CIK representation, derive the
   `key_type` (cik | series | class) from the SEC identifier scheme, and drop an N-CEN
   LEI when the check-digit arithmetic rejects it **and** GLEIF does not publish it.
   Both arms, not the arithmetic alone — see *The identifier*.
4. **resolve** — probabilistic name match for the operating-company tail, via the
   `splink_resolve` operator (frozen Fellegi-Sunter model, precision-first).
5. **tier** — combine `authoritative` ∪ `confirmed` ∪ `candidate`; a deterministic
   edge always wins over a name match for the same key.
6. **package** — enrich from the sources, stamp `as_of`, materialise the terminal edge
   table, and **gate it on the identifier** (see *The identifier*); the
   `parquet_export` operator writes it to `build/edgar_gleif.parquet` (a
   first-class produced asset, not an unparseable `COPY` graph-island). A **total**
   `order_by` (`company_name` is not unique — 6,906 ties) makes the bytes reproducible.
7. **describe** — emit `datapackage.json` from the built Parquet (see Boundaries).

## The identifier

An edge asserts that an SEC filer **is** a given legal entity. An LEI that names no
entity cannot carry that assertion, and no `tier` value repairs one that does not — so
the Protocol refuses it rather than publishing it under a softer label.

An LEI is admitted when **either** test passes:

- **ISO 7064 MOD 97-10.** The last two of the twenty characters are check digits over
  the other eighteen. `models/load.sql` declares `lei_mod97`, which expands each
  character to its base-36 value and folds left, so no fixed-width integer overflows;
  the identifier is well formed when the remainder is 1.
- **GLEIF publishes it.** The register holds entries whose check digits do not satisfy
  the standard, and an entity GLEIF publishes is a real one whatever the arithmetic
  says of its digits. This arm is a forward guard rather than a path anything travels
  today: measured against the published snapshot it changes no row. It is here so that
  the first filing to report such a register entry is not dropped for carrying the
  digits the register gave it.

The shape cannot do this job. `00000000000000000000` satisfies ISO 17442's
eighteen-alphanumeric-plus-two-digit pattern, which is what `datapackage.json` and
`schema.finetype.json` declare — a `pattern` does not evaluate a checksum. That is why
the test is arithmetic, and why it runs in the Protocol, where a rejected value can be
kept out of the bytes rather than reported after they are published.

Where each half applies:

| Source | Treatment | Why |
|---|---|---|
| N-CEN (`sec-ncen`) | **filtered** in `models/load.sql` | filer-typed free text; placeholders and transcriptions are expected of it |
| RA000665 (`sec-registration`), resolver (`exact_name`, `jaro_winkler`) | **gated** in `models/package.sql` — the Run fails | the LEI is the register's own key; a value failing both tests means something upstream is broken, and a broken Run must not publish |

`scripts/test_ingest_lei.py` proves both can fail, offline: it runs the shipped models
against scratch sources, and one case deletes the filter from `models/load.sql` and
asserts the gate behind it reddens.

## What is not in it

**A filer key whose reported LEI could not be resolved is excluded from this crosswalk.**
An edge asserts that two identifiers name the same thing. An identifier that names
nothing cannot carry that assertion at any confidence, so labelling it with a weaker
`tier` does not make it publishable — the assertion is the problem, not its strength.

**Read an absent filer as *"no identity edge we are willing to publish"*, not as *"no
such filer"*.** The company exists and its filing exists; what is missing is a
counterparty identifier we could stand behind. Causes include a placeholder — a row of
zeroes — written where a null belongs, a single-character transcription of a real LEI,
and a value matching no issued identifier at all.

Two things bound what this removes. The rule fires when a value fails **both** admission
tests above, so it cannot reach a filer whose LEI resolves. And a key disappears where
the unresolvable value was the sole LEI reported for it: where the same key also carries
a registration-sourced or resolver-sourced edge, that edge stays and the key is still
here.

**This file quotes no count.** The figure moves with each Run, and a number written here
would be stale the first time the dataset is rebuilt with nothing to say so. To recover
the excluded set for a Run, join the LEI columns of the N-CEN quarters `arcform.yaml`
pins against `key` in this dataset: the filers with no row are those for which no usable
LEI was reported. The rule takes effect at the Run that applies it rather than
retroactively, so a snapshot published earlier can still carry rows it now removes.

## Boundaries (deliberate)

- The arcform **engine** and the **operator catalog** live in the `arcform` repo; this
  Protocol only *references* operators by `name@version` (e.g. `splink_resolve@1`). The
  operators embed their frozen script bytes (`resolve.py`, `describe.py`, the dlt
  paginator), so `@1` addresses an exact, reproducible implementation — the path-call
  the `resolve`/`describe`/fetch steps used before is retired.
- **Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer
  flip — stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
  `build/edgar_gleif.parquet`; it is not an arcform step.
- **Describe** — `datapackage.json` is **emitted** from the built Parquet by the
  `describe` step, not hand-authored. The step splits the descriptor by authority:
  - `finetype profile build/edgar_gleif.parquet -o datapackage` types every column
    from its taxonomy — the *machine-decidable* half: per-field `type` / `format`,
    the `x-finetype-*` semantic labels + observed constraints, and the resource
    `bytes` / `hash` / `format` (computed straight from the Parquet).
  - the `datapackage_describe` operator (which embeds the former `describe.py`) overlays
    **`descriptor.overrides.json`** — the *curated* half finetype cannot infer — and
    writes `datapackage.json`. Overrides win; finetype fills the rest.
  - **finetype gap (deliberate split, not a workaround).** finetype emits exactly
    one typed Data Resource and nothing above the field level: no package identity
    (`title` / `description` / `homepage` / `licenses` / `sources`), no published
    resource `path`, no per-field prose `description`, and no relational metadata
    (`primaryKey`, `x-joins`, and their evidence and coverage). None of that is
    derivable from column values, so it lives in the sidecar. finetype's semantic
    labels are also only as good as the installed finetype — a wrong label, type or
    constraint is corrected by adding the field to the sidecar's `fields` map (an
    override may set any field key, including `type`, `constraints` and
    `x-finetype-label`, and wins over what finetype emitted).
  - The step **hard-fails** if a curated `primaryKey` / `foreignKey` names a column
    absent from the built Parquet — the descriptor-drift guard. Keep the sidecar in
    step with `models/package.sql`'s output columns.
  - **That guard reads `primaryKey` and `foreignKeys`, and nothing else.** `x-joins`
    is a non-structural key, so the overlay copies it through unread: a column name
    that has gone stale there does not stop the describe step, and surfaces later as
    an exit 2 from `scripts/crosswalk_join.py` when the join is next run.

## The joins

This package declares no `schema.foreignKeys`. A Data Package foreign key asserts
that **every** value on the left exists on the right, and neither relationship this
crosswalk carries satisfies that against the published bytes: `edgar` is a
ticker/exchange listing rather than the CIK universe, so most SEC-side keys have no
row there and it holds CIKs the crosswalk does not; and a small tail of rows here
carry an LEI that is not in the GLEIF object this descriptor names. Declaring either
as a foreign key would advertise a guarantee the data does not keep.

What is true is stated instead, at the package root under **`x-joins`**: the
columns, the referenced package, the row subset each join applies to, whether the
comparison needs a rendering, and the measured `coverage`. `edgar_gleif.key` is text
and `edgar.cik` is an integer — the two SEC identifier schemes share one column, so
no arithmetic type describes it — and the declaration says so by naming the
rendering (`compareAs: text`) rather than leaving a consumer to guess.

`coverage` is measured against the exact objects in `resources[].path` and moves
when they are republished, exactly as `bytes` and `hash` do.
`scripts/crosswalk_join.py` runs the declarations and fails when the data disagrees;
it takes every column, path, filter and rendering out of the descriptors, because a
join that can only be run by a program which already knows the answer was never
described. `scripts/test_crosswalk_join.py` proves that runner can fail, offline,
against scratch packages.

## Scale

- **Full SEC-entity universe (wired).** `fetch_cik_lookup` (http_fetch) +
  `build_sec_entities` (`models/sec_entities.sql`) + `export_sec_entities`
  (parquet_export) build the ~1.05M-filer resolution left side (cik-lookup + former
  names), and `splink_resolve`'s blocking is
  tuned for it (compound keys, stopword handling, **country as a blocking dimension** —
  only ~355k of 3.36M GLEIF entities are US). Individuals (insider Form 3/4/5 filers)
  are left in but carry no LEI, so they never match — the **output is entity-only by
  construction** (no PII published).
- **Run**: needs the `arc` binary, `uv` on `PATH` (the `splink_resolve`, `gleif_ra_fetch`,
  and `datapackage_describe` operators run their frozen scripts via `uv run`), and
  `finetype` on `PATH` (the describe operator shells out to it); `arc run --param
  as_of=YYYY-MM-DD`. At full scale the resolve step is real compute (~100M candidate
  pairs) — run it deliberately.
