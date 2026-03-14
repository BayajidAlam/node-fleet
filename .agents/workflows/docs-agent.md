---
description: Documentation agent for VisionSync - generates architecture docs, Excalidraw data flows, and AWS cloud documentation with sync checks
---

# VisionSync - Docs Agent

> Read [`.agents/CONTEXT.md`](./../CONTEXT.md) first before creating or updating any architecture documentation.

## Scope

This workflow handles:

- Documentation generation in `doc/`
- Architecture and data-flow documentation
- AWS cloud architecture writeups and service boundaries
- Documentation consistency across top-level project docs

## Skills to Load

Load skills in this order:

1. [`documentation-authoring`](./../skills/documentation-authoring/SKILL.md)
2. [`excalidraw-architecture-flow`](./../skills/excalidraw-architecture-flow/SKILL.md)
3. [`aws-solution-architect`](./../skills/aws-solution-architect/SKILL.md)
4. [`aws-diagrams`](./../skills/aws-diagrams/SKILL.md) for AWS icon cloud diagrams
5. [`doc-sync-governance`](./../skills/doc-sync-governance/SKILL.md) before finalizing

## Required Rules

- Every data-flow or process-flow explanation must include raw `.excalidraw` JSON.
- Do not use Mermaid.
- AWS icon diagrams are allowed for cloud architecture views.
- Run sync checks so docs remain aligned with `.agents/CONTEXT.md`.

## Typical Requests

- Create project architecture documentation from code and infrastructure files.
- Produce end-to-end data flow diagrams in Excalidraw format.
- Generate cloud architecture documentation with AWS service mapping.
- Audit and fix documentation drift across `doc/`, `Readme.md`, and `DEPLOYMENT.md`.
