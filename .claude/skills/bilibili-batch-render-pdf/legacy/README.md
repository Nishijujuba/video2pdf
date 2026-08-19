# Legacy Batch Driver (retained)

This directory stages the retired Legacy batch driver that previously lived at
`.agents/skills/bilibili-batch-render-pdf/scripts/run_batch.py`. It is retained
for **pre-existing batch directories only** and is not part of the Kernel batch
flow. Nothing here is deleted.

## Status

Per ADR 0036 and issue #15, the Legacy batch driver is retired for new batches:

- **New batches must use the Kernel `batch-*` CLI** through
  `scripts/video_workflow.py` (`batch-plan` -> `batch-run` ->
  `batch-recover`/`batch-status`). See the parent `SKILL.md`.
- The Legacy driver's **PDF-existence success rule** and **global
  `--concurrency` authority** are retired and carry no new-batch authority.
  Batch item success is defined only by a Video Workflow Run reaching the
  `delivered` delivery stage with a fresh passing Delivery Guard report.
- The Legacy driver's manual/reconcile modes remain available only for
  pre-existing legacy batch directories that already contain a legacy manifest.

## Contents

- `scripts/run_batch.py` — the Legacy driver (plan/manual/run/reconcile modes).
- `scripts/test_run_batch.py` — the Legacy driver's unit tests (still discovered
  by the `skill-tests` suite under `.agents/skills/**/test_*.py`).
- `references/manifest.schema.json` — the Legacy manifest schema.
- `references/part-result.schema.json` — the Legacy child-task result schema.
- `agents/openai.yaml` — the Legacy agent model/runtime configuration.

## How to run it from its new location

The invocation is unchanged in shape; only the script path is adjusted:

```powershell
python .agents\skills\bilibili-batch-render-pdf\legacy\scripts\run_batch.py --help

python .agents\skills\bilibili-batch-render-pdf\legacy\scripts\run_batch.py `
  --manifest "D:\Project\video2pdf\newskill-kimi\workspace\<batch>\batch-control\manifest.json" `
  --mode run
```

`run_batch.py` resolves its bundled schema and the Pyramid output gate relative
to its own file location (`Path(__file__).resolve()`), so it continues to find
`legacy/references/part-result.schema.json` and
`.agents/skills/pyramid-principle-validate/scripts/check_output_gate.py` from
its new home.

## Do not use for new batches

New Bilibili batch work must use the Kernel `batch-*` CLI. Using this driver to
create new batches would reintroduce retired authorities (PDF-existence success
and global concurrency) that issue #15 explicitly removes.
