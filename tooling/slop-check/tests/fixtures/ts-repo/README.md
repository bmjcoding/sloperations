# Fixture service

The server keeps its tables in `server/db/schema.ts` and its migrations in
`server/db/migrations/`. Agents follow [the agent contract](docs/agent-contract.md)
and the rules in [AGENTS.md](AGENTS.md); [`contract/rules.md` in the handbook](docs/agent-contract.md)
names the same contract.

Review records for this service live in the companion repository, at
`.review/council/DECISIONS.md`. The build writes `dist/app.js`.

```sh
# The store was renamed in an example inside a code fence, which the history check skips.
npm run build
```
