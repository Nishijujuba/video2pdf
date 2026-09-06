# Final Compile title and running-header validation impact

## Decision

Issues #118, #119, #120, and #122 change one generated-title derivation boundary,
one Final Compile generated-header evidence contract, and two compiler-source
completion rules. The migration applies to
fresh successor inventories and fresh named Final Compile attempts. Preserved
failed attempts remain immutable diagnostic evidence.

## Fixture dependency impact

| Issue | Authority input | Derived nodes | First validating gate | Focused coverage |
|---|---|---|---|---|
| #118 | Style declarations plus paired TeX environment invocations | Successor generated-text item, compiler-origin title edge | generated style title occurrence | bare plus override, all-override omission, unmatched boundary, ambiguous rendered occurrence |
| #119 | Final PDF bytes and PDF PageLabels | Running-header generator inputs and folio checks | running-header PDF label binding, then generated-origin completeness | explicit reset with continued numbering, implicit numbering, one folio contradiction |
| #120 | Unassigned rendered spans inside the existing header geometry | One running-header generated edge with preserved source records | running-header title authority | mixed Latin/CJK title and one wrong-title contradiction |
| #122 | Existing body compiler anchors plus authenticated TeX source | Completed compiler source records for centered wraps and box-title punctuation | compiler source completion | centered continuation, genuine box-end anchors, source ambiguity, title contradiction |

The negative cases start from their corresponding positive fixtures and mutate
only the named title or folio value. No source hash, TOC authority, page count,
geometry, or unrelated origin edge is made stale to reach the intended gate.

## Contract changes

`extract_tcolorbox_invocations` owns the supported invocation grammar and pairs
each begin with its end. A literal optional `title` suppresses the style default
for that invocation. Both boundaries remain eligible compiler-source locations
for a bare invocation. Unsupported or unmatched target-environment boundaries
fail closed.

`latex-running-header-v1.inputs` now contains `final_pdf_sha256` and one
`pdf_page_labels` entry per physical page. The adapter derives both values from
the compiled PDF. The Provider reopens that same PDF and rejects any supplied
binding that differs. When the PDF contains no explicit PageLabels dictionary,
the binding uses ordinary one-based PDF numbering.

All still-unassigned spans in the existing top-of-page header region enter the
same running-header generated edge. Existing compiler source records remain in
the edge's `object_sources`; they do not control visual-header membership.

Centered body continuations may start away from the body margin when the prior
visual line has one compiler-grounded identity and their joined rendered text
occurs on exactly that authenticated source line. A matched tcolorbox end
boundary may anchor missing title punctuation only when the rendered prefix
through that punctuation is uniquely present in the paired invocation's title
line or equals its literal title override. The completed record retains the
compiler-grounded end line as its source evidence.

## Deferred qualification

Focused contract tests establish the new derivation and validation behavior.
Actual retained-run compilation, rendered-text reconciliation, Acceptance v2,
individual page inspection, and Delivery Guard remain separate governing
qualifications and are outside these unit-test results.
