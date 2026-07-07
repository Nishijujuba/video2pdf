# PRD: Delivery Glossary Terminology Governance

## Problem Statement

The video-to-PDF workflow produces Chinese teaching PDFs from source videos that often contain English product names, engineering terms, method names, and ordinary English words that the speaker temporarily turns into concepts. The current workflow has general style and readability criteria, but it does not give the writer, consistency reviewer, and Acceptance Reviewer a shared contract for deciding when English should remain visible in body text and when the Chinese term should carry the explanation.

This gap creates awkward results in non-English teaching PDFs. A term such as `grief` may be preserved in body text because it came from the source video, even though a standalone-readable Chinese note would be clearer if the body used "失落感" and preserved the English source word only in a delivery contract. The same workflow also needs to preserve genuinely useful English terms such as `capability overhang`, `backoff semantics`, or `HTML mockup` when the English expression is a technical label, source-video method, or necessary alignment point.

The user needs a terminology governance layer that preserves source alignment without making the PDF read like machine translation. The default deliverable should be a standalone-readable video learning note: Chinese body text carries the argument, while source-language terms remain available through controlled preservation locations.

## Solution

Add a Delivery Glossary contract for non-English teaching PDFs. The Delivery Glossary records the core English expressions that carry explanatory work, their Chinese primary names, and the body-text strategy that the final PDF must follow. The glossary becomes part of the final delivery artifact set and is included in the allowed artifacts manifest so the Acceptance Reviewer may use it.

The Delivery Glossary is not a reader-facing appendix by default. It is a machine-checkable and reviewer-readable contract. A PDF body appendix or visible concept index is generated only when the user or task explicitly asks for one.

The workflow changes in five places.

First, the Outline agent creates the initial global terminology contract. Every core English expression that will carry explanatory work must get a Chinese primary name, a boundary explanation, a body display strategy, and a source-preservation location.

Second, Writer agents follow the global terminology contract when writing `section_*.tex`. If a Writer agent discovers a new core English expression, it reports the expression in its handoff as `new_term_candidates`; it does not directly change an existing global term strategy.

Third, the coordinator merges accepted new term candidates into the Delivery Glossary before consistency review and final acceptance. Rejected candidates remain outside the glossary and should not become body-text concepts.

Fourth, the Consistency agent checks that the TeX files follow the Delivery Glossary: first-use wording, later-use stability, source-English preservation location, and chapter-to-chapter terminology consistency.

Fifth, the Acceptance Reviewer reads the Delivery Glossary through the allowed artifacts manifest and checks that the final PDF body follows each term's declared `body_display_strategy` and `where_to_preserve_english`. Passing acceptance requires matching the strategy, not merely explaining the term somewhere.

### Concrete Artifact Paths

The project-level design record is:

- `docs/adr/0005-use-delivery-glossary-for-non-english-pdf-terms.md`

The default per-video delivery glossary is:

- `<video-output-dir>/review/acceptance/delivery_glossary.json`

The existing acceptance manifest must include the glossary:

- `<video-output-dir>/review/acceptance/allowed_artifacts_manifest.json`

The existing acceptance report remains the only machine-readable final delivery decision source:

- `<video-output-dir>/review/acceptance/acceptance_report.json`

The default acceptance criteria file should be extended to require glossary-aware review for non-English teaching PDFs:

- `docs/acceptance/acceptance_criteria.v1.json`

The rendering workflows that need to create and preserve the glossary are:

- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `AGENTS.md`

