# How a hosted dataset carries its licence's notice

Most hosted datasets are CC0 or public domain and ask nothing of a copy. Four licences do ask: the UK's Open Government Licence v3.0 (SOC 2020, from ONS), the Unicode License v3 (ISO 3166 country and subdivision codes and ISO 4217 currency codes, from Unicode CLDR), Apache License 2.0 (country calling codes, from Google's libphonenumber metadata) and CC BY 4.0 (UN/LOCODE, from UNECE). Each asks for something to travel with every copy, and a hosted dataset reaches a reader by three routes: the Parquet file, whose footer carries the descriptor; the lake's `meta.descriptors` table; and the dataset page on meridian.online. This guide says what each licence asks, how a descriptor carries it so that all three routes hold it, and the lines each source's descriptor carries.

## What each licence asks of a copy

Read from each licence's text.

| Licence | Asks for | Does not ask for |
|---|---|---|
| OGL v3.0 (`OGL-UK-3.0`) | the attribution statement the provider specifies (ONS: "Source: Office for National Statistics licensed under the Open Government Licence v3.0."), and where possible a link to the licence | a copy of the licence; a statement of changes (it does forbid implying official status or endorsement) |
| Unicode License v3 (`Unicode-3.0`) | the copyright and permission notice to appear "with all copies of the Data Files or Software, or in associated Documentation": the licence's full text, which opens with Unicode's copyright line | a statement of changes |
| Apache 2.0 (`Apache-2.0`) | section 4: a copy of the licence to every recipient; prominent notice that modified files were changed; the source's copyright and attribution notices retained (libphonenumber's is "Copyright (C) 2009 The Libphonenumber Authors"; it has no NOTICE file) | |
| CC BY 4.0 (`CC-BY-4.0`) | section 3(a)(1)(A), where supplied by the licensor: identification of the creator, a copyright notice, a notice that refers to the licence, a notice that refers to the disclaimer of warranties, and a URI to the material; and an indication of changes (3(a)(1)(B)); and that it is under CC BY 4.0 with the licence's text or URI (3(a)(1)(C)) | a copy of the licence (a URI is enough) |

`CC0-1.0`, `other-pd` and `notspecified` ask nothing, and their descriptors carry no notice.

## How a descriptor carries it

The notice rides on the `licenses[]` entry it answers to, in three extension keys:

- `x-attribution`: the line the source asks for: who, the copyright, the source, the licence's name.
- `x-changes`: what Meridian did to the source.
- `x-licence-text`: the licence's full text, where the licence asks for a copy.

It sits beside its licence so a package built from two sources under two licences carries two notices without a second structure. A notice at package level does not count, because the entry names the licence and so carries what it asks.

The package `description` must also contain the `x-attribution` line verbatim, so a reader of the description, and the lake's table comment, see it without opening the entry.

