# Plan: census/naics_2022 onto the code-list contract

Spec: `docs/superpowers/specs/2026-11-18-naics-2022-code-list-contract.md`. arc is the build at CI's `ARC_REV` (c57f7bf).

## Tasks

1. **The contract table and its Protocol.** `datasets/naics/models/load.sql` builds the ladder (parents, depth, ancestors, `description_full`, `release`, `text_source`, `definition`, the kept `title`) and fails the run on a missing or ambiguous parent; `package.sql` adds `corpus` in its place. `arcform.yaml` moves to the one-writer layout, adds `check_code_list` (pinned by sha256, `--levels sector,subsector,industry_group,naics_industry,national_industry`) and sets `on_schema_change: replace`. Overrides gain `x-meridian-code-list`, the new fields' descriptions and sources; nominations and `schema.finetype.json` cover the new columns; README's step list regenerates.
   Verify: `arc run --force` in `datasets/naics` with no credential succeeds (fetches Census, reads the lake; a few minutes). Expected: `check_code_list` and `check_before_publish` pass; 2,125 rows.
2. **arc tests.** `datasets/naics/tests.yaml` with fixtures under `datasets/naics/testdata/` (the index's descriptions fixture, the lake bundle, a described stand-in): a pass case checking parents, depth, ancestors, the range sectors and text, and a refusal for a missing parent. Add `naics` to CI's `protocol-tests` matrix and to `ONE_WRITER`.
   Verify: `arc test` in `datasets/naics`, `datasets/naics_2022_titles` and `datasets/naics_2017_to_2022` (seconds each). Expected: all pass. Depends on 1.
3. **Agreement, shape and docs.** Label-agreement reasons the strict check asks for; `check_lake_shape.py` against the live table; Schedule B's join measured against the built table; fold the spec into `docs/code-lists.md`, then delete the spec and plan.
   Verify: the repository's Python tests (`uv run --with pytest ... pytest scripts`, a minute or two) and `check_protocol_readme.py`. Expected: pass. Depends on 1 and 2.
