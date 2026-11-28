---
name: grilling
description: Grill the user relentlessly about a plan, decision or idea, one round of questions at a time, until every branch is settled. Use when the user wants to stress-test their thinking, says "grill me", or asks to be interviewed about a design.
license: MIT. LICENSE has complete terms
---

# Grilling

Run this interview when the user asks to be grilled or another skill invokes it; don't turn an
ordinary question into an interview. Interview the user until you share an understanding of the
plan. Map it as a **design tree**:
every decision branches into the decisions that depend on it.

Work in **rounds**. The **frontier** is every decision whose prerequisites are settled: the
questions you can ask now without guessing at answers you haven't heard. Ask the whole frontier in
one round, numbering questions continuously across rounds, and give your recommended answer. Word
each question so "yes" accepts the recommendation, and list the options when there are several.
Send the round and end your turn; do not add an outline or spec in the same message.

```
**Q1. <question title>**
<question body: context, and the options if there are several>

Recommended: <your answer>

---

**Q2. <question title>**
...
```

Recompute the frontier and ask the next round. A question that depends on another question still
open in this round belongs in a later round. If the user delegates a decision or asks to stop,
record the assumption and stop asking about that branch.

Facts are your job, never the user's. When a question needs a fact from the environment (files,
tools, history, docs), look it up rather than ask. If you can run subagents, hand the
lookup to one with a fresh context and keep going: only the questions that depend on it wait, and
you ask the rest of the frontier now. Decisions are the user's: put each one to them and wait.

The session is done when the frontier is empty: every branch visited, nothing silently assumed. Do
not act on the plan until the user confirms you share an understanding.

When the user wants decisions recorded as they settle, also use `domain-modeling`; recording them
is not acting on the plan.
