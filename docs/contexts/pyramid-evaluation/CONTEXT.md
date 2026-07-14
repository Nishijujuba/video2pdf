# Pyramid Evaluation Context

Status: active.

This context owns the standards and semantic decision language for evaluating pyramid structure. A calling workflow owns evaluation timing, continuation rules, and any workflow state derived from the published result.

## Pyramid Principle Text Standard

The general-purpose standard for judging whether written reasoning presents its central claim early, groups supporting ideas coherently, preserves meaningful same-level distinctions, and aligns headings with their supporting content.

## Teaching-PDF Pyramid Standard

The teaching-document specialization of the Pyramid Principle Text Standard. It preserves top-down support while allowing learner-facing progression through motivation, mechanism, examples, figures, and takeaways.

## Semantic Review

A meaning-based evaluation of whether claims, headings, explanations, and evidence form a valid support hierarchy. It supplies the judgment that mechanical structure checks cannot establish.

## Pyramid Evaluation Target

A caller-supplied text artifact and evaluation context whose reasoning structure is assessed against one Pyramid standard. Its workflow timing and downstream state belong to the calling workflow.

## Pyramid Gate

The semantic quality evaluation that determines whether a Pyramid Evaluation Target satisfies its selected standard. It publishes a Pyramid Gate Report for downstream consumption.

## Pyramid Gate Report

The authoritative machine-readable result of a Pyramid Gate, bound to the exact target and evaluation context. It records the structural judgment, supporting evidence, and unresolved weaknesses without owning downstream workflow actions.

## Waiver

A human-authorized exception that permits a workflow to proceed despite a known Pyramid Gate weakness. The human workflow owner grants it, the calling workflow records it, and the Pyramid evaluator has no waiver authority.
