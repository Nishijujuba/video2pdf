# Delivery Glossary v1

The Delivery Glossary is a contract artifact for non-English teaching PDFs. It records which English expressions carry explanatory work, the Chinese primary term that should carry the reader-facing explanation, and the body-text strategy that later consistency and acceptance review must apply.

The Delivery Glossary is not a default PDF appendix. It is reviewer-readable and machine-checkable workflow evidence. A reader-facing appendix, concept index, or body glossary appears only when the user or task explicitly requests one.

## Default Path

Per-video workflows should write the glossary at:

```text
review/acceptance/delivery_glossary.json
```

Issue 01 validates the glossary as a standalone file through:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\validate_delivery_glossary.py review\acceptance\delivery_glossary.json
```

Manifest and Acceptance Report integration is owned by the next issue.

## Required Contract

Top-level fields:

- `schema_version`: must be `delivery_glossary.v1`.
- `language_profile`: must be `non_english_teaching_pdf`.
- `default_reader_mode`: describes the reader mode, normally `standalone_readable_video_learning_note`.
- `terms`: non-empty list of core English expressions that carry explanatory work.

Each term requires:

- `english`
- `chinese_primary`
- `plain_language_boundary`
- `related_terms`
- `opposed_terms`
- `first_use_expected_location`
- `body_display_strategy`
- `where_to_preserve_english`
- `required_after_first_use`

Allowed `body_display_strategy` values:

- `preserve_english`
- `chinese_with_english_parenthetical`
- `chinese_primary_only`
- `quote_only`

Allowed `where_to_preserve_english` values:

- `body_parenthetical`
- `body_after_definition`
- `footnote`
- `caption`
- `quote_only`
- `delivery_glossary_only`
- `none`

`forbidden_body_forms` is optional in v1. It is an extension field for high-risk terms and is not required for a valid glossary.

## Product Names

Product names, company names, person names, code identifiers, commands, and file extensions stay out of the Delivery Glossary unless they define a new core concept. For example, `Cursor` should stay excluded when it is only a product name, while `capability overhang` belongs in the glossary when it acts as a technical concept label.
