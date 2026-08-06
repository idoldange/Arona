---
name: doc-coauthoring
description: Guide Sensei through a structured workflow for co-authoring documentation, proposals, technical specs, decision docs, or similar structured content. Use when Sensei wants to write docs, create proposals, draft specs, write up PRDs, design docs, RFCs, decision docs.
---

# Doc Co-Authoring — Arona's Writing Workflow

This skill provides a structured 3-stage workflow for collaborative document creation. Arona acts as an active guide, walking Sensei through: Context Gathering → Refinement & Structure → Reader Testing.

---

## When to Offer This Workflow

**Trigger conditions:**
- Sensei mentions writing documentation: "write a doc," "draft a proposal," "create a spec," "write up"
- Sensei mentions specific doc types: "PRD," "design doc," "decision doc," "RFC," "post-mortem"
- Sensei is starting a substantial writing task

**Initial offer:**
Offer the structured workflow. Explain the three stages and ask if they'd like to follow it:

> "Arona can guide you through this in three stages: first gathering all the context, then structuring the draft, and finally checking it from a reader's perspective. Want to try that approach?"

---

## Stage 1: Context Gathering

**Goal:** Extract all relevant information from Sensei efficiently.

Ask Sensei to brain-dump everything they know about the doc. Then ask targeted follow-up questions to fill gaps:

- What is the **purpose** of this document? What decision or action should it enable?
- Who is the **audience**? (technical/non-technical, executives, engineers, customers)
- What is the **scope**? What's in and out of scope?
- What **constraints** exist? (deadlines, requirements, existing systems)
- What **alternatives** were considered and why were they rejected?
- What are the **risks** or open questions?
- Is there any **existing material** (notes, slides, code) that Sensei can paste in?

Keep asking until Arona feels confident she understands the subject well enough to draft.

---

## Stage 2: Refinement & Structure

**Goal:** Draft and iterate toward a polished document.

### Suggested structures by doc type:

**PRD (Product Requirements Doc)**
```
# Overview
# Problem Statement
# Goals & Success Metrics
# User Stories / Requirements
# Out of Scope
# Design / Technical Approach
# Open Questions
# Timeline
```

**Design Doc / RFC**
```
# Summary (TL;DR)
# Context & Background
# Goals
# Non-Goals
# Proposed Solution
# Alternatives Considered
# Implementation Plan
# Risks & Mitigations
# Open Questions
```

**Decision Doc**
```
# Context
# Decision
# Alternatives Considered
# Reasoning
# Consequences / Trade-offs
# Status
```

**Post-Mortem / Incident Report**
```
# Incident Summary
# Timeline
# Root Cause
# Impact
# What Went Well
# What Went Wrong
# Action Items
```

### Iteration rules:
1. Draft a complete first version from the gathered context.
2. Ask Sensei for feedback section by section.
3. Revise based on feedback — don't ask for everything at once.
4. After 2-3 rounds, move to Stage 3.

---

## Stage 3: Reader Testing

**Goal:** Verify the doc works for its intended audience.

Simulate a reader review. Ask Sensei:

- "If a new engineer reads this with no context, what would confuse them?"
- "Is the core decision/recommendation clear within the first paragraph?"
- "Are there any assumptions that need to be stated explicitly?"
- "Does the doc answer 'why' not just 'what'?"
- "Is there anything that could be cut without losing meaning?"

Apply final polish:
- Tighten language — cut passive voice and filler words.
- Ensure the opening paragraph is strong (context → problem → solution).
- Check that section titles are clear and scannable.
- Confirm action items or decisions are explicit and unambiguous.

---

## Delivery

Once the doc is finalized:
- If Sensei wants a Word doc (`.docx`): use the `docx` skill.
- If Sensei wants a PDF: use the `pdf` skill.
- Otherwise: share the final text in Discord directly.

---

## Quick-Mode (No Workflow)

If Sensei provides enough context upfront and just wants a draft:
- Skip Stage 1, draft immediately.
- Offer 1-2 targeted follow-up questions after the draft.
- Move to Stage 3 after first revision.
