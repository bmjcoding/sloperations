---
name: unslop
description: Cut AI tells and dead weight from prose, and order it so each concept is introduced before it is used. Use when the user asks to unslop, de-AI, tighten or copyedit writing such as docs, READMEs, PR descriptions, comments, posts or skill files. Not for deciding whether a comment or doc is still true.
license: MIT. LICENSE has complete terms
---

# Unslop

Scan the text for the patterns below, then rewrite. Preserve meaning, facts and tone; add no
claims. Where a rule asks for a number or mechanism the text lacks, cut the sentence or flag it.
After rewriting, verify no fact, name, number, date or quote changed, and restore any that did.
Leave code, commands, identifiers, quotations, tables, mandated templates and defined project
terms as they are; these rules govern prose sentences. A user-supplied style sample overrides
style rules. Rule numbers are fixed IDs; gaps are intentional.

- **3.** No trailing "-ing" phrases that add no fact ("highlighting...", "ensuring...",
  "reflecting...").
- **5.** No vague attributions ("experts believe", "industry reports suggest"). Name the source or
  delete.
- **7.** No AI vocabulary: additionally, crucial, delve, enduring, enhance, foster, garner,
  interplay, intricate, landscape, pivotal, showcase, tapestry, testament, underscore, vibrant.
- **8.** Say "is" or "has", not "serves as", "stands as", "boasts", "features".
- **9.** No "not just X, but Y". State the point.
- **10.** Don't force ideas into threes. Use the natural number.
- **11.** One name per thing. Repeat it.
- **12.** No "from X to Y" unless X and Y sit on a real scale.
- **13.** No em dashes, en dashes, or hyphens used as dashes, and no parentheses in their place. Use
  a period or a comma.
- **14.** A colon goes before a list or an example, never mid-sentence as a connector.
- **15.** Don't bold every proper noun or acronym.
- **16.** No bold label and colon that restates its line ("**Performance:** Performance
  improved..."). A bold lead-in that ends in a period and adds new detail is fine.
- **17.** Sentence case headings.
- **18.** No decorative emojis.
- **19.** Straight quotes.
- **20.** No chatbot phrases ("I hope this helps!", "Let me know if...", "Certainly!").
- **22.** No praise or agreement openers. Respond directly.
- **23.** Cut filler. "In order to" becomes "to", "due to the fact that" becomes "because". Delete
  "it is important to note that".
- **24.** At most one hedge per claim ("may").
- **25.** No generic conclusions. State specific plans or facts.
- **26.** No abstract metaphor nouns where a plain word exists: substrate ("base"), wedge in
  ("add"), vector ("way"), endgame ("the last phase"), gold-plating ("more than the job needs"),
  locus, vantage, nexus, bedrock, modality, paradigm, north star, flywheel, and primitive, harness,
  surface, scaffolding or ratchet used as metaphors. Keep literal technical terms such as test
  harness or type primitive.
- **27.** Say what it does, not how it feels. Name the mechanism or the number: not "fast startup"
  but "starts in 40ms with zero network calls". Cut a sentence that could appear unchanged in
  another project's docs.
- **28.** One idea per sentence. Split a sentence the reader has to reread.
- **29.** Active voice. Name the actor unless it is unknown or doesn't matter: "the loader parses
  the file", not "the file is parsed".
- **30.** Cut adverbs or use a stronger verb. Give the number.
- **31.** Use the plain word: "use", not "utilize" or "leverage"; "help", not "facilitate"; "if",
  not "in the event that".
- **32.** No metaphor, aphorism, personified code, or figurative verb where a literal phrase exists.
- **33.** Write whole sentences with their articles and verbs. Spell out arrows and abbreviations.
- **34.** Introduce a concept before any sentence that depends on it, unless the intended reader
  arrives knowing it. Reorder the text, or define the term where it first appears.
- **35.** Cut any passage, paragraph or section whose removal costs the reader nothing. Test it by
  reading the text without it.
