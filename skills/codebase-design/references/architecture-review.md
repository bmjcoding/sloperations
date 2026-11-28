# Architecture review

Find where the code gets "should these pieces change together?" wrong, group the problems under the
fixes that resolve them, and present ranked deepening candidates. Change no code until the user
picks a candidate.

## 1. Scope

- If the user named a module, subsystem or pain point, review that.
- Otherwise find the hotspots. Deepening pays off where code keeps changing.

  ```sh
  # Files changed most often
  git log --since=6.months --format= --name-only | sort | uniq -c | sort -rn | head -30
  ```

  If no hotspot stands out, widen the window or paths. For each hotspot, list files changed in the
  same commits (`git log --since=6.months --format= --name-only --full-diff -- <path>`). That is
  a clue; base each finding on the code itself.
- Read the project's glossary and the ADRs for the area. Don't re-propose what an
  ADR settled unless the friction is real enough to reopen it.

## 2. Log every problem

Walk the scoped code, or give each subagent a part of it in a fresh context and this format. Write
one line per problem, with `path:line`, under one of four kinds. Verify each subagent's cited
`path:line` against the code before grouping.

- **Hidden links**: pieces that must change together where the code doesn't show it. Copied logic
  or values; a module reading a global or file another module sets; one feature split across
  folders by layer; a call order or convention callers just have to know.
- **Needless links**: pieces tied together that change for different reasons. Callers that depend
  on internals; stable code importing more volatile code; a general function with a parameter only
  one workflow understands; a shallow module whose interface changes whenever its internals do.
- **Surplus**: pieces with no reason of their own to change. Pass-through wrappers; extension
  points and config for things that never vary; dead code.
- **Special cases**: code that exists for one situation instead of following from how the system
  works. An `if` for one value, a parameter for one caller, a swallowed exception, a retry or
  sleep, a disabled test. Each is a requirement the design never accounted for.

Apply the deletion test to every suspected shallow module. Also note concepts that take many small
modules to understand, pure functions extracted only for testability while the bugs live in how
they are called, and behavior that is hard to test through the current interface.

## 3. Group problems under fixes

Work out each fix as if the system had known today's requirements from the start. Look for one fix
behind several problems; it often looks nothing like them. Don't fix special cases one by one.
Check every fix against one rule: pieces that change together end up together, pieces that don't
aren't tied, and everything depends on what changes less than itself.

Write the decision log with each fix above the problems it resolves:

```md
- Make the general `Dialog` give focus back to whatever had it before it opened, so the wait and
  the flag can both go.
  - `ui/LogoutDialog.tsx:30` waits 100ms after closing and hopes the message input can take focus.
  - `ui/SettingsDialog.tsx:61` sets a global `skipRefocus` flag so the logout dialog it opens
    doesn't send focus to the message input.
- Define a `PaymentProvider` interface that both providers implement, so checkout and refunds no
  longer know which one they use.
  - `checkout/submit.ts:9` depends directly on `payments/stripe.ts`.
  - `refunds/refund.ts:22` wraps every Stripe call in `if (order.provider === "paypal")`.
```

Keep the log in a file under `${TMPDIR:-/tmp}`, never in the repository, to survive compaction.

## 4. Present ranked candidates

Each fix becomes a candidate:

- **Files**: the modules involved.
- **Problem**: the friction, citing the logged problems.
- **Solution**: what would change, in plain words.
- **Benefits**: in locality and leverage terms, and how the tests would change.
- **Before and after**: a small diagram or call sketch of the shallow shape and the deep one.
- **Dependency category**: from [references/deepening.md](references/deepening.md).
- **Strength**: `Strong`, `Worth exploring` or `Speculative`.
- **ADR conflict**: if it contradicts an ADR, name it and say why it is worth reopening.

Rank by strength, then by how many logged problems the fix resolves. End with the top
recommendation and why. Present the candidates in the conversation; write the HTML report from
[references/html-report.md](references/html-report.md) only if the user wants one. Don't propose
interfaces yet. Ask which candidate to explore.

## 5. Explore the chosen candidate

- Use `grilling` to settle the constraints, dependencies, the deepened module's shape, what sits
  behind the seam and which tests survive.
- Use `domain-modeling` as decisions settle: propose a glossary entry for a new module's concept
  or a sharper term for a fuzzy one, and if the user rejects the candidate for a reason a future
  review would need, offer an ADR so the candidate isn't suggested again.
- If the user wants alternative interfaces, use [references/design-it-twice.md](references/design-it-twice.md).

## When implementing a chosen fix

- Remove the special cases the redesign makes unnecessary; keep the ones the system still needs.
- Keep comments about constraints outside the code, such as a proxy that drops websockets. A
  workaround comment goes when the redesign removes its cause.
- Keep intended behavior unchanged; report independent bugs separately instead of changing
  behavior silently. Run the owning tests before and after.
- The fix is done when a typical change to that area lands in one module, and nothing in it imports
  something more volatile than itself.
