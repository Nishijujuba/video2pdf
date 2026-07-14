# Isolate source acquisition as a validated package handoff

Source acquisition currently mixes coordinator commands, platform-specific download rules, Cookie handling, subtitle fallback, and informal file inspection. This consumes the same context later needed for outline and writing work and leaves downstream agents to infer whether downloaded artifacts are complete or technically usable.

## Considered Options

- Let the coordinator download materials directly: rejected because acquisition logs, retries, and platform failures consume orchestration context and produce inconsistent handoffs.
- Split downloading and technical validation into separate subagents: rejected because they operate on the same platform transaction and would duplicate file discovery, provenance, and failure handling.
- Let the acquisition role also summarize content and select figures: rejected because those judgments belong to Outline, Writer, and Figure Agents with different evidence and review responsibilities.

## Decision

Every `fresh_download` Source Acquisition Mode uses a dedicated Source Acquisition Agent. It invokes the selected Video Platform Adapter to obtain platform metadata, the original cover, timestamped subtitles, the best usable media, and audio when required. When usable subtitles are unavailable, it invokes the configured Whisper fallback under the platform and language rules. A successful deterministic `verified_import` follows ADR 0018 and does not launch this Agent.

Scripts probe media streams, durations, languages, file sizes, and fingerprints. The Agent evaluates only the bounded quality and policy choices that require semantic judgment and returns a Source Acquisition Decision Judgment Patch. Under ADR 0019, `source-finalize` validates that Patch plus script-owned evidence, materializes the Source Manifest, and hands off the Validated Source Package. The Agent never writes the Source Manifest directly.

The Source Acquisition Agent records Cookie rejection or expiration as an explicit blocker and does not silently change authentication policy. It does not select key frames, crop images, summarize content, design the outline, or write the PDF. Figure Agents own derived visual selection; Outline and Writer Agents own semantic interpretation.

## Consequences

Downstream agents consume one declared source boundary instead of searching download directories. The source checkpoint can be validated mechanically before semantic work starts. Fresh acquisition and verified import share the same script-owned Source Manifest authority while only fresh acquisition requires the semantic Agent.
