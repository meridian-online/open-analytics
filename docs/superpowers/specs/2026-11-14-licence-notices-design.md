# How a hosted dataset carries its licence's notice

## Intent

Four reference lists wait on this: the UK's SOC 2020 (ONS, Open Government Licence v3.0), ISO 3166 country and subdivision codes and ISO 4217 currency codes from Unicode CLDR (Unicode License v3), country calling codes from Google's libphonenumber metadata (Apache License 2.0), and UN/LOCODE from UNECE (CC BY 4.0). Every dataset Meridian hosts so far is CC0 or public domain, so nothing has had to carry a notice. Each of these four licences asks for something to travel with every copy, and a hosted dataset reaches its reader by three routes: the Parquet a reader downloads (its descriptor is stamped into the footer), the lake's `meta.descriptors` table that arcform's lake publish writes (the whole descriptor, minus `bytes`, `hash` and `created`), and the dataset page on meridian.online. The notice has to hold on all three.

Done when each of the four licences has a stated way a published dataset carries its notice, a check here fails a descriptor that lacks it, and each source's attribution line is written down.

## What each licence asks of a copy

Read from each licence's text, with the per-source reading from Meridian's licensing reviews of 2026-10-30 (closed-set lists) and of the occupation classifications.

| Licence | Asks for | Does not ask for |
|---|---|---|
| OGL v3.0 (`OGL-UK-3.0`) | the attribution statement the provider specifies (ONS: "Source: Office for National Statistics licensed under the Open Government Licence v3.0."), and where possible a link to the licence | a copy of the licence; a statement of changes (it does forbid implying official status or endorsement) |
| Unicode License v3 (`Unicode-3.0`) | "this copyright and permission notice appear with all copies of the Data Files or Software, or in associated Documentation": the licence's full text, which opens with Unicode's copyright line | a statement of changes |
| Apache 2.0 (`Apache-2.0`) | §4: a copy of the licence to every recipient; prominent notice that modified files were changed; the source's copyright and attribution notices retained (libphonenumber: "Copyright (C) 2009 The Libphonenumber Authors"; it has no NOTICE file) | |
| CC BY 4.0 (`CC-BY-4.0`) | §3(a): the creator and copyright notice, a URI to the material, an indication of changes, and that it is under CC BY 4.0 with the licence's text or URI | a copy of the licence (a URI is enough) |

## Goals

- One stated way a descriptor carries each licence's notice, and it is inside the descriptor, so the footer copy and the lake copy carry it with no further work.
- `check_descriptors.py` refuses a descriptor whose licence asks for a notice it does not carry, and refuses a licence identifier nobody has read.
- SOC 2020's three descriptors carry their notice, so they can publish.
- Each of the four sources' attribution and changes lines written down where the next Protocol finds them.

## Non-goals

