# Report

Write in the user's language, headings included. The type file ([release](types/release.md)) sets which sections appear and in what order. This file sets how every report is marked up, so that `check_report.py` can check it and `render.py` can render it. The markup is plain Markdown with a few conventions, so the file also reads well as text.

The examples below use an invented project, Larkspur 3.0, a stream-processing engine. They show form only.

## Meta line

Right under the title, before the first `##` heading, write one line:

```markdown
# Larkspur 3.0: the storage rewrite is the release; wait for 3.1 before upgrading

**Subject** Larkspur 3.0 (released 2026-03-02) · **As of** 2026-03-09 · **Reader** data platform team running 2.x, deciding when to upgrade (from the request) · **Basis** 4 primary sources, 1 secondary
```

- The as-of date is the ledger's `as_of`. `check_report.py` looks for it in this line.
- The reader part names the decision the report serves, and says where the description came from: the request, the conversation, or "assumed".
- The title states the thesis, not just the subject. "Larkspur 3.0: the storage rewrite is the release" beats "Larkspur 3.0 analysis".

## Citations

- Cite a fact with its ledger source number right after the sentence or table cell it supports: `The project reports twice the ingest throughput on its own benchmark [2].` For several sources: `[1, 3]`.
- Keep the ledger's numbers even when some sources end up uncited. Do not renumber.
- A sentence that mixes a fact and a judgment cites the fact. Better: split it in two.
- In tables, cite in the cell that holds the fact. Cells that say what a change means or what to do are yours and carry no citation, even in a row whose other cells cite.
- Do not cite judgments. A citation says "the source says this", and a judgment the source does not make must not look as if it did.
- Once a number has been cited, you can repeat it anywhere (a limit, the conclusion) without citing it again.
- A finding that something is absent ("no 3.0.1 patch yet", "the docs mention Windows only on the community page") has no quote. Back it with a quote that shows it when one exists: the newest tag in a release list, the full list of a config's fields. Otherwise write it in your own voice with what you searched and when ("of the 40 doc pages at 3.0.0, only the community page mentions Windows"), uncited, and name it in the hand-over as checked by search.

## Callouts

A blockquote that starts with a bold label is a callout. The renderer styles it. The checker treats judgment callouts as judgment, so numbers inside them need no citation.

| Kind | Labels | Use |
| --- | --- | --- |
| Judgment | 판단, 평가, 의견 / Judgment, Assessment, Our view | your reading of what a change means |
| Limit | 다만, 주의, 한계 / However, Caveat, Limit | what a claim does not imply, when it is easy to miss |
| Note | 참고 / Note | context the reader may need |

```markdown
> **Judgment** The storage rewrite matters more than the new SQL functions: it changes what a cluster costs to run, not just what it can express.

> **However** Tiered storage moves cold segments to object storage; it does not change the write path, so ingest latency is unaffected either way.
```

Use callouts sparingly: usually one judgment callout per deep-dive section (two when the section makes two separate calls), and a limit callout only where the limit is easy to miss. Put the facts a judgment rests on, with their citations, in the prose before it; the callout can then repeat a number without citing it. Judgments in your own voice ("I read this as…", "제 판단으로는…") are fine in prose too.

## Tables

Use a table when the reader compares rows: changes by area, numbers with their conditions, priorities, breaking changes. Keep cells short and put explanations in prose. Every table that lists changes has a column for what the change means, not only what it is. Start each table row with `|`, and escape a `|` inside a cell as `\|`.

## Diagrams

Draw a diagram when a change moves a boundary or adds a layer: where something runs, what calls what, what now sits between two parts. A full release report usually has two or three. One shows the headline change, as a before and after pair of `flow` blocks or a `layers` block. Draw the before only from a source that describes it; otherwise draw the after alone. Another shows how the reader's system would attach to the project, in the reader section. A list of features is not a diagram; use a table.

Two fenced blocks render as diagrams and still read as plain text:

```layers
Application | jobs, dashboards
Query layer | SQL, stream joins
Storage (new in 3.0) | local hot tier, object-storage cold tier
```

```flow
Ingest | Kafka topics
Process | Larkspur jobs
Serve | query API
```

`layers` stacks rows from top to bottom, and `flow` lays steps out from left to right. Each line is `title | detail`; the detail is optional. On the next line, put an italic caption that says whether the diagram is official or your own, and what it does not imply:

```markdown
*Conceptual diagram, not the project's official architecture.*
```

## Sources

The last section lists every cited source, one per line, in ledger number order:

```markdown
## Sources

[1] Larkspur 3.0 release notes — https://example.org/larkspur/releases/3.0 · primary · read 2026-03-09
[2] Larkspur blog: "Tiered storage in 3.0" — https://example.org/larkspur/blog/tiered-storage · primary · read 2026-03-09
[4] Independent benchmark by a user group — https://example.net/bench/larkspur-3 · secondary · read 2026-03-09
```

