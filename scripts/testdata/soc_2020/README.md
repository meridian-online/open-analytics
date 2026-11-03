# SOC 2020 test fixtures

Cut copies of three workbooks the UK Office for National Statistics publishes for the Standard Occupational Classification 2020, downloaded on 2026-11-03. They keep the issued file names, which carry the release the models read.

Source: Office for National Statistics licensed under the Open Government Licence v3.0 (https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). The rows are committed as ONS gives them.

- `soc2020volume1structureanddescriptionofunitgroupsexcel20260827.xlsx`: the sheet `SOC2020 descriptions` of Volume 1, structure and description of unit groups, issued 27 August 2026.
- `soc2020volume2thecodingindexexcel20260827v2.xlsx`: the sheets `INFO`, `FILE SPEC` and `SOC2020 coding index` of Volume 2, the coding index (version 14), issued 27 August 2026.
- `soc2010soc2020relationshiptablesjuly2021.xlsx`: the sheet `soc2010-soc2020-map_Unit` of the relationship between SOC 2010 and SOC 2020, July 2021.

Each copy keeps the header row exactly as ONS prints it (and in the coding index, the `INFO` and `FILE SPEC` sheets whole), then only these rows:

- Volume 1: major groups 1 and 9 and every sub-major, minor and unit group beneath them (103 groups).
- Coding index: for each of those 76 unit groups, its first two entries and its first entry that carries an additional or industry qualifier; the two pairs of entries that repeat a title and both qualifiers (`President, vice` and `Operative, cleaning`, each a coded entry beside a cross-reference); and two further cross-references (`a1pc`, `a2cj`). That is 192 rows, 188 of them coded.
- Relationship tables: the first 12 blocks (a 2010 unit group's `Total sample` row and its 2020 pairings) whose every 2020 group is a unit group in the Volume 1 copy, so the contract check holds against a correct model. They include 2010 groups whose jobs went to two, three and four 2020 groups (`1116`, `1134`, `1162`), and a pairing with a share of 0 (`1116` to `1111`, men, Labour Force Survey).

The test writes the `.arcmeta` sidecar `http_fetch@1` writes beside each copy, naming the real ONS URL, since the models read each release from it.
