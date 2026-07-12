# `crosswalk.edgar_gleif` — the EDGAR ↔ GLEIF crosswalk

The resolved link between SEC EDGAR registrants (CIK / ticker / fund series) and
their GLEIF Legal Entity Identifier (LEI). This directory holds the dataset's three
orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `scripts/`, `descriptor.overrides.json`) | **Protocol** — how it's made | run `arc run` → produces the Dataset **and** its descriptor |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, foreignKeys/evidence — **emitted** by the `describe` step, no longer hand-authored |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `crosswalk.edgar_gleif`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** the source Datasets (`source.edgar`, `source.gleif`) from the openlake.
2. **fetch** the deterministic backbone (no guessing) — **official sources only
   (SEC + GLEIF)**: GLEIF SEC registrations (RA000665 → `CIK/series ↔ LEI` +
   `entity.category`) and **SEC Form N-CEN** (the annual fund filing → registrant
   `CIK ↔ LEI` and series `SERIES_ID ↔ LEI`, ~100% LEI fill). No crowd-sourced data
   enters the published dataset; Wikidata is an out-of-band validation cross-check only.
3. **load / normalise** — type the tables, normalise CIK representation, derive the
   `key_type` (cik | series | class) from the SEC identifier scheme.
4. **resolve** — probabilistic name match for the operating-company tail, via the
   `splink_resolve` operator (frozen Fellegi-Sunter model, precision-first).
5. **tier** — combine `authoritative` ∪ `confirmed` ∪ `candidate`; a deterministic
   edge always wins over a name match for the same key.
6. **package** — enrich from the sources, stamp `as_of`, write the terminal Parquet.
7. **describe** — emit `datapackage.json` from the built Parquet (see Boundaries).

## Boundaries (deliberate)

- The arcform **engine** and the **`splink_resolve` operator** live in the `arcform`
  repo; this Protocol only *references* the operator. (A typed-operator registry that
  resolves operators by name+version is greenfield; for now the `resolve` step calls
  the script by path.)
- **Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer
  flip — stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
  `build/edgar_gleif.parquet`; it is not an arcform step.
- **Describe** — `datapackage.json` is **emitted** from the built Parquet by the
  `describe` step, not hand-authored. The step splits the descriptor by authority:
  - `finetype profile build/edgar_gleif.parquet -o datapackage` types every column
    from its taxonomy — the *machine-decidable* half: per-field `type` / `format`,
    the `x-finetype-*` semantic labels + observed constraints, and the resource
    `bytes` / `hash` / `format` (computed straight from the Parquet).
  - `scripts/describe.py` overlays **`descriptor.overrides.json`** — the *curated*
    half finetype cannot infer — and writes `datapackage.json`. Overrides win;
    finetype fills the rest.
  - **finetype gap (deliberate split, not a workaround).** finetype emits exactly
    one typed Data Resource and nothing above the field level: no package identity
    (`title` / `description` / `homepage` / `licenses` / `sources`), no published
    resource `path`, no per-field prose `description`, and no relational metadata
    (`primaryKey`, `foreignKeys`, and their `x-evidence` / `x-confidence` /
    `x-status`). None of that is derivable from column values, so it lives in the
    sidecar. finetype's semantic labels are also only as good as the installed
    finetype — a wrong label is corrected by adding the field to the sidecar's
    `fields` map (an override may set any field key, including `x-finetype-label`).
  - The step **hard-fails** if a curated `primaryKey` / `foreignKey` names a column
    absent from the built Parquet — the descriptor-drift guard. Keep the sidecar in
    step with `models/package.sql`'s output columns.

## Scale & frontier (not yet wired)

- **~500k-entity scale**: expanding EDGAR beyond ticker filers means the
  `splink_resolve` blocking rules must be tuned — the GLEIF blocking hotspots are the
  empty/short-name bucket (~136k), first-token `THE` (~66k) and `STICHTING` (~10k);
  add compound keys, stopword handling, and **country as a blocking dimension** (only
  ~355k of 3.36M GLEIF entities are US). Individuals (insider Form 3/4/5 filers) stay
  **excluded** — personal data, counsel-gated.
- **Run**: needs the `arc` binary, a `uv` environment for the `splink_resolve` step,
  and `finetype` on `PATH` for the `describe` step;
  `arc run --param as_of=YYYY-MM-DD`.
