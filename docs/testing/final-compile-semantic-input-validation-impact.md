# Final Compile and semantic-input repair validation impact

Issues #105, #112, and #113 correct three boundaries observed while resuming the retained qualification and the original video Run: resolving producer paths into a Final Compile Manifest, preserving a requested Final PDF basename through governed compilation, and admitting a real semantic-input correction without changing compiled reader text.

The user has excluded the obsolete historical and full test collections. This change is verified through explicitly named new test methods and the affected real public workflow routes. Imported fixture helpers do not authorize collecting their historical test methods.

## Affected fixture and authority graph

| Boundary | Positive graph | Negative graph and precedence | Downstream materialization |
| --- | --- | --- | --- |
| #105 runtime handoff | The current diagnostic Compile Manifest contains Run-relative producer sources. The successor Final Compile Manifest resolves each source against that Run. | Existing generation, diagnostic, Seal, runtime-policy, and handoff checks remain in force. A manifest source still identifies the exact declared producer bytes. | Logical IDs, generations, digests, staging paths, and approved runtime inputs are retained; the Final Compile source path becomes absolute. |
| #112 named Final PDF | A normalized Unicode basename enters the public provider and registered adapter, then appears in the operation, request, Final Artifact Seal, Compile Report, and returned artifact paths. | `final_compile_pdf_basename` / `final_compile_pdf_basename_invalid` rejects invalid input before adapter execution. A changed name cannot replay an operation that names another PDF. Existing exact Seal-path checks still reject an unsealed copy. | Both input tracks use the shared provider/adapter. Fresh compilation produces the named PDF and its matching provenance; completed replay returns the same governed artifact identities. |
| #113 semantic-input repair | A corrected governed Glossary/source projection changes semantic review inputs while compile Artifact Generations and reader-text identity stay current. | `precompile_repair_input_advance` / `precompile_repair_evaluation_inputs_unchanged` rejects a new repair with no meaningful input advance before successor publication. Existing failure authority, receipt, successor, and attempt-budget checks remain in force. | The provider derives current semantic dependencies and publishes fresh Reviewer Skeletons. Old Judgment Patches cannot approve the successor's semantic inputs. |

The #105 focused fixture isolates the handoff boundary using retained fixture builders and controlled Seal/diagnostic authority responses. It materializes actual source files so source-path assertions test resolvable identities. It does not replace the retained Run's real Seal, runtime recovery, or Final Compile qualification.

The #112 fixture exercises the actual registered adapter using controlled compile inputs. The working-tree test substitutes only the expected Git blob identity while the implementation is being edited. Formal Run qualification uses the committed adapter identity without that substitution.

No historical manifest, report, journal, or Judgment Patch is rewritten. A previously published malformed handoff manifest stays retained; an explicitly supplied fresh Final Compile Manifest may bind the current Seal and diagnostic authority through the existing public input route. No migration framework or alternate repair lifecycle is added.

## Focused execution

The frozen verification driver selects new methods from these classes explicitly:

- `Issue105AbsoluteManifestSourceTests` in `test_issue105_absolute_manifest_sources.py`.
- `Issue112NamedFinalPdfTests` in `test_issue112_named_final_pdf.py`.
- `Issue113SemanticInputRepairTests` in `test_issue113_semantic_input_repair.py`.

Production qualification resumes the original frozen repair command, obtains fresh semantic reviews and a Text Seal, and runs Final Compile with the requested basename. The retained Run independently completes Final Compile with a current declared manifest. Original-PDF delivery additionally requires Rendered Text Reconciliation, Acceptance Report v2, individual inspection of every rendered page, and a fresh Delivery Guard pass.

## Operator filename input

`delivery-quality-final-compile --pdf-basename <normalized-article-title.pdf>` selects the final leaf filename before compilation. The value uses the project's existing title normalization rule and includes the lowercase `.pdf` extension. Omitting it preserves the current `final.pdf` default.

For the original chip-team article, the value is `协同先于智能_让芯片设计团队像一个身体一样行动.pdf`. The returned `final_pdf_path` supplies the path for Rendered Text Reconciliation and Final Evidence preparation. Publication does not rename a sealed PDF.
