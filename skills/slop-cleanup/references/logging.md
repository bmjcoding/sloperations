# Structured logging

## Conventions

- Keep the repository's event naming convention. Renaming events, unifying timestamp fields, or
  changing logger behavior is an operational-interface change: escalate it, and migrate consumers
  or alias a field on the one record. Never emit duplicate records to bridge a rename.
- One logger. TypeScript bans `console.*` outside the logger module and CLI `warn`/`error`
  (`no-console`). Python bans `print` outside CLIs (`T20`), root-logger use (`LOG`) and eager
  f-string messages (`G004`); `logging.getLogger(__name__)` and `structlog.get_logger(__name__)`
  propagating to one JSON handler are fine.

## The static `logs` check (blocking)

`[logs] loggers` names the logger objects; calls look like `telemetry.info(event, fields)` (set
`event_arg`/`fields_arg` for other shapes). A call counts when its target roots at a configured
logger, so `telemetry.child({}).info(...)`, `self.log.info(...)`, `log.bind(...).info(...)` and a
`const child = telemetry.child(...)` alias are all checked. For each call, `slop-check`:

- fails a literal field whose name contains a secret word (`password`, `token`, `authorization`,
  `cookie`, `secret`, `apikey`, `privatekey`; as whole words or with separators removed, so
  `accessToken`, `apiKey` and `api_key` fail and `inputTokens` passes), whatever the event name is;
- resolves the event name through the binding in scope: a string literal, a literal ternary, a
  `const` string or a `const` object of strings (`as const` included) in the same file or imported
  from a scanned file, a TypeScript parameter typed as a union of string literals, or a Python
  module constant (also when imported from a scanned module). Anything else fails as
  `logs/dynamic-event`: a template, concatenation, call or f-string, and a variable or parameter
  that does not resolve to literals;
- reconciles every `(event, literal fields)` pair with `log-contract.json` and fails on any
  difference: an event the contract lacks, a contract event with no producer in the source roots, a
  literal field the contract does not list, or a required field missing at a producer. Fields bound
  to a `const` object count as literal.

```json
{ "events": {
  "request": { "fields": ["method", "route", "status", "ms"], "optional": ["err"] },
  "scribe answer": { "fields": ["model", "inputTokens"], "spread": ["inputTokens"] } } }
```

`spread` lists the required fields a producer may supply through a spread (`...usage`, `**extra`) or
a non-literal fields argument; every other required field must be written literally at each
producer. Python positional arguments after the message are format arguments, not fields. The
static limits: fields added by the logger at run time, a logger reached through a function argument,
and spread contents are invisible here; the producer tests cover them. Test roots are not producers.

## Producer sink tests (blocking, in the unit suite)

For every published event in `log-contract.json`, a unit test drives the real producer (the
request handler, not a synthetic `info("hello")`) against an in-memory sink and asserts the
emitted JSON: every required field, the envelope shape, trace and span propagation, error
serialization and redaction. TypeScript, with `node:test`:

```ts
test("request records carry the contract fields", async () => {
  const lines: string[] = [];
  const app = createApp({ sink: (line) => lines.push(line) });
  await app.inject({ method: "GET", url: "/health" });
  const rec = lines.map((l) => JSON.parse(l)).find((r) => r.message === "request");
  assert.deepEqual(Object.keys(rec).filter((k) => ["method", "route", "status", "ms"].includes(k)).sort(), ["method", "ms", "route", "status"]);
});
```

Python, with pytest's `caplog` or a handler on the module logger, asserting the record's fields the
same way.

## A logger that breaks an invariant today

When the current logger violates a target invariant (for example, caller fields spread after the
envelope overwrite `time`, `level`, `message` or `service`), the behavior-neutral cleanup records a
failing probe in the ledger as an unresolved, escalated invariant, and does not claim it is
enforced. The blocking regression test lands with the separate, owner-approved fix.

## Compatibility

`log-contract.json` is a policy path. Because reconciliation forces any code-side rename or field
removal into a contract edit, `slop-check policy` sees every
removed event, removed field, field made optional or allowed through a spread, and refuses it
without the owner's signed approval. Additions are reported for review.
