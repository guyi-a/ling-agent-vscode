---
name: inline-visualization
description: Use compact inline diagrams for flows, architecture, comparisons, timelines, and state transitions.
alwaysApply: true
---

# Inline diagrams

- When a flow, architecture, mechanism, comparison, timeline, hierarchy, or
  state transition is materially easier to understand visually, load the
  `inline-visualization` skill before composing the answer.
- Prefer one compact SVG over a long prose enumeration.
- Emit diagrams only in a fenced `html` block whose info string includes
  exactly `type="renderer"`.
- Keep the diagram self-contained and accessible. Do not use scripts, external
  resources, remote images, or remote fonts.
- Keep prose to a short conclusion and caption. Do not force a diagram for a
  simple fact, one-step action, or purely textual answer.
