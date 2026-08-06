---
name: internal-comms
description: Help Sensei write internal communications in standard formats: status reports, leadership updates, 3P updates (Progress/Plans/Problems), project updates, incident reports, FAQs, newsletters, meeting notes. Use when asked to write any kind of internal communication.
---

# Internal Comms — Arona's Communication Templates

Use these templates and guidelines when Sensei needs to write internal communications.

---

## 3P Update (Progress / Plans / Problems)

The most common format for async status communication.

```
**[PROJECT NAME] — 3P Update**
*Week of [DATE]*

**✅ Progress** *(What got done this week)*
- [Specific accomplishment 1]
- [Specific accomplishment 2]
- [Metric if relevant: "Shipped X to Y% of users"]

**📋 Plans** *(What's next)*
- [Next action 1 — owner if relevant]
- [Next action 2]
- [Target date if known]

**🚨 Problems** *(Blockers, risks, asks)*
- [Blocker + what's needed to unblock]
- [Risk + mitigation plan]
- [Ask: "Need decision from X on Y by Z"]
```

**Tips:**
- Bullet points, not prose — this is a scan document.
- Problems section is the most important; never omit it to look good.
- Quantify progress where possible (numbers, percentages, shipped/done).

---

## Leadership Update

For upward communication to managers or execs.

```
**[PROJECT] — Leadership Update**
*[DATE]*

**Summary** *(2-3 sentences max)*
[What is the project, where does it stand, what's the key message]

**Status: 🟢 On Track / 🟡 At Risk / 🔴 Off Track**

**Key Wins**
- [Achievement with business impact]

**Key Risks**
- [Risk | Likelihood | Mitigation]

**Decisions Needed**
- [Decision | Options | Recommendation | Deadline]

**Next Milestone**
[What, by when, success criteria]
```

**Tips:**
- Open with the most important thing first.
- Never bury a red flag in the middle of a green update.
- State asks explicitly ("Need X from Y by Z").

---

## Incident / Post-Mortem Report

```
**Incident Report — [INCIDENT NAME]**
*Date: [DATE] | Severity: P[1/2/3]*

**Executive Summary**
[2-3 sentences: what happened, impact, current status]

**Timeline**
| Time (UTC) | Event |
|-----------|-------|
| HH:MM | [Event description] |
| HH:MM | [Detection / escalation] |
| HH:MM | [Mitigation applied] |
| HH:MM | [Resolution / monitoring] |

**Root Cause**
[Primary cause. Avoid blame. Focus on systems/processes.]

**Impact**
- Users affected: [number or %]
- Duration: [X minutes/hours]
- Services affected: [list]
- Data loss: [Yes/No — if yes, details]

**What Went Well**
- [Rapid detection via alert Y]
- [Clear runbook reduced MTTR]

**What Went Wrong**
- [Missing alert for Z condition]
- [Runbook step X was ambiguous]

**Action Items**
| # | Action | Owner | Due Date |
|---|--------|-------|----------|
| 1 | [Specific, measurable fix] | [Name] | [Date] |
| 2 | ... | | |
```

---

## Project Update (Broader Audience)

```
**[PROJECT NAME] Update — [DATE]**

**What We're Building**
[1-2 sentences — assume reader has no context]

**Progress This Period**
- [Key accomplishments]

**What's Next**
- [Upcoming milestones]

**How You Can Help**
- [Specific asks from the audience]
```

---

## FAQ Document

Structure FAQs as question-answer pairs in logical groups:

```
# [Topic] — FAQ

## Getting Started
**Q: [Most common first question]**
A: [Clear, concise answer. Link to more detail if needed.]

**Q: [Second question]**
A: [Answer]

## Advanced Topics
**Q: [Technical or edge-case question]**
A: [Answer]

---
*Last updated: [DATE] | Questions? Contact [NAME/CHANNEL]*
```

---

## Writing Principles

**Clarity above all:**
- Write for someone who has 30 seconds to skim.
- Put the most important information first (inverted pyramid).
- Use headers and bullets for anything > 3 sentences.

**Avoid:**
- Passive voice: "It was decided" → "We decided"
- Vague language: "soon," "significant," "various"
- Jargon your audience doesn't share

**Always include:**
- Date / timeframe
- Who owns what
- What action is needed from the reader (if any)

---

## Tone Guidelines

| Audience | Tone |
|----------|------|
| Peers / team | Direct, collaborative, can be casual |
| Manager / skip-level | Professional, concise, decision-focused |
| Exec / leadership | Very concise, strategic, bottom-line first |
| Broad org / all-hands | Clear, positive, assume no context |