The final-delivery acceptance skill and validators need to allow the glossary as a final artifact and validate reports that claim glossary-aware review:

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_criteria.py`

### Minimum Delivery Glossary JSON Contract

The first version of the glossary should use a small explicit schema:

```json
{
  "schema_version": "delivery_glossary.v1",
  "language_profile": "non_english_teaching_pdf",
  "default_reader_mode": "standalone_readable_video_learning_note",
  "terms": [
    {
      "english": "grief",
      "chinese_primary": "失落感",
      "plain_language_boundary": "指旧工作方式、旧技能价值和旧身份感被重新解释时产生的心理断裂，不是泛泛的情绪低落。",
      "related_terms": ["gain", "sense of loss", "craft identity"],
      "opposed_terms": ["relief", "increased agency"],
      "first_use_expected_location": "section_04.tex",
      "body_display_strategy": "chinese_primary_only",
      "where_to_preserve_english": "delivery_glossary_only",
      "required_after_first_use": "后文优先使用“失落感”。"
    }
  ]
}
```

The initial `body_display_strategy` enum is:

- `preserve_english`: for product names, code names, APIs, file formats, and identifiers that should remain English in body text.
- `chinese_with_english_parenthetical`: for fixed technical terms, field terms, or source-video method names where the English label remains useful in the body.
- `chinese_primary_only`: for ordinary English words that the speaker temporarily turns into a concept and where natural Chinese should carry the body text.
- `quote_only`: for English expressions that should appear only inside source quotes.

The initial `where_to_preserve_english` enum is:

- `body_parenthetical`
- `body_after_definition`
- `footnote`
- `caption`
- `quote_only`
- `delivery_glossary_only`
- `none`

The first implementation will not require `forbidden_body_forms`. The field remains a future optional extension for high-risk terms when the strategy fields prove insufficient. For example, `grief` could later forbid bare `grief`, `哀悼感（grief）`, or `悲伤（grief）`, but v1 should not require every term to maintain a forbidden list.

### Core-Term Inclusion Rule

An English expression enters the Delivery Glossary only when it carries explanatory work. It should enter when at least one condition is true:

1. It appears in a title, guide, chapter opening, paragraph conclusion, figure caption, table heading, method name, framework, category, or source-video concept label and carries the explanation.
2. It is a source-video method, framework, or recurring concept label.
3. A Chinese reader may not know it, and missing the explanation would break the main argument.
4. It is an ordinary English word temporarily elevated into a document concept, such as `grief`.
5. A Writer agent plans to use it as a chapter driver, method name, judgment standard, or comparison axis.

It should stay out of the glossary when it is only a product name, company name, person name, code identifier, command, parameter, file extension, one-off quote, or familiar technical abbreviation that does not define a new concept.

`HTML mockup` illustrates the boundary. If it only means "an HTML file", it does not need a glossary entry. If it means a method for giving a model a reference artifact that communicates layout and interaction intent, it should enter the glossary.

## User Stories

1. As a Chinese PDF reader, I want ordinary English words that are temporarily concept-labeled in the video to become natural Chinese body text, so that the PDF reads like a finished article.
2. As a Chinese PDF reader, I want technical English terms to keep useful source labels when needed, so that I can map the note back to the original video.
3. As a Chinese PDF reader, I want the first use of a core term to explain its boundary in plain language, so that I do not need external English knowledge to follow the argument.
4. As a video-learning reader, I want source terms to remain recoverable through glossary, footnotes, captions, or quotes, so that I can align the PDF with the video when reviewing.
5. As a workflow owner, I want one global terminology contract per video output, so that separate Writer agents do not translate the same concept in incompatible ways.
6. As an Outline agent, I want to decide body display strategy for core terms before chapter writing starts, so that writers have a stable terminology contract.
7. As a Writer agent, I want to report `new_term_candidates` in my handoff, so that I can flag newly discovered terminology without mutating the global strategy myself.
8. As a Writer agent, I want clear examples for terms such as `grief`, `capability overhang`, `backoff semantics`, and `HTML mockup`, so that I can choose the right display style.
9. As a coordinator, I want to merge or reject new term candidates before consistency review, so that the final glossary remains authoritative.
10. As a Consistency agent, I want the Delivery Glossary to define first-use and later-use expectations, so that terminology drift can be reported concretely.
11. As an Acceptance Reviewer, I want the glossary listed in the allowed artifacts manifest, so that I can use it without violating the read-only acceptance boundary.
12. As an Acceptance Reviewer, I want each term to declare `body_display_strategy`, so that I can decide whether an English term should appear in the body.
13. As an Acceptance Reviewer, I want each term to declare `where_to_preserve_english`, so that I can distinguish deliberate Chinese-only body text from accidental loss of source alignment.
14. As an Acceptance Reviewer, I want glossary-aware criteria to be delivery-blocking, so that awkward source-English preservation cannot slip through as a style preference.
15. As a workflow maintainer, I want `forbidden_body_forms` deferred as an optional extension, so that v1 remains lightweight while preserving an upgrade path.
16. As a workflow maintainer, I want the glossary schema to be minimal and explicit, so that validation failures are easy to diagnose.
17. As a workflow maintainer, I want non-English teaching PDFs separated from English-learning PDFs, so that IELTS or vocabulary notes can still preserve extensive English source text.
18. As a workflow maintainer, I want the Delivery Glossary omitted from the PDF appendix by default, so that readers see a polished note rather than workflow contract fields.
19. As a workflow maintainer, I want a visible body glossary only when explicitly requested, so that artifact contracts and reader-facing teaching aids stay separate.
20. As a future implementer, I want concrete artifact paths and enum values in the PRD, so that the feature can be split into small issues without re-litigating terminology decisions.

## Implementation Decisions

- The feature applies to non-English teaching PDFs. English-teaching, IELTS, TOEFL, pronunciation, grammar, vocabulary, and similar language-learning videos keep their existing English-primary treatment.
- The default reader mode is `standalone_readable_video_learning_note`: the PDF should be readable as an independent Chinese article while preserving source alignment through controlled artifacts and source notes.
- The Delivery Glossary is a final-delivery contract artifact and allowed Acceptance Reviewer input.
- The Delivery Glossary is not a default PDF appendix. Reader-facing glossary output is opt-in.
- The Outline agent owns the initial global glossary and strategy decisions.
- Writer agents may report `new_term_candidates` in handoff notes. They do not independently change existing global term strategies.
- The coordinator merges accepted candidates into the Delivery Glossary before consistency and final acceptance.
- The Consistency agent checks TeX against the glossary before final acceptance.
- The Acceptance Reviewer checks final artifacts against the glossary and criteria. The reviewer must evaluate actual body display behavior, not just whether a term was explained somewhere.
- `forbidden_body_forms` is recorded as a future optional field, not a v1 requirement.
- The first implementation should prefer a single glossary schema and one manifest inclusion path over multiple chapter-specific glossary files.
- The acceptance report remains the machine-readable delivery decision source. The glossary informs acceptance; it does not replace the report.

## Testing Decisions

- Validate the Delivery Glossary schema with focused fixture tests: valid minimal glossary, missing required fields, invalid enum value, invalid language profile, empty terms when glossary is required, and extra future fields if the schema allows extension.
- Test manifest generation so `delivery_glossary.json` is included as an allowed final artifact for non-English teaching PDFs and omitted or optional for English-learning profiles.
- Test acceptance report validation with a passing glossary-aware report and failures for reports that claim glossary-aware review without including the glossary in context.
- Test glossary-aware criteria with representative terms:
  - `grief`: `chinese_primary_only` plus `delivery_glossary_only`.
  - `capability overhang`: `chinese_with_english_parenthetical` plus `body_parenthetical`.
  - `HTML mockup`: included only when used as a method concept.
  - Product names: excluded unless they define a new concept.
- Test that a PDF body phrase equivalent to "本节讨论的 grief 是" fails when the glossary strategy requires `chinese_primary_only`.
- Test that "本节讨论的'失落感'，指的是..." passes for `grief`.
- Test that a visible PDF appendix is not required unless the task explicitly requests one.
- Reuse the existing acceptance validator and final-delivery manifest seams where possible. The highest useful seam is the final acceptance contract: criteria, manifest, glossary, final artifacts, and acceptance report validation.

## Out of Scope

- Implementing `forbidden_body_forms` as a required v1 field.
- Generating a reader-facing PDF glossary appendix by default.
- Changing English-learning or IELTS PDF behavior.
- Rewriting already delivered PDFs unless the user requests a repair pass.
- Replacing the Acceptance Report as the final machine-readable decision source.
- Creating per-section glossary JSON files for every `section_*.tex`.
- Building a general translation dictionary or universal English glossary unrelated to a specific video output.

## Further Notes

The motivating example is `grief` in the Fable video PDF. The source video uses "dealing with the grief", but the best standalone Chinese body text is closer to:

> 因此，本节讨论的“失落感”，指的是一种更窄、更工程化的心理断裂：当旧约束不再稳定地定义工作方式时，程序员如何重新理解自己积累过的判断、失败和手艺。

This example shows why the feature should use explicit display strategies rather than a blanket "Chinese primary plus English parenthetical" rule. The goal is not to remove English from technical notes. The goal is to make English preservation intentional and checkable.