`datasets/licence-notices.json` says, per licence identifier, what that licence asks, with the `clause` it is read from. A clause is the reading of the licence, a paraphrase or excerpts stitched together, not a quotation. Its keys are `path` (the licence URL the entry's `path` must be; held only for a licence that asks for a notice, since the public-domain and CC0 descriptors point at their several issuers' terms pages), `attribution` (`x-attribution` required), `copyright` (the attribution must carry `©` or `Copyright`), `names_licence` (a string the attribution must contain; for OGL it means the attribution must name the licence, which ONS's specified statement does, and a provider whose specified statement does not would need the table entry revisited), `changes` (`x-changes` required) and `text_marker` (`x-licence-text` required and must contain it). Rule 10 of `scripts/check_descriptors.py`, `licence.notice`, holds every descriptor under `datasets/` to its licence's entry, unpublished descriptors included, so a descriptor that lacks its notice is stopped before it publishes. A licence identifier the table does not list is refused: a new licence cannot publish until someone has read it and added it. A table that is missing, unparseable or carries an unknown key is an error, not a pass. Descriptors under `examples/` are not read by this rule, since their Parquet is published nowhere.

## Where it travels

- **The download.** The Parquet footer carries the whole descriptor, and the self-described half of `check_descriptors.py` holds the footer to the repo copy, treating only `description`, `title` and `$comment` as prose. The notice keys are not prose, so a footer that lacks or differs from the repo's notice is a disagreement. Nothing new is needed.
- **The lake.** arcform's lake publish writes the whole descriptor into the lake's `meta.descriptors` table and the package description as the table comment, so the notice and the attribution line arrive there with it. The SOC 2020 tables' were read back from the live catalogue at their first publish on 2026-11-14 (see [`docs/publishing.md`](publishing.md)). This repo's CI does not read the lake and does not check this.
- **The page.** The dataset page will show each licence entry's `x-attribution` and `x-changes`, with the entry's `path` as a link to the licence. That change is still to come. The page lives in the website repo and transcribes descriptor fields by hand, held to the descriptors by that repo's parity test, so the page and its test change there.

## Each source's lines

Each source's lines go in its Protocol's overrides, on the `licenses` entry, and the description repeats the attribution.

### SOC 2020

`x-attribution` is the same on all three descriptors: "Source: Office for National Statistics licensed under the Open Government Licence v3.0." `x-changes` says what Meridian did for each table, as committed in `datasets/soc_2020/*.overrides.json`. Published through the lake publish on 2026-11-14.

### CLDR codes (country, subdivision, currency)

`x-attribution`, with `<NN>` the CLDR release and `<year>` the year of Unicode's copyright line in that release's `license.txt`:

> Codes and English names from the Unicode Common Locale Data Repository (CLDR) v<NN>, © 1991–<year> Unicode, Inc., under the Unicode License v3. Code elements are assigned by ISO 3166/MA.

For the currency codes, name SIX as the maintenance agency in place of the ISO 3166/MA sentence. `x-licence-text` is the full `license.txt` of that CLDR release. No `x-changes` is required.

### Country calling codes (libphonenumber)

`x-attribution`, with `<X.Y.Z>` the libphonenumber release:

> Country calling codes derived from libphonenumber's PhoneNumberMetadata.xml (v<X.Y.Z>), Copyright (C) 2009 The Libphonenumber Authors, licensed under the Apache License 2.0. Assignments are made by the ITU.

`x-changes`: "Meridian grouped and sorted the entries." `x-licence-text` is the full Apache License 2.0 text.

### UN/LOCODE

`x-attribution`, with `<release>` the UN/LOCODE release:

> UN/LOCODE, release <release>, © United Nations Economic Commission for Europe, from unlocode.unece.org, licensed under CC BY 4.0.

`x-changes`: "Meridian selected columns and sorted the entries." The licence's URI is the entry's `path`, which is enough; no licence text is carried.

## Decisions

- **The notice rides on the licence entry.** The alternatives were a package-level `x-meridian-attribution` object, which mirrors `x-meridian-code-list` but separates the notice from its licence, so a two-licence package needs a list keyed back to the identifiers; and side files or a second footer key (a `NOTICE` object beside the Parquet, or the licence text under its own footer key), which miss the lake, since it keeps the descriptor and nothing else, and break the rule that a URL is the only thing a reader needs. The entry route was measured: the Frictionless reference implementation (5.19.0) loads a licence entry with an extra key. If `describe` ever drops the keys from a licence entry, the package-level object is the fallback.
- **The full licence text goes into the descriptor for Unicode and Apache**, not a link. Apache asks that every recipient get a copy and Unicode that its notice appear "with all copies", and the footer and the lake copy are copies.
- **An unread licence identifier fails the check**, so a new licence cannot publish without someone reading what it asks.
- **The rule reads unpublished descriptors too**, because its point is to stop a descriptor before it publishes.
- **The description repeats the attribution line**, so the lake's table comment and any reader of the description see it.
- **`path` is held only for licences that ask for a notice.** Public-domain and CC0 descriptors keep their issuers' terms pages.
- **No statement of changes is required for OGL or Unicode**, since their texts do not ask for one. SOC 2020 carries one anyway because its descriptions already say what Meridian did.
- **Nothing is required of public-domain sources.** Credits they ask for as courtesy (Census, CDC's reuse lines) can use the same keys later.
