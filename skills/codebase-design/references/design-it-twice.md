# Design it twice

Produce three or four radically different interfaces for one module, then compare them.

## 1. Frame the problem

Write a short explanation for the user:

- the constraints any new interface must satisfy;
- the dependencies it relies on, with their category from
  [references/deepening.md](references/deepening.md);
- a rough code sketch that makes the constraints concrete. It is not a proposal.

Show the frame and wait for the user to confirm or correct it before producing the designs.

## 2. Produce the designs

Write one technical brief per design: file paths, coupling details, dependency categories, what
sits behind the seam, this skill's vocabulary and the project's glossary terms. Give each brief
a different constraint:

1. Minimize the interface: one to three entry points, maximum leverage per entry point.
2. Maximize flexibility: many use cases and extension.
3. Optimize for the most common caller: make the default case trivial.
4. If dependencies cross a network: design around seams and injected adapters.

If you can run subagents, give each brief to its own subagent with a fresh context, in
parallel. Otherwise write the designs yourself one at a time, each from only its own brief, and
finish all of them before comparing.

Each design returns:

1. The interface: types, methods, parameters, invariants, ordering and error modes.
2. A usage example from a real caller.
3. What the implementation hides behind the seam.
4. Its dependency strategy and adapters.
5. Trade-offs: where leverage is high and where it is thin.

## 3. Compare and recommend

Present the designs one at a time, then compare them by depth, locality and seam placement.
Recommend one, and say why. If parts of two designs combine well, propose the hybrid.
