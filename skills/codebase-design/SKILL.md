---
name: codebase-design
description: Design deep modules and clean seams, enforce import rules, make concurrent or crash-safe state writes converge, or run a requested architecture review. Use when designing a module interface, placing a seam, untangling code that changes together, adding an import rule, or when the user explicitly asks for an architecture review.
license: MIT. LICENSE has complete terms
---

# Codebase design

Design **deep modules**: a lot of behavior behind a small interface, placed at a seam, tested
through that interface.
Loading this skill for vocabulary or interface advice does not start an architecture review or a
multi-design exercise; run those only when the user asks.

## Vocabulary

Use these terms in code review, plans and reports; project glossary terms take precedence when
naming domain concepts.

- **Module**: anything with an interface and an implementation, at any scale: a function, class,
  package or a slice across tiers. Not "unit", "component" or "service".
- **Interface**: everything a caller must know to use the module correctly: types, invariants,
  ordering constraints, error modes, required configuration and performance. Not "API" or
  "signature", which name only the type-level part.
- **Implementation**: the code inside a module.
- **Depth**: how much behavior a caller or test can exercise per unit of interface it has to
  learn. A module is **deep** when a small interface hides a lot of behavior, **shallow** when the
  interface is nearly as complex as the implementation. Depth is not a ratio of line counts.
- **Seam**: the place where a module's interface lives, where behavior can change without editing
  that place. Where the seam goes is its own decision. Not "boundary", which collides with a
  bounded context.
- **Adapter**: a concrete thing that satisfies an interface at a seam. It names a role, not a size.
- **Leverage**: what callers get from depth: one implementation pays back at every caller and test.
- **Locality**: what maintainers get from depth: change, bugs and verification stay in one module.

## Rules

- **Deletion test.** Imagine deleting the module. If its complexity vanishes, it was a
  pass-through. If the complexity reappears across its callers, it earns its keep.
- **Test through the interface.** Callers and tests cross the same seam. A test that needs to reach
  past the interface means the module has the wrong shape.
- **One adapter is a hypothetical seam; two adapters make a real one.** Add a seam only where
  something varies across it, usually production and test.
- **Keep internal seams internal.** A deep module may have private seams its own tests use. Don't
  expose them through the interface.
- **Accept dependencies; don't construct them.** Pass the payment gateway in rather than calling
  `new StripeGateway()` inside.
- **Return results rather than mutating inputs** where the caller can apply them.
- **Fewer entry points, fewer parameters.** Every one adds tests and setup.
- **Name modules from the domain.** Use the terms in the project's glossary (`CONTEXT.md`, or the
  existing glossary file `domain-modeling` uses): "the Order intake module", not "the OrderHandler
  service".

## Shared state

- **Separate writers by fact.** When concurrent actors might write the same file, key, branch or
  record, first ask whether they publish independent facts. If they do, give each actor its own
  target and merge where the state is read. Two workers writing their own field into one
  `state.json` still share a write. Serialize with a lock, a single writer, sequential phases or
  compare-and-swap only when one shared target is a real invariant. An instruction to agents is not
  concurrency control.
- **Converge after interruption.** Derive each step of a state-changing operation from the state
  that exists, not from what an earlier run meant to do: read it, compare it with the goal, apply
  the difference. Anything a run leaves behind (a lock, temporary file, half-written record,
  claimed job) must be recognizable as stale from evidence that holds wherever that run stopped.
  Write to a temporary file in the destination directory and rename it into place. Flush the file
  and directory when power-loss durability is required. For remote effects, use a stable
  idempotency key or query remote state before retrying.
- **Test with an interruption matrix.** List every point where the operation can stop: before and
  after each write, each external call and each rename. For each point, leave the state as a crash
  there would, run the operation again, and check that it reaches the same end state as a clean
  run. Also run it twice in a row. A row that depends on what was left behind needs a
  reconciliation step.

## Enforcing a seam

When a seam is enforced by an import rule (dependency-cruiser, ESLint import rules, import-linter
or the stack's equivalent), the rule must be shown to catch something. A portable rule family:
code outside a package imports only files at the package root, and subfolders are private so
adding a folder needs no config change. Tests import through the root files too. Forbid dependency
cycles, and prefer several small entry files over one barrel that re-exports a subtree.

1. Run the check on the clean tree. It passes.
2. Plant one violation in a new throwaway file inside the scan roots (never edit an existing file
   for this check), such as importing another package's subfolder internals. Run the check. It
   fails, naming the expected rule.
3. Delete the throwaway file. The check passes again.

If step 2 passes, the rule is not wired: check that the scan roots include every importer, then
fix the config. Repeat steps 2 and 3 for each rule you add. Wire the check into the repository's
existing aggregate check command if it has one, and add a one-line pointer in whichever agent
instructions file the repository already uses; create neither for this step.

## References

- [references/deepening.md](references/deepening.md): read before deepening a cluster of modules.
  It classifies dependencies and says how each kind is tested across the seam.
- [references/design-it-twice.md](references/design-it-twice.md): read only when the user wants
  alternative interfaces for a module.
- [references/architecture-review.md](references/architecture-review.md): run only when the user
  asks for an architecture review, a deepening scan or an untangling pass.
- [references/html-report.md](references/html-report.md): read only when the user wants the
  review's candidates as an HTML report.