- Building the CLDR, libphonenumber or UN/LOCODE Protocols. They are their own issues; this settles what their descriptors must carry.
- The dataset page. It lives in the web repo, transcribes descriptor fields by hand, and is held to the descriptors by that repo's parity test. This states what the page shows and enters the page and parity work as a web issue.
- Checking the lake copy. arcform's lake publish writes the whole descriptor into `meta.descriptors`; nothing in this repo reads the lake yet. Stated, not checked.
- Credits that public-domain sources ask for as courtesy (Census, CDC's reuse lines). The same fields can carry them later; nothing here requires them.

## Anti-goals

- A notice that lives only on the page or only in a README: the downloaded file must answer on its own, as every descriptor here already does.
- A second file beside the Parquet (a `NOTICE` or `LICENSE` object). The repo's standing rule is that a URL is the only thing a reader needs.

## Approaches

1. **The notice rides on the licence entry (chosen).** Each `licenses[]` entry whose licence asks for a notice carries it in extension keys: `x-attribution` (the line the source asks for: who, the copyright, the source, the licence's name), `x-changes` (what Meridian did to the source), and `x-licence-text` (the licence's full text, where the licence asks for a copy). The notice sits beside the licence it answers to, so a package built from two sources under two licences carries two notices without a second structure. The Frictionless reference implementation (5.19.0) loads a licence entry with an extra key, measured 2026-11-14.
2. **A package-level `x-meridian-attribution` object.** Mirrors `x-meridian-code-list`, which is known to pass through `describe`. Separates the notice from its licence, and a two-licence package needs a list keyed back to the identifiers. The fallback if `describe` turns out to drop nested keys from a licence entry.
3. **Side files or a second footer key.** A `NOTICE` object beside the Parquet, or the licence text under its own footer key. Both miss the lake, which keeps the descriptor and nothing else, and the side file breaks the one-URL rule. Rejected.

Ruling (Hugh away, reversible): approach 1.

## The design

**A table of what each licence asks, `datasets/licence-notices.json`.** Keyed by licence identifier, every identifier used by a descriptor under `datasets/`. Each entry states, with the clause it is read from: `path` (for a licence that asks for a notice, the licence URL the entry's `path` must be; a licence asking for nothing states none, since its descriptors already point at several issuers' terms pages), `attribution` (true: `x-attribution` required), `copyright` (true: the attribution must carry a copyright mark, `©` or `Copyright`), `names_licence` (a string the attribution must contain, such as "Open Government Licence v3.0"), `changes` (true: `x-changes` required), `text_marker` (when set, `x-licence-text` is required and must contain it, e.g. `UNICODE LICENSE V3`, `Version 2.0, January 2004`). `CC0-1.0`, `other-pd` and `notspecified` are listed asking for nothing.

**Rule 10, `licence.notice`, in `check_descriptors.py`.** Reads every descriptor file under `datasets/*/` (`datapackage.json` and `<table>.datapackage.json`), published or not, because the point is to stop an unpublished descriptor before it publishes. For each licence entry: an identifier missing from the table is a refusal ("read the licence and add it"); otherwise each requirement above is checked, and the package `description` must contain the `x-attribution` line verbatim, so the lake's table comment and any reader of the description see it. A table entry with an unknown key, or a requirement without its clause, is an error (exit 2), not a pass. `examples/` descriptors are not read by this rule: their Parquet is published nowhere. The self-test adds fixtures for each refusal.

**The download.** The footer already carries the whole descriptor, and rule 2 of the self-described half already holds the footer to the repo copy, treating only `description`, `title` and `$comment` as prose. The new keys are not prose, so a footer that lacks or differs from the repo's notice is a disagreement. No new code.

**The lake.** `meta.descriptors` stores the whole descriptor; `COMMENT ON TABLE` stores the description, which now contains the attribution line. No new code here; the lake build's own tests are arcform's.

**The page.** Under the table, the page shows each licence entry's `x-attribution` and `x-changes`, with the licence's `path` as a link. A web issue carries the page and the parity-test change that holds it to the descriptor.

**SOC 2020.** Each of its three overrides and committed descriptors gains `x-attribution` ("Source: Office for National Statistics licensed under the Open Government Licence v3.0.", which each description already carries) and `x-changes` (what Meridian did, per table). `test_soc_2020_models.py` already holds descriptor and overrides together for the licence; it is extended to the new keys. A cold local `arc run` of the Protocol (arc 0.2.1 with the DuckDB 1.5.5 CLI, nothing published) regenerates the three descriptors, which stay committed as arc wrote them, and confirms `describe` and the stamp keep the keys, by reading each stamped footer back with `stamp_descriptor.py read`. If `describe` drops the nested keys, the build switches to approach 2.

**The written lines, `docs/licence-notices.md`.** The guide: the table above in prose, how a descriptor carries each notice, and each source's `x-attribution` and `x-changes`, ready for its Protocol's overrides: SOC 2020 (live in its descriptors), CLDR, libphonenumber and UN/LOCODE, from the lines Meridian's licensing review of 2026-10-30 drafted for each page, with the release left as a placeholder the Protocol fills. The README's checks section and licence section, and the code-list guide's overrides paragraph, point at it.

## Calls made on Hugh's behalf (reversible)

- Approach 1 over the package-level key.
- The full licence text goes into the descriptor for Unicode and Apache, rather than a link, because Apache asks for a copy and Unicode for the notice "with all copies", and the footer and lake are copies.
- An unread licence identifier fails the check, so a new licence cannot publish without someone reading it.
- The rule reads unpublished descriptors too.
- The description must repeat the attribution line.
- `path` is held only for licences that ask for a notice; the public-domain and CC0 descriptors keep their issuers' terms pages.
- SOC 2020 is not published by this work; it publishes through the lake publish once that lands.
- No statement of changes is required for OGL or Unicode, as their texts do not ask for one; SOC 2020 carries one anyway because Meridian's descriptions already say what it did.

## Left to the builder

The exact wording of each table's `x-changes`, the self-test fixture layout, and the doc's structure.
