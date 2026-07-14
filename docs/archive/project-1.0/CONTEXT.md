# Project 1.0 Planning Context

Status: archived and read-only.

This context preserves the historical language of the completed Project 1.0 local planning model. Its terms explain archived records and must not be used to create, publish, classify, prioritize, or execute new planning work.

## Feature Issue Set

The Project 1.0 planning unit that grouped implementation issues belonging to one feature. It owned batch-level execution planning and dependency visualization within the archived local tracker.

## Issue Dependency Edge

An execution-order relationship between two Project 1.0 issues in the same Feature Issue Set. It identified which upstream issue had to complete before downstream work could proceed.

## Issue Dependency View

A generated Project 1.0 projection of execution order and status for one Feature Issue Set. It served as a navigation aid while archived issue metadata retained dependency authority.

## Issue Dependency Index

A generated Project 1.0 entry point that summarized the available Feature Issue Sets and their execution states.

## Issue Dependency View Generator

The retired Project 1.0 projection component that materialized and checked dependency views from issue metadata. It held no independent planning or dependency authority.

## Issue Dependency View Freshness

The condition in which a generated Issue Dependency View still represented the issue metadata from which it was derived.

## Issue Dependency Source Fingerprint

The deterministic identity of the Project 1.0 issue metadata that affected dependency projections. It allowed a generated view to be associated with its exact source state.

## Issue Dependency Consistency Error

A finding that made Project 1.0 dependency metadata or a generated dependency projection unreliable as an execution guide.

## Currently Executable Issue

A Project 1.0 issue classification for work whose own status permitted execution and whose declared dependencies were complete.

## Next Executable Issue List

A generated Project 1.0 projection listing the Currently Executable Issues in one Feature Issue Set.

## Waiting On Dependencies List

A generated Project 1.0 projection listing Dependency-Blocked Issues together with the upstream work preventing their execution.

## Status-Blocked Issue

A Project 1.0 issue whose own recorded status declared an explicit blocker. The blocker belonged to the issue itself rather than being inferred solely from incomplete dependencies.

## Dependency-Blocked Issue

A Project 1.0 issue whose own status otherwise permitted execution while at least one declared dependency remained incomplete.

## Issue Dependency Layer

A visual ordering group in a Project 1.0 Issue Dependency View derived from dependency depth. It communicated relative execution order without becoming a separate scheduling authority.

## Issue Node Status Color

The visual encoding of an archived issue's recorded status in an Issue Dependency View. Dependency relationships remained represented by edges and layers.

## Issue Status Color Palette

The stable Project 1.0 mapping from issue statuses to visual colors across generated dependency views.