Each line has the number, a title, the URL (or, for a file, the label used in the ledger), primary or secondary in the report's language (1차 or 2차 in Korean), and the date it was read. For text saved through your own tool (`via: agent`), add "text via agent tool". A source you read only in part can say so ("read: upgrade and storage sections"). Numbers are never reused, so a removed or uncited source leaves a gap in the list. That is expected.

A primary source is made by whoever owns what the claim is about: the project for its own release, a plugin's own repository for that plugin, a platform's own documentation for the platform. A secondary source reports on something its author does not own: independent benchmarks, issue reports from users, press, one company's blog post about another's project.

## Length

`check_report.py` prints the body length as `stats.length`: characters without whitespace, tables included, from the first `##` heading to the sources list. In Korean, a full release report runs about 6,000 to 8,500 of them (in English, about 2,000 to 3,000 words). Keep each deep dive to what the decision needs: one or two paragraphs, a judgment, and its limit.

When a draft runs long, cut in this order:
1. Sentences and table rows that repeat another section.
2. Detail inside deep dives that no recommendation uses.
3. Numbers that bear on no decision. Headline numbers stay in the numbers table, merged into one row if no recommendation uses them.
4. The lowest-ranked deep dive. Keep the headline and the reader's top change.

Never cut the bottom line, the limits, or rows of the before-upgrading table.

For a short request ("briefly", "간단히"), write only the bottom line, the change table, the reader section, and the sources list: about 2,000 to 4,000 characters by the same count. The bottom line names the headline in one sentence. Breaking changes and new requirements go into the change table as their own rows, marked as such, and the reader section says what to check before upgrading.

## Claim ledger

`<evidence>/ledger.json` is created by `sources.py init`. `fetch` and `add` fill `sources`, and `remove` marks one as removed. You write the claims as a JSON list in a file of your own and load them with `sources.py claims <file> --dir <evidence>`. Do not edit `sources` by hand.

```json
{
  "topic": "Larkspur 3.0",
  "reader": "data platform team running 2.x",
  "as_of": "2026-03-09",
  "allow": ["P0", "P1", "P2", "P3"],
  "sources": [
    {"id": 1, "title": "Larkspur 3.0 release notes", "url": "https://example.org/larkspur/releases/3.0",
     "kind": "primary", "via": "fetch", "accessed": "2026-03-09", "file": "sources/1.txt", "chars": 18234}
  ],
  "claims": [
    {"id": "C1", "source": 1,
     "quote": "Ingest throughput doubles to 2.4 GB/s per node on the reference cluster",
     "claim": "Ingest throughput rises to 2.4 GB/s per node",
     "conditions": "reference cluster, 8 nodes, measured by the project"},
    {"id": "C2", "source": 1,
     "quote": "Java 21 is now the minimum supported runtime",
     "claim": "Requires Java 21 or later"},
    {"id": "C3", "derived_from": ["C4", "C5"], "formula": "(180-45)/180*100",
     "claim": "Storage cost per TB drops by 75% in the project's example"}
  ]
}
```

- `id`: unique, such as `C1`, `C2`, …. The report cites sources, not claim IDs, so renumbering while drafting is harmless.
- `source`: the source number.
- `quote`: verbatim from the saved text. Use the shortest span that holds the fact and its numbers, usually one sentence. `...` inside a quote skips text, and the parts must appear in order. When the saved text has markup inside the sentence (HTML tags in a table cell, a link target), quote around it with `...`.
- `claim`: the fact in your own words, in the report's language. Every number in it must appear in the quote.
- `conditions`: the setting the fact holds under, taken from the source: hardware, size, dataset, who measured, and which version, branch, or product it belongs to when the quote does not say. Numbers here must appear in the source (its saved text or its URL), and the report may then state them with a citation. So "Larkspur 2.7 release notes" goes here, not in `claim`, when the quote never says 2.7.
- For a page that changes over time (a search API, a "latest releases" list, repository metadata), the saved text is the snapshot of record and the source's `accessed` date is its timestamp. A date in `conditions` may be that day.
- A derived claim has `derived_from` (IDs of quoted claims) and `formula` instead of `source` and `quote`. The formula uses only `+ - * /`, parentheses, and numbers from the input quotes (plus 1, 10, 100, 1000). The claim must state the formula's value, rounded as written.
- `allow`: tokens that look like facts but are not checked, such as priority labels (the default is `P0` to `P3`), and the reader's own figures from the request (the version they run, their volumes), which no source can back. Keep it short: an allowed number is never checked anywhere in the report.
