# CONTEXT.md format

```md
# {Context name}

{One or two sentences on what this context is and why it exists.}

## Language

**Order**:
{One or two sentences defining the term.}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment for an **Order**, sent to its **Customer** after delivery.
_Avoid_: Bill, payment request

**Customer**:
A person or organization that places an **Order**.
_Avoid_: Client, buyer, account
```

## Rules

- **Pick one word.** When several words name a concept, choose the best and list the rest under
  `_Avoid_`.
- **One or two sentences.** Define what the term is, not what it does.
- **Bold other glossary terms inside a definition**, and let the sentence say how they relate.
- **Project terms only.** Leave out general programming concepts such as timeouts or error types,
  even if the project uses them heavily.
- **Group terms under subheadings** when clusters emerge. A flat list is fine for one cohesive
  area.

## CONTEXT-MAP.md

For several contexts, a root `CONTEXT-MAP.md` lists them, where they live and how they relate:

```md
# Context map

## Contexts

- [Ordering](./src/ordering/CONTEXT.md): receives and tracks customer orders
- [Billing](./src/billing/CONTEXT.md): generates invoices and processes payments

## Relationships

- **Ordering → Billing**: Ordering emits `OrderPlaced`; Billing consumes it to open an invoice
- **Ordering ↔ Billing**: shared types for `CustomerId` and `Money`
```
