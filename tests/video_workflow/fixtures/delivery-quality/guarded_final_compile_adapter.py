from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import fitz


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
document = fitz.open()
for _ in range(plan["page_count"]):
    document.new_page()
document.save(pdf)
document.close()
pdf_sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
final_seal = {
    "schema_name": "final-artifact-seal",
    "schema_version": "1.0.0",
    "activation_status": "target_only",
    "sealed_at": request["compiled_at"],
    "precompile_text_seal_sha256": request["precompile_text_seal_sha256"],
    "generation_set_sha256": request["generation_set_sha256"],
    "compile_manifest_sha256": request["compile_manifest_sha256"],
    "compile_provider": request["compile_provider"],
    "final_pdf": {
        "path": "adapter-output/final.pdf",
        "sha256": pdf_sha256,
        "size": pdf.stat().st_size,
    },
}
final_seal["seal_sha256"] = fingerprint(final_seal)
write_json(output / "final-artifact-seal.json", final_seal)
pages = output / "rendered_pages"
pages.mkdir()
with fitz.open(pdf) as rendered_pdf:
    for page in range(1, plan["page_count"] + 1):
        rendered_pdf[page - 1].get_pixmap().save(pages / f"page_{page:03d}.png")
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
    {
        "text_origin_plan_sha256": plan["plan_sha256"],
        "final_artifact_seal_sha256": final_seal["seal_sha256"],
        "edges": plan["edges"],
    },
)
manifest = json.loads(Path(request["compile_manifest_path"]).read_text(encoding="utf-8"))
closure_inputs = [
    {
        "logical_id": item["logical_id"],
        "generation": item["generation"],
        "sha256": item["sha256"],
    }
    for item in manifest["entries"]
]
staging = output / "compiler-staging"
staging.mkdir()
for item in manifest["entries"]:
    destination = staging / item["staging_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item["source_path"], destination)
(staging / "main.aux").write_text("generated auxiliary\n", encoding="utf-8")
recorder = output / "compile-recorder.fls"
recorder.write_text(
    "".join(
        [
            *(f"INPUT {item['staging_path']}\n" for item in manifest["entries"]),
            *(
                f"INPUT {item['path']}\n"
                for item in manifest.get("approved_runtime_inputs", [])
            ),
            "INPUT main.aux\n",
        ]
    ),
    encoding="utf-8",
)
recorder_sha256 = hashlib.sha256(recorder.read_bytes()).hexdigest()
write_json(
    output / "compile-provenance.json",
    {
        "compile_manifest_sha256": request["compile_manifest_sha256"],
        "text_origin_plan_sha256": plan["plan_sha256"],
        "final_artifact_seal_sha256": final_seal["seal_sha256"],
        "invocation": {"recorder": True},
        "recorder_cwd": str(staging),
        "dependency_closure": {
            "complete": True,
            "inputs": closure_inputs,
            "runtime_inputs": manifest.get("approved_runtime_inputs", []),
            "recorder_sha256": recorder_sha256,
            "recorder_path": "compile-recorder.fls",
        },
    },
)
