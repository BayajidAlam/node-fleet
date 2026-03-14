---
name: excalidraw-architecture-flow
description: Excalidraw-first architecture and data-flow documentation for VisionSync. Use when describing process flow, system behavior, or component interactions; always include raw .excalidraw JSON and never Mermaid.
metadata:
  version: "1.0.0"
---

> Always read [`.agents/CONTEXT.md`](./../../CONTEXT.md) first. It is the source of truth for architecture, flow, and terminology.

# Excalidraw Architecture Flow Skill

## When to Use This Skill

Invoke this skill when:

- Explaining data flow between services or components
- Describing process logic, event pipelines, or request lifecycle
- Documenting architecture behavior beyond simple prose
- Comparing two architecture or workflow options
- Updating docs where flow diagrams can remove ambiguity

---

## Mandatory Output Policy

1. Every flow, process, or architecture explanation must include at least one raw `.excalidraw` JSON block.
2. Do not use Mermaid, PlantUML, or any other diagram language as a replacement.
3. If AWS icon cloud diagrams are requested, you may use `aws-diagrams` for cloud visualization, but still include Excalidraw for the flow narrative.
4. Keep Excalidraw output copy-paste ready with no placeholder comments and no truncated JSON.

---

## Excalidraw Construction Checklist

1. Define clear zones first (Client, Edge, Compute, Data, Async, Observability).
2. Place components inside correct zone boundaries.
3. Add directional arrows for every step and label critical transitions.
4. Number key steps in order of execution.
5. Show failure or retry paths when they affect behavior.
6. Keep labels concise and technically precise.
7. Ensure final JSON includes required top-level keys: `type`, `version`, `source`, `elements`, `appState`, `files`.

---

## Minimal JSON Skeleton

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [
    {
      "id": "zone-client",
      "type": "rectangle",
      "x": 40,
      "y": 60,
      "width": 220,
      "height": 140,
      "angle": 0,
      "strokeColor": "#1e1e1e",
      "backgroundColor": "#f1f3f5",
      "fillStyle": "solid",
      "strokeWidth": 1,
      "strokeStyle": "solid",
      "roughness": 0,
      "opacity": 100,
      "groupIds": [],
      "roundness": null,
      "seed": 1001,
      "version": 1,
      "versionNonce": 1111,
      "isDeleted": false,
      "boundElements": null,
      "updated": 1710000000000,
      "link": null,
      "locked": false
    }
  ],
  "appState": {
    "viewBackgroundColor": "#ffffff"
  },
  "files": {}
}
```

---

## Quality Gate Before Finalizing

- Confirm the flow described in prose matches the Excalidraw arrows.
- Confirm no step exists in prose that is missing in the diagram.
- Confirm no Mermaid block exists in the final output.
- Confirm diagram can be pasted into a `.excalidraw` file with no edits.
