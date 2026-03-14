---
name: doc-sync-governance
description: Documentation synchronization and governance for VisionSync. Use to prevent drift between .agents/CONTEXT.md, doc files, and top-level docs whenever architecture or data-flow content changes.
metadata:
  version: "1.0.0"
---

> Always read [`.agents/CONTEXT.md`](./../../CONTEXT.md) first. Treat it as the baseline source of truth before approving doc updates.

# Documentation Sync Governance Skill

## When to Use This Skill

Invoke this skill when:

- Finalizing architecture or flow documentation changes
- Updating any material that may impact system behavior descriptions
- Reviewing docs for consistency before merge
- Creating release notes for architecture-impacting changes

---

## Sync Scope

This skill validates and aligns:

- `.agents/CONTEXT.md`
- `doc/**`
- `Readme.md`
- `DEPLOYMENT.md`
- Architecture-focused docs under `IaC/` when relevant

---

## Governance Workflow

1. Collect all changed documentation files.
2. Extract key facts from each file: components, data paths, triggers, status transitions, and environment dependencies.
3. Compare those facts against `.agents/CONTEXT.md`.
4. Resolve drift by updating docs so one canonical behavior is represented.
5. Produce a short sync report describing what was aligned.

---

## Drift Categories

- `Critical Drift`: Contradictory behavior that can mislead implementation or operations.
- `High Drift`: Missing or stale components in architecture or flow sections.
- `Medium Drift`: Naming mismatch or incomplete interface detail.
- `Low Drift`: Formatting and section-order inconsistencies.

---

## Required Final Check

Before finalizing doc work:

- Confirm architecture and flow claims match `.agents/CONTEXT.md`.
- Confirm Excalidraw flow visuals are present where flow is explained.
- Confirm docs changed in this task reference consistent service names and status transitions.
- Provide a brief sync summary listing files checked and drift resolved.
