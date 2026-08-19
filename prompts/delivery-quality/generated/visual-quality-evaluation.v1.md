# visual-quality-reviewer

Catalog: video2pdf.delivery-quality@1.0.0
Projection: visual-quality-evaluation@1.0.0
Projection kind: evaluation

## Immutable rules

- `figure_visual_integrity`: Every figure is legible, unclipped, non-overlapping, correctly associated with its caption, and visually supports the nearby explanation.
- `table_layout_integrity`: Every table preserves readable cells, headers, alignment, page boundaries, and association with its explanatory context.
- `credibility_disclosure_rendered_placement`: Each required credibility disclosure is visible at a rendered location where the reader encounters it before relying on the affected claim and where it does not disrupt reading flow.

The projection grants semantic decision authority only when its kind is evaluation. Unknown findings are Contract Gaps.
