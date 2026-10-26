//! Load every shipped Protocol manifest through `arc`'s own loader.
//!
//! `arc-manifest-load [ROOT]` walks `ROOT/datasets/` and `ROOT/examples/` (ROOT
//! defaults to the current directory), hands each `arcform.yaml` it finds to
//! `arc::spec::Manifest::load`, and reports the loader's verdict on each one.
//!
//! **Why `arc`'s loader and not a YAML parse.** A YAML parse accepts every document
//! `arc` refuses for its content: a precondition `arc` has no variant for, an operator
//! version the catalog cannot satisfy, a `with:` block the operator's schema rejects.
//! The only answer that means anything is the one `arc` gives, so this links `arc`
//! and asks it. The message printed for a refusal is the loader's own `Display`,
//! unedited.
//!
//! **What it does not do.** It runs no step, opens no network connection and reads no
//! file but the manifests: `Manifest::load` reads one file and validates the document
//! in isolation. So it says nothing about the step graph (a `depends_on:` naming a
//! step that does not exist is accepted), nothing about the SQL file a `sql:` step
//! points at, and nothing about whether the pipeline runs. `tests/gate.rs` pins each
//! of those as accepted, so the day `arc` starts refusing one, a test says so.
//!
//! **Exit status.** 0 when every manifest loads and every floor is met; 1 when the
//! loader refuses at least one manifest; 2 when the walk itself is refused, because
//! the root or a subtree is missing or holds fewer manifests than its floor.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use arc::spec::Manifest;

/// The filename `arc` gives a Protocol's manifest.
const MANIFEST: &str = "arcform.yaml";

/// Subtrees that must be present, and how many manifests each must hold at minimum.
///
/// Per subtree, not only in total: one total floor stays green when `datasets/`
/// disappears and `examples/` grows by as many, which is the arrangement that
/// would quietly stop checking the published packages. The numbers are the census on
/// `main` when they were last raised, four datasets and eleven examples. Brightfield
/// holds its own walk of this repository to floors of its own, which can lag these
/// without either walk passing wrongly. They are floors:
/// a new Protocol in either subtree does not trip them. What they catch is a subtree
/// shrinking or vanishing, which a walk would otherwise report as success.
const SUBTREE_FLOORS: &[(&str, usize)] = &[("datasets", 4), ("examples", 11)];

/// The sum of the subtree floors, stated once so the log shows what the walk expected.
const TOTAL_FLOOR: usize = 15;

fn main() -> ExitCode {
    let root = std::env::args_os()
        .nth(1)
        .map_or_else(|| PathBuf::from("."), PathBuf::from);

    let manifests = match collect_manifests(&root) {
        Ok(found) => found,
        Err(reason) => {
            eprintln!("arc-manifest-load: walk refused: {reason}");
            return ExitCode::from(2);
        }
    };

    let mut refused = 0usize;
    for path in &manifests {
        let shown = path.strip_prefix(&root).unwrap_or(path).display();
        let dir = path.parent().expect("a manifest path always has a parent");
        match Manifest::load(dir) {
            Ok(manifest) => println!("loaded  {shown} ({} step(s))", manifest.steps.len()),
            Err(error) => {
                refused += 1;
                eprintln!("REFUSED {shown}: {error}");
            }
        }
    }

    let loaded = manifests.len() - refused;
    let census: Vec<String> = SUBTREE_FLOORS
        .iter()
        .map(|(subtree, floor)| {
            let n = manifests
                .iter()
                .filter(|p| p.strip_prefix(root.join(subtree)).is_ok())
                .count();
            format!("{subtree}/ {n} (floor {floor})")
        })
        .collect();
    println!(
        "arc-manifest-load: {loaded} of {} manifest(s) loaded through arc's loader, \
         {refused} refused; {}; total floor {TOTAL_FLOOR}",
        manifests.len(),
        census.join(", ")
    );

    if refused > 0 {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

/// Every manifest under `root`'s floored subtrees, sorted, or the reason the walk
/// cannot be trusted. Returns `Err` rather than an empty `Ok` for each shape of
/// absence, so "found nothing" is never read as "found nothing wrong".
fn collect_manifests(root: &Path) -> Result<Vec<PathBuf>, String> {
    if !root.is_dir() {
        return Err(format!("{} is not a directory", root.display()));
    }

    let mut all = Vec::new();
    for (subtree, floor) in SUBTREE_FLOORS {
        let dir = root.join(subtree);
        if !dir.is_dir() {
            return Err(format!(
                "{} has no {subtree}/ subtree, so this is not the repository root or \
                 its layout moved and the floor is stale",
                root.display()
            ));
        }
        let mut found = Vec::new();
        walk(&dir, &mut found).map_err(|e| format!("walking {}: {e}", dir.display()))?;
        if found.len() < *floor {
            return Err(format!(
                "{subtree}/ holds {} {MANIFEST} file(s), floor is {floor}: a walk over \
                 an emptied or moved subtree reads exactly like full coverage, so it \
                 is refused instead",
                found.len()
            ));
        }
        all.extend(found);
    }

    if all.len() < TOTAL_FLOOR {
        return Err(format!(
            "{} {MANIFEST} file(s) found, total floor is {TOTAL_FLOOR}",
            all.len()
        ));
    }
    all.sort();
    Ok(all)
}

/// Depth-first walk collecting files named [`MANIFEST`]. Symlinked directories are not
/// followed: a link back up the tree would not terminate, and nothing here needs one.
fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    let mut entries: Vec<_> = fs::read_dir(dir)?.collect::<Result<_, _>>()?;
    entries.sort_by_key(fs::DirEntry::path);
    for entry in entries {
        let path = entry.path();
        let meta = fs::symlink_metadata(&path)?;
        if meta.is_dir() {
            walk(&path, out)?;
        } else if meta.is_file() && path.file_name().is_some_and(|n| n == MANIFEST) {
            out.push(path);
        }
    }
    Ok(())
}
