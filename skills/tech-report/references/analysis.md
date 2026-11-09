# Analysis

A changelog summary lists what changed. An analysis decides what matters most, why, for whom, what it does not mean, and what to do. Readers experience these decisions as a report that is "clean" and that they can trust. Each habit below comes with a test to apply to your draft. The examples use an invented project, Larkspur 3.0.

## 1. Bottom line first

The first section answers, in at most five sentences: what this is, whether it matters, for whom, and the verdict. Then the three changes that matter most, one line each. Then the main thing the release is not.

Test: a reader who stops after the first section can repeat the verdict and knows whether to read on.

## 2. Judge the shift, not the feature count

"Adds 4 connectors and 12 SQL functions" is a changelog. "Moves from a processing engine toward a storage system that other tools query" is an analysis. Look for what changed in the project's role, architecture, workflow, or cost structure. Then check whether the evidence supports that reading, and say how strongly.

Test: if the bottom line could be written from the release's table of contents alone, it is not yet an analysis.

## 3. Keep facts and judgments apart

- A fact sentence says what the source says, and cites it. A judgment sentence says what you think and why.
- Attribute claims: "the project reports twice the ingest throughput on its reference cluster [1]", not "ingest is twice as fast".
- Source adjectives (revolutionary, seamless, production-ready) are the source's claims. Quote and attribute them, or leave them out.
- Label diagrams and recommendations you made as yours.

Test: for each sentence, you can point to its ledger claim or to the reasoning behind it.

## 4. Give every claim its specific limit

After each significant claim, state what it does not imply, and be specific about it.

- Weak: "Further validation is needed." "Results may vary."
- Strong: "Tiered storage moves cold segments to object storage; it does not change the write path, so ingest latency is unaffected either way."
- Strong: "The new Python API covers job submission only; state inspection still needs the Java client."

Test: if the caveat would fit any other report unchanged, replace it or cut it. One specific limit beats three generic ones.

## 5. Give every number its conditions

Every number states the setting (hardware, size, dataset), who measured it, and what it is compared with. When results come from different setups, say that they are not a ranking. When a benchmark is near its ceiling (most entries above 95%), say so, and say what would tell the options apart instead: unseen conditions, long-running jobs, cost per result, failure recovery.

Test: from what you wrote, the reader can tell where the number came from and what it can and cannot be compared with.

## 6. Say what every change means

In the change table, the third column is what the change means in practice for this reader. If you cannot fill it in, the row probably does not belong in the top table.

## 7. Write for the reader's decision

The reader section is a short ranked list: what to do now, next, and later. Each entry has a concrete approach and a reason tied to the reader's situation. Base it on what the user said about themselves, state your assumptions, and do not invent their stack or numbers.

Separate adopting the idea from adopting the tool. Often the right recommendation is to build an adapter or keep a clean boundary rather than switch wholesale. Say which parts the reader should own and which they can take from the project.

## 8. List the risks before adoption

Cover breaking changes, new requirements, removed features, and migration paths: what each one breaks and how to check it before upgrading. A release that is days old has unknown stability. Say so, and say what evidence would settle it: issue reports, a patch release, a trial run in the reader's environment.

## 9. End with what to take and what to watch

Give at most three takeaways the reader can act on, and one thing to watch: a trend, a threat, or a dependency.

## Style

- Write plain declarative sentences. No hype, and no stacked hedges: use one qualifier where it carries meaning.
- When the reader compares things, prefer a short table over a long list.
- Do not rate on a 1–5 scale unless the rubric is written next to it. A ranked list with one reason each says more.
- Do not pad. Cut sections that restate earlier ones, and do not write a summary of the conclusion.
