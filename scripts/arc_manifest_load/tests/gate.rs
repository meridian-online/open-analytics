//! The gate's self-test: run the built `arc-manifest-load` against scratch trees and
//! assert its exit status AND its message.
//!
//! Every refusal here is held against an acceptance it differs from by one manifest:
//! `accepts_the_census` is the tree each refusal case starts from. So a gate that loads
//! nothing fails the refusal cases, and a gate that refuses everything fails the
//! acceptance. The messages a refusal is held to are computed by asking `arc`'s loader
//! for the same bytes, so the test pins "the gate prints `arc`'s message" rather than a
//! copy of today's wording.
//!
//! The scratch trees hold no `build/` and no SQL file, so every case also shows the
//! gate needs neither.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use arc::spec::Manifest;

/// A manifest `arc` accepts: one SQL step. The SQL file it names does not exist.
fn plain(name: &str) -> String {
    format!(
        "name: {name}\nengine: duckdb\ndb: build/{name}.db\nsteps:\n  - name: model\n    sql: models/model.sql\n"
    )
}

/// A manifest `arc` accepts only when the operator catalog carries `http_fetch`, which
/// it does in the `arc` binary. A loader built without that feature refuses it.
const FETCHES: &str = "\
name: fetches
steps:
  - name: fetch
    op: http_fetch@1
    with:
      url: https://example.invalid/data.json
      out: build/data.json
  - name: model
    sql: models/model.sql
    depends_on: [fetch]
";

/// The census the floors were cut against: four under `datasets/`, eleven under
/// `examples/`. `examples/e1` fetches, so the operator catalog is exercised.
fn census(root: &Path) {
    for i in 1..=4 {
        write(root, &format!("datasets/d{i}"), &plain(&format!("d{i}")));
    }
    write(root, "examples/e1", FETCHES);
    for i in 2..=11 {
        write(root, &format!("examples/e{i}"), &plain(&format!("e{i}")));
    }
}

fn write(root: &Path, dir: &str, yaml: &str) {
    let dir = root.join(dir);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("arcform.yaml"), yaml).unwrap();
}

fn scratch(name: &str) -> PathBuf {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    root
}

struct Run {
    code: i32,
    stdout: String,
    stderr: String,
}

fn gate(root: &Path) -> Run {
    let out = Command::new(env!("CARGO_BIN_EXE_arc-manifest-load"))
        .arg(root)
        .output()
        .expect("the gate binary runs");
    Run {
        code: out.status.code().expect("the gate exits rather than being killed"),
        stdout: String::from_utf8(out.stdout).unwrap(),
        stderr: String::from_utf8(out.stderr).unwrap(),
    }
}

/// What `arc`'s own loader says about `yaml`. Panics if it accepts, so a case whose
/// input stopped being a refusal says so instead of asserting against nothing.
fn arcs_refusal(yaml: &str) -> String {
    Manifest::from_yaml_str(yaml)
        .expect_err("arc's loader refuses this input")
        .to_string()
}

fn assert_no_build_dir(root: &Path) {
    for entry in fs::read_dir(root.join("datasets")).unwrap() {
        assert!(!entry.unwrap().path().join("build").exists(), "the gate ran a step");
    }
}

#[test]
fn accepts_the_census() {
    let root = scratch("accepts_the_census");
    census(&root);
    let run = gate(&root);
    assert_eq!(run.code, 0, "stdout:\n{}\nstderr:\n{}", run.stdout, run.stderr);
    assert!(
        run.stdout.contains(
            "15 of 15 manifest(s) loaded through arc's loader, 0 refused; \
             datasets/ 4 (floor 4), examples/ 11 (floor 11); total floor 15"
        ),
        "{}",
        run.stdout
    );
    assert!(run.stdout.contains("loaded  examples/e1/arcform.yaml (2 step(s))"), "{}", run.stdout);
    assert!(run.stderr.is_empty(), "{}", run.stderr);
    assert_no_build_dir(&root);
}

