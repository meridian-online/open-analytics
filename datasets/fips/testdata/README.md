# Test fixtures for `census/fips`

Cut-down copies of the three Census files the Protocol fetches, downloaded on 2026-11-16, for the tests in `../tests.yaml`. Each keeps Census's header line and its lines exactly as Census prints them (UTF-8, `|`-delimited, LF line endings); only lines were removed.

| Fixture | Stands in for | Kept |
|---|---|---|
| `national_state.csv` | `fetch_states`, Census's `national_state2020.txt` | Alabama (01), Connecticut (09), DC (11), New Mexico (35), American Samoa (60), Puerto Rico (72), the U.S. Minor Outlying Islands (74) |
| `national_county.csv` | `fetch_counties`, `national_county2020.txt` | 22 counties: 01001, 01003, 01005; Connecticut's eight counties 09001 to 09015; 11001; 35001 and 35013 (Doña Ana County, an accented name); American Samoa's five districts (Manu'a District has an apostrophe); 72001 and 72097 (Mayagüez Municipio); 74300 |
| `gazetteer_counties.csv` | `extract_gazetteer`, `2026_Gaz_counties_national.txt` | The 8 of those counties the Gazetteer lists (it lists none of Connecticut's old counties, American Samoa's or the Minor Outlying Islands'), and Connecticut's nine planning regions, 09110 to 09190 |
| `gazetteer.zip` | `fetch_gazetteer`, `2026_Gaz_counties_national.zip` | `gazetteer_counties.csv` under Census's member name, `2026_Gaz_counties_national.txt`, so `extract_gazetteer` runs on it |

The fixtures are named `.csv` so `arc test` can rewrite them for a `break:`; arc places each at the `.txt` path its step writes, which is where `load.sql` reads it and takes the vintage from the name. If `gazetteer_counties.csv` changes, rebuild the zip from it: copy it to `2026_Gaz_counties_national.txt` in an empty folder and run `zip -X gazetteer.zip 2026_Gaz_counties_national.txt` there.
