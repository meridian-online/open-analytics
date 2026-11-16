# Test fixtures for `ourairports`

Cut-down copies of the files the Protocol fetches, downloaded on 2026-11-16, for the tests in `../tests.yaml`. The three CSVs keep OurAirports' header line and its lines exactly as OurAirports writes them (UTF-8, its quoting, LF line endings); only lines were removed. arc places each fixture at the path its fetch step writes.

| Fixture | Stands in for | Kept |
|---|---|---|
| `airports.csv` | `fetch_airports`, OurAirports' `airports.csv` | 25 airports, listed below |
| `countries.csv` | `fetch_countries`, `countries.csv` | The 16 countries the kept airports are in |
| `regions.csv` | `fetch_regions`, `regions.csv` | The 21 regions the kept airports are in |
| `listing.json` | `fetch_listing`, GitHub's listing of the repository's top folder at `main` | The entries for the three CSVs, with the fixtures' own git blob shas (`git hash-object`) and sizes, and README.md's, so verify finds them as it finds the real files |
| `release.csv` | `verify`, for the tests that rewrite a data CSV | The commit's sha and the release, 2026-11-16, as verify writes them |
| `commit.json` | `fetch_release_before` and `fetch_release_after`, GitHub's answer for the latest commit on the data repository's `main` | That day's commit, `1cdbf9c…`, committed 2026-11-16T02:53:14Z, trimmed to its `sha` and `commit.committer.date` |

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

To cut new fixtures, keep the header line and copy whole lines from the downloaded files; do not write them back through a CSV tool, which changes OurAirports' quoting. When a CSV fixture changes, put its new `git hash-object` sha and size in `listing.json`, or verify refuses it.
