# Deepening

How to merge a cluster of shallow modules into one deep module, given its dependencies.

## Classify each dependency

The category decides how the deepened module is tested across its seam.

| Category | Examples | How to deepen and test |
| --- | --- | --- |
| In-process | Pure computation, in-memory state | Merge the modules and test through the new interface. No adapter. |
| Local substitute | PGlite for Postgres, an in-memory filesystem | Deepen if the stand-in exists, and run it in the test suite. The seam stays internal; no extra adapter at the external interface. |
| Remote but owned | Your own services behind HTTP, gRPC or a queue | Define an interface at the seam. The deep module owns the logic; inject the network client as an adapter. Tests use an in-memory adapter; production uses the network adapter. |
| True external | Payment, email or SMS providers you don't control | Take the dependency through an injected interface; tests supply a mock adapter. |

## Replace tests; don't layer them

- Write new tests at the deepened module's interface. Assert observable outcomes, not internal
  state.
- When test cleanup is in scope, use `test-audit` to confirm each replaced shallow-module test is
  covered by a remaining one, then delete it.
- A test that must change when only the implementation changes is testing past the interface.