#[test]
fn refuses_an_unknown_precondition_with_arcs_own_message() {
    let root = scratch("refuses_an_unknown_precondition");
    census(&root);
    let bad = format!("{}    preconditions:\n      - not_a_real_precondition: true\n", plain("d2"));
    write(&root, "datasets/d2", &bad);

    let expected = arcs_refusal(&bad);
    assert!(expected.contains("untagged enum Precondition"), "{expected}");

    let run = gate(&root);
    assert_eq!(run.code, 1, "stdout:\n{}\nstderr:\n{}", run.stdout, run.stderr);
    assert!(
        run.stderr.contains(&format!("REFUSED datasets/d2/arcform.yaml: {expected}")),
        "{}",
        run.stderr
    );
    assert!(run.stdout.contains("14 of 15 manifest(s) loaded through arc's loader, 1 refused"), "{}", run.stdout);
    assert_no_build_dir(&root);
}

#[test]
fn refuses_an_unsatisfiable_operator_under_examples_with_arcs_own_message() {
    let root = scratch("refuses_an_unsatisfiable_operator");
    census(&root);
    let bad = "\
name: e3
steps:
  - name: export
    op: parquet_export@9
    with:
      input: t
      dest: build/t.parquet
";
    write(&root, "examples/e3", bad);

    let expected = arcs_refusal(bad);
    assert!(expected.contains("'parquet_export@9' not satisfied"), "{expected}");

    let run = gate(&root);
    assert_eq!(run.code, 1, "stdout:\n{}\nstderr:\n{}", run.stdout, run.stderr);
    assert!(
        run.stderr.contains(&format!("REFUSED examples/e3/arcform.yaml: {expected}")),
        "{}",
        run.stderr
    );
}

#[test]
fn refuses_a_root_that_is_not_there() {
    let root = scratch("refuses_a_root_that_is_not_there").join("absent");
    let run = gate(&root);
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(run.stderr.contains("walk refused") && run.stderr.contains("is not a directory"), "{}", run.stderr);
}

#[test]
fn refuses_a_directory_holding_no_manifest() {
    let root = scratch("refuses_a_directory_holding_no_manifest");
    let run = gate(&root);
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(run.stderr.contains("has no datasets/ subtree"), "{}", run.stderr);
}

#[test]
fn refuses_examples_alone() {
    let root = scratch("refuses_examples_alone");
    census(&root);
    fs::remove_dir_all(root.join("datasets")).unwrap();
    let run = gate(&root);
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(run.stderr.contains("has no datasets/ subtree"), "{}", run.stderr);
}

#[test]
fn refuses_datasets_alone() {
    let root = scratch("refuses_datasets_alone");
    census(&root);
    fs::remove_dir_all(root.join("examples")).unwrap();
    for i in 5..=15 {
        write(&root, &format!("datasets/d{i}"), &plain(&format!("d{i}")));
    }
    let run = gate(&root);
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(run.stderr.contains("has no examples/ subtree"), "{}", run.stderr);
}

#[test]
fn refuses_a_subtree_under_its_floor_even_when_the_total_is_met() {
    let root = scratch("refuses_a_subtree_under_its_floor");
    census(&root);
    fs::remove_dir_all(root.join("datasets/d4")).unwrap();
    write(&root, "examples/e12", &plain("e12"));
    let run = gate(&root);
    assert_eq!(run.code, 2, "{}", run.stderr);
    assert!(
        run.stderr.contains("datasets/ holds 3 arcform.yaml file(s), floor is 4"),
        "{}",
        run.stderr
    );
}

// What the gate does NOT cover, pinned. The workflow header states these limits; each
// case reddens on the day `arc`'s loader starts refusing the input, which is the day
// the header's statement goes stale.

#[test]
fn does_not_see_a_depends_on_naming_no_step() {
    let root = scratch("does_not_see_a_depends_on_naming_no_step");
    census(&root);
    write(
        &root,
        "datasets/d1",
        &format!("{}    depends_on: [does_not_exist]\n", plain("d1")),
    );
    let run = gate(&root);
    assert_eq!(run.code, 0, "arc's loader now refuses this:\n{}", run.stderr);
}

#[test]
fn does_not_see_the_sql_file_a_step_points_at() {
    let root = scratch("does_not_see_the_sql_file");
    census(&root);
    assert!(!root.join("datasets/d1/models/model.sql").exists());
    let run = gate(&root);
    assert_eq!(run.code, 0, "arc's loader now reads the SQL file:\n{}", run.stderr);
}
