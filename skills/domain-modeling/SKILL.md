---
name: domain-modeling
description: Build and sharpen a project's domain model. Use when discussing codebase terminology, resolving what a domain term means, writing or editing the glossary (CONTEXT.md), deciding whether two names or stored copies are the same fact, or recording or editing an ADR.
license: MIT. LICENSE has complete terms
---

# Domain modeling

First read the owning glossary and the area's ADRs, system-wide ones included. Challenge terms
against scenarios and code, and once writing is authorized, record terms and decisions as they settle.

## Files

Most repositories have one context: a root `CONTEXT.md` and `docs/adr/`.
A root `CONTEXT-MAP.md` (or `GLOSSARY-MAP.md`) means several contexts, each with its own glossary
and `docs/adr/`, plus a root `docs/adr/` for system-wide decisions; see
[references/context-format.md](references/context-format.md).

- Resolve the owning glossary and ADR directory once, at the start. Use an existing `CONTEXT.md` or
  `GLOSSARY.md` (and `CONTEXT-MAP.md` or `GLOSSARY-MAP.md`) if there is one; otherwise create
  `CONTEXT.md` lazily, when the first term is resolved and writing is authorized.
- With several contexts, infer which one the topic belongs to. If it is unclear, ask.
- Write or create glossary and ADR files only when the user asks to record the model or the
  session is already editing repository docs. In an exploratory discussion, propose the term or ADR
  and ask before writing files.

## During the session

- **Challenge against the glossary.** When the user uses a term that conflicts with the glossary,
  say so at once: "The glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"
- **Sharpen fuzzy terms.** Propose one canonical term for a vague or overloaded one: "By 'account',
  do you mean the Customer or the User? Those are different things."
- **Test with scenarios.** Invent concrete edge cases that force a precise line between two
  concepts.
- **Check the code.** When the user says how something works, check whether the code agrees, and
  state any contradiction: "The code cancels whole Orders, but you said partial cancellation is
  possible. Which is right?"
- **Canonical-fact check.** Two names, fields or stored copies are one fact when they record the
  same business assertion about the same entity; a derived cache or rebuildable projection is a
  copy of that fact. When the business meaning differs, such as an email captured on an invoice at
  issue time, they are separate facts and need separate terms. Before consolidating stored copies
  of one fact, compare row identity, cardinality and retention.
- **Write terms inline.** Once writing is authorized, update the glossary as soon as a term is
  resolved, in the format in [references/context-format.md](references/context-format.md). Don't
  batch updates.
- **Keep the glossary a glossary.** No implementation details, specs, scratch notes or
  implementation decisions.

## ADRs

Offer an ADR only when all three hold:

1. **Hard to reverse**: changing course later has a real cost.
2. **Surprising without context**: a future reader will wonder why it was done this way.
3. **A real trade-off**: there were genuine alternatives, and one was picked for specific reasons.

If any is missing, skip it. Write it in the format in
[references/adr-format.md](references/adr-format.md), in the owning context's `docs/adr/`, or the
root one for a system-wide decision; create it if missing. Number it one above the highest numeric
prefix of a regular file in that directory, starting at 0001 when empty:
`find -H "<adr-dir>" -maxdepth 1 -type f -name '*.md' 2>/dev/null | sed -n 's|.*/\([0-9][0-9]*\)-[^/]*\.md$|\1|p' | sort -n | tail -n 1`.
