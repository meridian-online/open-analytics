# Test fixtures for `ourairports`

Cut-down copies of the files the Protocol fetches, downloaded on 2026-11-16, for the tests in `../tests.yaml`. The three CSVs keep OurAirports' header line and its lines exactly as OurAirports writes them (UTF-8, its quoting, LF line endings); only lines were removed. arc places each fixture at the path its fetch step writes.

| Fixture | Stands in for | Kept |
|---|---|---|
| `airports.csv` | `fetch_airports`, OurAirports' `airports.csv` | 25 airports, listed below |
| `countries.csv` | `fetch_countries`, `countries.csv` | The 16 countries the kept airports are in |
| `regions.csv` | `fetch_regions`, `regions.csv` | The 21 regions the kept airports are in |
| `tree.json` | `fetch_tree`, GitHub's git tree of the repository's top folder at `main` | GitHub's shape on one line (the commit `sha`, `tree` entries with `path`, `mode`, `type`, `sha` and `size`, and `truncated`), with entries for the three CSVs carrying the fixtures' own git blob shas (`git hash-object`) and sizes, and README.md's real entry |
| `release.csv` | `verify`, for the tests that rewrite a data CSV | The commit's sha and the release, 2026-11-16, as verify writes them |
| `commit.json` | `fetch_release_before` and `fetch_release_after`, GitHub's answer for the latest commit on the data repository's `main` | That day's commit, `1cdbf9c…`, committed 2026-11-16T02:53:14Z, trimmed to its `sha`, `commit.committer.date` and `commit.tree.sha`. The tree sha is not the real one (`437dbde…`) but the sha git computes for the fixture tree in `tree.json`, `ee87e71…`, so verify's tree check passes on the fixtures |
| `tree_with_folder.json`, `commit_with_folder.json` | `fetch_tree`, and both release fetches, in the test that a tree with a folder hashes as git hashes it | `tree.json` with a folder entry added, `regions` (mode `040000`, as GitHub writes a folder's, type `tree`, the empty tree's sha), named so it sorts after `regions.csv` as git sorts a folder; and `commit.json` with `commit.tree.sha` set to that tree's git sha, `f01a622…`, as `git mktree --missing` prints it for those entries |

The airports, by ICAO code (OurAirports' `ident` in brackets where it differs):

| ICAO | Airport | Why |
|---|---|---|
| EGLL | London Heathrow Airport | The row the field checks read; its IATA code, LHR, is in `iata_code` only |
| KJFK | John F. Kennedy International Airport | A US airport whose `local_code` (the FAA identifier) is JFK |
| KLAX | Los Angeles International Airport | `local_code` LAX, the same letters as its IATA code, kept |
| CYBB | Kugaaruk Airport | `keywords` holds its IATA code, YBB, with other text: neither may reach the table |
| PPIT (`16A`) | Nunapitchuk Airport | `ident` is not the ICAO code; `local_code` is 16A |
| LOAL (`AT-0001`) | Pöchlarn-Wörth Heliport | A heliport with an ICAO code, accented |
| EDUY | Welzow-Sedlitzer See Seaplane Base | A seaplane base with an ICAO code |
| PAKC (`KCC`) | Coffman Cove Seaplane Base | A seaplane base whose `ident` fell back to its local code; NULL `elevation_ft` |
| ENAN | Andøya Airport, Andenes | Accented, with a comma |
| ENBN | Brønnøysund Airport, Brønnøy | Accented, with a comma |
| LGKF | Kefalonia International Airport "Anna Pollatou" | Double quotes in the name |
| SBGR | São Paulo/Guarulhos–Governor André Franco Montoro International Airport | Accents and an en dash; region São Paulo |
| SLCR (`BO-0026`) | Carrasco Airport | Region `BO-U-A`, one OurAirports has not assigned, named "(unassigned)" |
| YWSN (`AU-0785`) | Woodstock Station | NULL `elevation_ft` and `municipality` |
| ZSLA (`CN-0511`) | Lu'an Airbase | An apostrophe; NULL `elevation_ft` and `municipality` |
| EDDF, FAOR, LFPG, NZAA, OMDB, RJTT, YSSY | Frankfurt, Johannesburg, Paris CDG, Auckland, Dubai, Tokyo Haneda, Sydney | Large airports in Europe, Africa, Asia and Oceania; RJTT's region, Tōkyō Prefecture, and OMDB's Arabic keywords |
| none (`00A`) | Total RF Heliport | No ICAO code: left out |
| none (`EGLS`) | Old Sarum Airfield | No ICAO code, though its `gps_code` is EGLS: left out |
| none (`EGBE`) | Coventry Airport | A `closed` airport with no ICAO code: left out |

So the table built from the fixture has 22 rows.

To cut new fixtures, keep the header line and copy whole lines from the downloaded files; do not write them back through a CSV tool, which changes OurAirports' quoting. When a CSV fixture changes, put its new `git hash-object` sha and size in `tree.json`, and the new tree's sha in `commit.json`'s `commit.tree.sha`, or verify refuses it. The tree's sha is git's hash of `tree <length>` NUL and, for each entry in name order, `<mode> <path>` NUL and the entry's 20-byte sha: in a git repository, `git mktree --missing` over the entries prints it.

The lake fixtures were emitted from the open lake on 2026-11-17 with `python scripts/emit_descriptor.py --all --bundle <file>`, from the repository root, with `census.naics_2022` replaced on 2026-11-18 by the descriptor its Protocol builds on the code-list contract, as the lake will hold it once that build is published, and need regenerating when a table they hold is republished with different declarations:

| Fixture | Stands in for | Kept |
|---|---|---|
| `lake_descriptors.json` | `emit_descriptors`, the bundle of every published table's descriptor | The twelve tables published with a `<slug>/datapackage.json`, which is every table the label check before publish compares; the six SOC tables, published with per-table descriptors, are left out because the check does not compare them. None of the twelve can go: `datasets/label-agreement.json` pins values from every one of them, and a pinned value that goes missing is a stale reason, which fails the check |
| `described.json` | `describe`, in the test that runs the check before publish for real | The lake's descriptor of `library.ourairports`, as it is in `lake_descriptors.json`. finetype infers a descriptor from the rows it reads, so on the fixture's rows it labels some columns differently from the full table, and the label check would refuse that descriptor for a reason that is not a defect |
| `check_before_publish.json` | `check_before_publish`, in the tests that run `describe` on the fixture | A stand-in report, which no step left in a test reads |
