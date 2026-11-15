# `census/fips` build plan

Spec: `docs/superpowers/specs/2026-11-16-census-fips-design.md`. Local arc: arcform main `f57ff3c`, built to `~/.arc-fips/bin/arc` (the arc on PATH is left alone). finetype: `FINETYPE_DUCKDB_EXT=/Volumes/Dev/meridian-online/finetype-v0.6.67/target/release/finetype.duckdb_extension`.

## Tasks

1. **The Protocol.** `datasets/fips/`: `arcform.yaml` (four fetches and one extract, `load`, `package`, export, describe, `check_code_list`, validate, stamp, publish), `models/load.sql`, `models/package.sql`, the overrides, nominations and drift schema, `.env.example`. Verify: `~/.arc-fips/bin/arc run` with no credential finishes green, publishes only to `build/lake/`, gives no parse warning, and a second plain run skips every step; the built table has 3,301 rows. Cost: a few seconds plus four small downloads.
2. **Tests** (depends on 1). `datasets/fips/testdata/` cut-down fixtures and `datasets/fips/tests.yaml`: one pass case with inline checks and six refusal breaks. Verify: `~/.arc-fips/bin/arc test --junit build/junit.xml` in `datasets/fips` prints `7 passed, 0 failed`. Cost: under a minute.
3. **CI** (depends on 2). Move `ARC_REV` to `f57ff3c` in the workflow, `scripts/arc_manifest_load/Cargo.toml` and its `Cargo.lock`; add a `fips-tests` job that builds `arc` at `ARC_REV` and runs `arc test` with finetype 0.6.67, `uv` and DuckDB 2.0's CLI. Add `datasets/fips` to `scripts/test_manifest_one_writer.py`'s list. Verify: `cargo build --locked` in `scripts/arc_manifest_load` and its loader over the repo; the PR's checks green. Cost: a cargo build (one at a time).
4. **Docs and README** (depends on 1). `datasets/fips/README.md` with the generated step list (`scripts/check_protocol_readme.py --write`), and `docs/code-lists.md` updated: the FIPS layout, `arc test` replacing the Python self-test, and the rulings. Verify: `python scripts/check_protocol_readme.py` passes.
5. **Fold and ready.** Fold the spec and plan into `docs/code-lists.md`, delete them, roborev branch review, PR checks, Forge link.
