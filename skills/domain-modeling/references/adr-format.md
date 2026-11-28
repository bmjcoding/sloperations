# ADR format

ADRs live in `docs/adr/` as `0001-slug.md`, `0002-slug.md` and so on.

```md
# {Short title of the decision}

{One to three sentences: the context, the decision and why.}
```

One paragraph is enough.

Add these only when they carry information:

- `Status` frontmatter (`proposed`, `accepted`, `deprecated` or `superseded by ADR-NNNN`), when the
  decision may be revisited.
- **Considered options**, when a rejected alternative is worth remembering.
- **Consequences**, when a downstream effect is not obvious.

## What qualifies

- **Architectural shape**: "The write model is event-sourced; the read model is projected into
  Postgres."
- **Integration between contexts**: "Ordering and Billing communicate through domain events, not
  synchronous HTTP."
- **Technology with lock-in**: the database, message bus, auth provider or deployment target. Not
  every library, only the ones that would take a quarter to replace.
- **Ownership and scope**: "The Customer context owns customer data; other contexts refer to it by
  ID only." A recorded no is as useful as a yes.
- **Deliberate deviations from the obvious path**: "Hand-written SQL instead of an ORM, because X."
  These stop the next engineer from undoing something deliberate.
- **Constraints the code can't show**: "Responses must stay under 200ms because of the partner API
  contract."
- **Non-obvious rejections**: an alternative someone will suggest again unless the reason is
  written down.
