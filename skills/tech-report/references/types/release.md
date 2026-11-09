# Type: release

Use this type for a software, model, dataset, or hardware release, a major version, or a similar announcement.

## Sources

Collect primary sources in this order:

1. Release notes or the changelog for the version.
2. The migration or upgrade guide.
3. The official announcement or blog post.
4. Documentation pages for the headline features.
5. The pull requests or design documents behind those features, when the notes are thin.
6. When the reader runs a named platform, that platform's requirements or compatibility page (supported runtimes, library versions), so claims about running the two together rest on both sides.
7. When the reader upgrades across several versions (2.4 to 3.0), the release notes and migration sections of every version in between. The change that blocks them may come from an intermediate release.

For projects hosted on GitHub, the release API returns the notes as text: `https://api.github.com/repos/<owner>/<repo>/releases/tags/<tag>`.

Secondary sources cover what the project cannot say about itself:
- independent benchmarks or evaluations;
- the issue tracker since the release (regressions, install failures), as early evidence of stability;
- credible write-ups from practitioners.

Read the full notes. Announcement posts often leave out breaking changes.

## Questions

Answer these from the ledger before writing:

1. What changed, grouped by area (not by pull request)?
2. Which two to four changes matter most for this reader, and why? What do they change: the project's role, the architecture, the workflow, or the cost? Which change does the project's own announcement lead with, and is it on your list?
3. For each of those, what does it not imply?
4. Which numbers are headline claims? Who measured them, under which conditions, and are they comparable with each other?
5. What breaks on upgrade? New requirements (drivers, runtimes), removed or renamed APIs, changed defaults, migration steps.
6. What should this reader do now, next, and later?
7. What is too early to judge?
8. What does the release say about where the project is heading, and what should the reader watch?

## Sections

1. **Title and meta line.** The title states the thesis.
2. **Bottom line.** The verdict, the three key changes, and the main limit.
3. **Changes at a glance.** A table: area | change | what it means.
4. **Deep dives, two to four,** ordered by what matters to this reader. For each: what changed (cited), why it matters (judgment), and its limits. Add a diagram when the change is structural ([report](../report.md#diagrams)). When the announcement's headline is not among your picks, give it a short deep dive anyway: what it is, and why it ranks lower for this reader.
5. **Numbers and how to read them.** A table: number | conditions | measured by | how to read it. Only when the release makes performance claims. Keep the headline numbers and those a recommendation uses.
6. **What it means for the reader.** A table: priority | task | approach | why. When the recommendation is an integration, add a diagram of how the reader's system attaches.
7. **Before upgrading.** A table: change | impact | how to check. For a reader who does not use the project yet, call it "before adopting" and list the new requirements and the breaking changes that make older tutorials and examples wrong.
8. **Outlook.** The direction, what to watch, and what is too early to judge.
9. **Conclusion.** At most three takeaways, then the one thing to watch most, repeated from the outlook on purpose.
10. **Sources.**

Drop sections 5 and 7 when the release has no performance claims or no breaking changes. Say so in one line instead of leaving a gap.
