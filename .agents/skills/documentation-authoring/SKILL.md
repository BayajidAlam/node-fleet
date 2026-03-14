---
name: documentation-authoring
description: Documentation writing standards for VisionSync. Use when creating or updating architecture docs, technical guides, and operational references to keep structure, terminology, and clarity consistent.
metadata:
  version: "1.0.0"
---

> Always read [`.agents/CONTEXT.md`](./../../CONTEXT.md) first. It defines canonical architecture, API flows, and infrastructure terms.

# Documentation Authoring Skill

## When to Use This Skill

Invoke this skill when:

- Creating new technical documentation in `doc/`
- Updating architecture or deployment documentation
- Writing system overviews, component docs, or integration guides
- Producing decision records and tradeoff explanations
- Refactoring existing docs for readability and consistency

---

## Authoring Principles

1. Write for implementers first, not marketing style prose.
2. Lead with architecture intent, then behavior, then operational impact.
3. Keep statements testable and specific.
4. Avoid ambiguous words like "soon", "fast", or "normally" without criteria.
5. Link claims to concrete files, endpoints, or configuration keys.

---

## Recommended Doc Structure

Use this section order unless the request needs a different shape:

1. `Overview`
2. `Scope`
3. `Architecture Summary`
4. `Data Flow` (must include Excalidraw JSON when flow is discussed)
5. `Components and Responsibilities`
6. `Interfaces` (API/events/queues/storage contracts)
7. `Security and Reliability Notes`
8. `Operational Runbook` (deploy, observe, rollback)
9. `Assumptions and Risks`
10. `Change Notes`

---

## Style Rules

- Prefer short paragraphs and concrete bullet points.
- Keep heading names stable across related docs.
- Use tables for component and interface mapping.
- Use explicit file references where useful.
- Use consistent service names from `.agents/CONTEXT.md`.

---

## Documentation Quality Checklist

- The doc answers what changed and why.
- The doc identifies affected services and data boundaries.
- The flow section is aligned with real implementation behavior.
- Security, failure, and retry behavior are called out where relevant.
- Links and file references are valid.
- No stale architecture claims conflict with `.agents/CONTEXT.md`.
