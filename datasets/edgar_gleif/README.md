# `crosswalk.edgar_gleif` — the EDGAR ↔ GLEIF crosswalk

The resolved link between SEC EDGAR registrants (CIK / ticker / fund series) and
their GLEIF Legal Entity Identifier (LEI). This directory holds the dataset's three
orthogonal facets:

| File | Facet | Answers |
|---|---|---|
| `arcform.yaml` (+ `models/`, `scripts/`) | **Protocol** — how it's made | run `arc run` → produces the Dataset |
| `datapackage.json` | **Descriptor** — what it is | schema, finetype labels, foreignKeys/evidence |
| `../registry.json` (this dataset's entry) | **Address** — how it's found | stable `uid`, `crosswalk.edgar_gleif`, manifest pointer |

## The Protocol

`arc run` (arcform) executes `arcform.yaml`. One **Run** → one Dataset version — the
freshness lever. The step DAG:

1. **fetch** the source Datasets (`source.edgar`, `source.gleif`) from the openlake.
2. **fetch** the deterministic backbone (no guessing): GLEIF SEC registrations
   (RA000665 → authoritative `CIK/series ↔ LEI` + `entity.category`) and Wikidata
   (`CIK↔LEI`, CC0). *N-CEN registrant layer is the next addition — see below.*
3. **load / normalise** — type the tables, normalise CIK representation, derive the
   `key_type` (cik | series | class) from the SEC identifier scheme.
4. **resolve** — probabilistic name match for the operating-company tail, via the
   `splink_resolve` operator (frozen Fellegi-Sunter model, precision-first).
5. **tier** — combine `authoritative` ∪ `confirmed` ∪ `candidate`; a deterministic
   edge always wins over a name match for the same key.
6. **package** — enrich from the sources, stamp `as_of`, write the terminal Parquet.

## Boundaries (deliberate)

- The arcform **engine** and the **`splink_resolve` operator** live in the `arcform`
  repo; this Protocol only *references* the operator. (A typed-operator registry that
  resolves operators by name+version is greenfield; for now the `resolve` step calls
  the script by path.)
- **Publish** — the content-addressed R2 upload + `manifest.json` / catalog pointer
  flip — stays in the out-of-repo publish pipeline. It *reads* this Protocol's terminal output
  `build/edgar_gleif.parquet`; it is not an arcform step.
- **Describe** — emitting `datapackage.json` from a finetype-profile step is the
  planned follow-up that retires today's hand-checked descriptor.

## Scale & frontier (not yet wired)

- **N-CEN**: SEC Form N-CEN datasets (`sec.gov/dera/data/form-n-cen-data-sets`) pair
  the fund **registrant CIK ↔ LEI** (100% populated) and map **series → registrant
  CIK**. Add a `fetch_ncen` step + UNION into `models/tier.sql` §1c.
- **~500k-entity scale**: expanding EDGAR beyond ticker filers means the
  `splink_resolve` blocking rules must be tuned — the GLEIF blocking hotspots are the
  empty/short-name bucket (~136k), first-token `THE` (~66k) and `STICHTING` (~10k);
  add compound keys, stopword handling, and **country as a blocking dimension** (only
  ~355k of 3.36M GLEIF entities are US). Individuals (insider Form 3/4/5 filers) stay
  **excluded** — personal data, counsel-gated.
- **Run**: needs the `arc` binary + a `uv` environment for the `splink_resolve` step;
  `arc run --param as_of=YYYY-MM-DD`.
