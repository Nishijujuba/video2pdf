from __future__ import annotations
from datetime import datetime, timezone
import hashlib, json, subprocess, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for item in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(item) not in sys.path: sys.path.insert(0, str(item))
from slice6_exit_evidence_contract import *
from video2pdf_workflow_kernel.evidence import EvidenceSupportError, fingerprint_implementation_changes, git_output, sha256_file
EVIDENCE_DIR=PROJECT_ROOT/"evidence/slice-06"; LOG_DIR=EVIDENCE_DIR/"logs"; MANIFEST_PATH=EVIDENCE_DIR/"exit-evidence-manifest.json"
def git(*args: str) -> str:
    try: return git_output(PROJECT_ROOT,*args)
    except EvidenceSupportError as exc: raise RuntimeError(str(exc)) from exc
def relative(path: Path) -> str: return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
def run_commands(commit: str):
    LOG_DIR.mkdir(parents=True,exist_ok=True); evidence=[]
    for test_id,command in COMMANDS:
        completed=subprocess.run(command,cwd=PROJECT_ROOT,capture_output=True,check=False)
        raw=completed.stdout.replace(b"\r\n",b"\n").replace(b"\r",b"\n")+completed.stderr.replace(b"\r\n",b"\n").replace(b"\r",b"\n")+f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {commit}\n".encode("ascii")
        path=LOG_DIR/f"{test_id}.log"; path.write_bytes(raw)
        evidence.append({"test_id":test_id,"command":list(command),"expected_exit_code":0,"actual_exit_code":completed.returncode,"log":{"role":"command_log","path":relative(path),"sha256":hashlib.sha256(raw).hexdigest()},"conforms":completed.returncode==0})
    return evidence
def main() -> int:
    if git("status","--porcelain=v1","--untracked-files=all"):
        print("ERROR: Slice 6 evidence collection requires a clean implementation HEAD",file=sys.stderr); return 2
    commit=git("rev-parse","HEAD"); git("merge-base","--is-ancestor",SLICE_BASE_COMMIT,commit); commands=run_commands(commit)
    manifest={"$schema":"https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json","schema_version":2,"kind":"video-workflow-exit-evidence","fingerprint_algorithm":"sha256-raw-v1","slice":{"number":SLICE_NUMBER,"name":SLICE_NAME},"slice_base_commit":SLICE_BASE_COMMIT,"implementation_commit":commit,"evidence_paths":[relative(MANIFEST_PATH),*[item["log"]["path"] for item in commands]],"generated_at":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"activation_scope":{"kind":"none","runtime_authority_change":False,"components_activated":[],"legacy_track_authority":"preserved"},"commands":commands,"expected_checkpoints":EXPECTED_CHECKPOINTS,"fixtures":[{"role":role,"path":path,"sha256":sha256_file(PROJECT_ROOT/path)} for role,path in FIXTURE_SPECS],"results":RESULTS,"result_bindings":RESULT_BINDINGS,"artifact_fingerprints":fingerprint_implementation_changes(PROJECT_ROOT,SLICE_BASE_COMMIT,commit,excluded_prefixes=(EVIDENCE_PREFIX,)),"unresolved_exceptions":[],"overall_decision":"pass" if all(item["conforms"] for item in commands) else "fail"}
    MANIFEST_PATH.parent.mkdir(parents=True,exist_ok=True); MANIFEST_PATH.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(MANIFEST_PATH); return 0 if manifest["overall_decision"]=="pass" else 1
if __name__=="__main__": raise SystemExit(main())
