# Use delivery glossary for non-English PDF terminology strategy

Non-English teaching PDFs produced from video sources should be readable as standalone Chinese articles while still preserving enough source alignment for review and learning. The workflow will therefore treat the default deliverable as a standalone-readable video learning note: body text prioritizes natural Chinese comprehension, while source-language terms are preserved through controlled locations such as a delivery glossary, footnotes, captions, or parenthetical first-use forms.

For non-English-teaching videos, the workflow will introduce a delivery glossary artifact, normally `review/acceptance/delivery_glossary.json`, and list it in `review/acceptance/allowed_artifacts_manifest.json`. This glossary is a final-delivery contract artifact and an allowed Acceptance Reviewer input. It is not shown as a PDF appendix by default; it becomes visible to readers only when the user or task explicitly requests a body appendix, concept index, or glossary section.

The delivery glossary includes only core English expressions that carry explanatory work. A term enters the glossary when it appears in a title, guide, chapter opening, paragraph conclusion, caption, table heading, method name, framework, category, or source-video concept label and the reader would lose the main argument without understanding it. Product names, company names, people names, code identifiers, commands, file extensions, and familiar technical abbreviations do not enter the glossary unless they are used to define a new core concept.

Each glossary term must record its Chinese primary name, English source expression, plain-language boundary, related or opposed concepts where useful, expected first-use location, `body_display_strategy`, and `where_to_preserve_english`. The initial strategy is owned by the Outline agent. Writer agents may propose `new_term_candidates` in their chapter handoff, including a proposed Chinese primary name and display strategy, but they do not directly change an existing global strategy. The coordinator merges accepted candidates into the delivery glossary before consistency and acceptance review.

The first strategy set is:

- `preserve_english`: use for product names, code names, APIs, file formats, and identifiers that should remain English in body text.
- `chinese_with_english_parenthetical`: use for fixed technical terms, field terms, or source-video method names where the English label remains useful to identify the concept.
- `chinese_primary_only`: use for ordinary English words that the speaker temporarily turns into a concept and where the natural Chinese term should carry the body text.
- `quote_only`: use when the English expression should appear only inside source quotes.

The first source-preservation locations are:

- `body_parenthetical`
- `body_after_definition`
- `footnote`
- `caption`
- `quote_only`
- `delivery_glossary_only`
- `none`

For example, `capability overhang` can use `chinese_with_english_parenthetical` because it is a technical concept label. `grief`, when used to describe the emotional cost of a programming-tool transition, should normally use `chinese_primary_only` with `where_to_preserve_english: delivery_glossary_only`; body text should say a bounded Chinese phrase such as "本节讨论的'失落感'，指的是一种更窄、更工程化的心理断裂" rather than making `grief` the Chinese sentence subject.

The workflow will not require a forbidden-form list in the first implementation. A future optional field such as `forbidden_body_forms` may be added for high-risk terms when the allowed strategy is insufficiently precise. For example, `grief` could later forbid body forms such as bare `grief`, `哀悼感（grief）`, or `悲伤（grief）` if repeated review failures show that the strategy fields alone do not block awkward wording. This field remains an extension point, not a v1 requirement.

Acceptance review should check the final PDF against the delivery glossary. A pass requires the final body text to follow each term's `body_display_strategy` and `where_to_preserve_english`, not merely to mention or explain the term somewhere. This prevents both over-preserving source English in body text and losing source alignment where English is semantically important.

The rejected alternatives are a blanket "Chinese term plus English parenthetical" rule for all core expressions, a body appendix by default, no glossary artifact, and a mandatory forbidden-form list in v1. The blanket parenthetical rule makes temporary source words such as `grief` feel like awkward machine translation. A default appendix exposes workflow contract fields to readers who only need a polished article. No glossary artifact leaves the Acceptance Reviewer without a stable contract for deciding whether English preservation or Chinese localization is correct. A mandatory forbidden-form list would increase authoring burden before there is evidence that strategy fields cannot enforce the needed distinction.
