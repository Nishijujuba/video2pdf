from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_bytes(value))


request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
plan = json.loads(Path(request["text_origin_plan_path"]).read_text(encoding="utf-8"))
output = Path(request["output_root"])
pdf = output / "final.pdf"
pdf.write_bytes(b"%PDF-1.4\n% guarded final compile fixture\n")
pdf_sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
pages = output / "rendered_pages"
pages.mkdir()
for page in range(1, plan["page_count"] + 1):
    (pages / f"page_{page:03d}.png").write_bytes(f"fixture-page-{page}".encode())
objects = plan["rendered_objects"]
for item in objects:
    item["text_sha256"] = hashlib.sha256(item["exact_utf8_text"].encode("utf-8")).hexdigest()
    item["object_sha256"] = fingerprint(item)
rendered = {
    "schema_name": "rendered-text-object-inventory",
    "schema_version": "1.0.0",
    "activation_status": "target_only",
    "final_pdf_sha256": pdf_sha256,
    "extractor_suite": plan["extractor_suite"],
    "coverage": {
        "page_count": plan["page_count"],
        "pages_scanned": list(range(1, plan["page_count"] + 1)),
        "content_streams_complete": True,
        "annotations_complete": True,
        "form_xobjects_complete": True,
        "declared_raster_text_complete": True,
    },
    "objects": objects,
}
rendered["inventory_sha256"] = fingerprint(rendered)
write_json(output / "rendered-text-object-inventory.json", rendered)
write_json(
    output / "text-origin-trace.json",
    {"text_origin_plan_sha256": plan["plan_sha256"], "edges": plan["edges"]},
)
write_json(
    output / "compile-provenance.json",
    {
        "compile_manifest_sha256": request["compile_manifest_sha256"],
        "text_origin_plan_sha256": plan["plan_sha256"],
        "dependency_closure": {"complete": True},
    },
)
