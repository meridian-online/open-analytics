# Contracts

Shared checks that code-list Protocols run inside their arc Runs, as `sql:` steps. Each file is plain DuckDB SQL with no parameters; a broken rule stops the Run with `error('<rule>: <first offending value>')`. `scripts/test_contracts.py` runs every rule against small in-memory tables.

## `check_titles.sql`: the job-title index contract

The calling Protocol's own model sets up, on the same DuckDB connection: a view `contract_titles` (the exported title index), a view `contract_code_list` (the code list Parquet from the same Run, with at least `code` and `level`), and a one-row table `contract_title_level(level VARCHAR)` naming the level the issuer codes titles to. The check fails on no rows, a `contract_title_level` that is not exactly one row with a non-NULL `level`, a missing or misordered core column (`entry_id, title, code, code_description, release, text_source, corpus` lead the table; extra columns may follow), a NULL or repeated `entry_id`, a NULL `title`, `code` or `release`, a `code` not in the code list, a `code` whose code-list `level` differs from `contract_title_level.level`, or more than one `release`. When every rule passes it writes `contract_titles_result(rows_checked)`.

## `check_crosswalk.sql`: the edition crosswalk contract

The calling Protocol's model sets up: a view `contract_crosswalk`, the view `contract_code_list` as above, and a one-row table `contract_crosswalk_shape(from_pattern VARCHAR, level VARCHAR)`. The check fails on no rows, a `contract_crosswalk_shape` that is not exactly one row with a non-NULL `from_pattern` and `level`, a missing or misordered core column (`from_code, from_description, to_code, to_description, from_edition, to_edition, release` lead the table), a repeated `(from_code, to_code)` pair, a NULL `from_code`, `to_code`, `from_edition`, `to_edition` or `release`, a `to_code` not in the code list or at a `level` other than `contract_crosswalk_shape.level`, a `from_code` that does not fully match `from_pattern` (the earlier edition is not hosted, so shape only), or more than one `release`. When every rule passes it writes `contract_crosswalk_result(rows_checked)`.
