---
name: context-sync
description: Keep TraceFlow documentation synchronized with reality. Load at the end of every working session that produced meaningful artifacts. Governs when to update CHANGELOG.md, the PRD, architecture.md, the workflow schema, and when to create new ADRs. Without this discipline, docs rot and Claude Code gets worse over time.
---

# Context Sync

The single biggest failure mode of long-running projects with AI assistance: **documentation falls out of sync with reality, and the AI's suggestions degrade because it's reasoning from stale context.** This skill prevents that.

## The cardinal rule

**A session is not done when the code is committed. It's done when the docs reflect the new reality.**

At the end of every meaningful session, run through this skill's checklist. If any "yes" answers, update the corresponding doc.

## When to update CHANGELOG.md

Update `docs/CHANGELOG.md` whenever any of these happen:

- A meaningful technical decision was made
- A major piece of functionality was built or shipped
- Pricing, packaging, or positioning changed
- A pivot or pause was decided
- A milestone was hit ($1K MRR, first client signed, first case study published, etc.)
- A vendor or tool was selected/swapped/abandoned
- An assumption was disproven or confirmed by real data

How to write entries:

```markdown
## YYYY-MM-DD — Brief session summary

### type: One-line summary
- **What:** Concrete description
- **Why:** Reasoning, links to alternatives considered
- **Status:** Current state (decided/built/launched/paused)
- **Links:** [PR, doc, ADR, etc.]
```

**Append at the top, never at the bottom.** Most-recent-first reading order.

**Don't write entries for trivia.** "Fixed typo in README" doesn't merit a CHANGELOG entry. "Migrated from REST to GraphQL for Monday integration" does.

## When to update the PRD

`docs/PRD.md` is the strategic source of truth. Update when:

- ICP changes (we widen, narrow, or shift target)
- Pricing changes
- Offer structure changes (LLR vs SIA, modules, tiers)
- Risk landscape shifts meaningfully
- Strategic fork decision becomes clearer
- Discovery script needs updating based on what's working/failing

Increment the version number in the metadata block (1.1 → 1.2) and add to the changelog at the top of the PRD itself.

**Don't update the PRD for tactical changes.** Pricing experiment in one segment? CHANGELOG entry. Pricing structurally changed for all clients? PRD update.

## When to update architecture.md

`docs/architecture.md` describes the technical platform. Update when:

- A new layer is added (new adapter type, new service)
- The data model changes structurally (new tenant-scoped table, schema refactor)
- A core operating principle changes (rarely)
- A deferred decision becomes active (e.g., "we said no UI before Client 8, now we're at Client 8 and building it")

Don't update for code-level details. Implementation patterns live in skill files; architectural decisions live here.

## When to update workflow-schema.md

`docs/workflow-schema.md` defines all the workflows. Update when:

- A new stage is added to any workflow
- The order of stages changes
- Owners change (e.g., when a step moves from founder to VA in Phase 2)
- Automation level shifts (e.g., manual → scripted → UI-driven)
- A new workflow is created entirely

The workflow schema is **authoritative**. If reality diverges, the schema is wrong — fix it first.

## When to create an ADR

Architecture Decision Records (`docs/decisions/`) capture significant decisions with their context. Create one when:

- A decision was non-obvious (multiple plausible alternatives existed)
- The decision is hard or expensive to reverse later
- Future-you will wonder "why did we do it that way?"

Use sequential numbering: `0002-name.md`, `0003-name.md`, etc. Title file with the decision in present tense.

ADR template:

```markdown
# ADR-NNNN: <decision in present tense>

**Date:** YYYY-MM-DD
**Status:** proposed | accepted | superseded by ADR-XXXX | deprecated

## Context

What is the situation? What forces are at play? What are we trying to solve?

## Decision

We will <thing>.

## Alternatives considered

- **Option A:** <description>. Rejected because <reason>.
- **Option B:** <description>. Rejected because <reason>.

## Consequences

### Positive
- ...

### Negative
- ...

### Reversibility
How hard would it be to reverse this? What signals would indicate we should?
```

**Don't create ADRs for trivia.** "Use Black for code formatting" is a CHANGELOG entry. "Single shared Supabase project vs project-per-tenant" is an ADR.

## When to update SKILL.md files

Skill files capture **how we do things**. Update a skill when:

- A pattern in it is no longer current
- A new pattern emerges from real work
- An anti-pattern is discovered that wasn't called out
- The skill's frontmatter description doesn't match what's actually inside

When you find yourself doing the same thing in 3+ different sessions without it being in a skill, **promote it to a skill**.

## When to update CLAUDE.md

CLAUDE.md is the master context file Claude Code reads first. Update when:

- The phase changes (Phase 0 → Phase 1, etc.)
- Active priorities shift
- A new skill is added (update the skill index)
- A new doc is added (update the document index)
- Working style preferences change
- A new "always do" or "never do" rule emerges

Keep it tight. CLAUDE.md is meant to be readable in 2 minutes. If it exceeds that, move detail into skills or docs and link.

## The end-of-session checklist

After any working session, run through this:

```
□ Was a meaningful decision made? → CHANGELOG entry
□ Was code committed? → CHANGELOG entry (if non-trivial)
□ Did strategy shift? → PRD update + version bump
□ Did the platform architecture change? → architecture.md update
□ Did any workflow stage change? → workflow-schema.md update
□ Was a hard, multi-option decision made? → New ADR
□ Did a pattern emerge that should be reused? → New or updated skill
□ Did the active phase or priorities change? → CLAUDE.md update
□ Were new files added to docs/ or skills/? → Update CLAUDE.md indexes
```

Most sessions: 1-2 boxes get ticked. Long or strategic sessions: 4-6. If 0 get ticked, you probably didn't accomplish anything worth tracking — that's also useful information.

## Anti-patterns

- **Documenting what we'll do, not what we did.** Aspirational docs are noise. Capture the decision *after* it's made, not before.
- **Letting docs sprawl.** Each doc has a purpose; resist the urge to create a new file when an existing one is the right home.
- **Updating docs without a CHANGELOG note.** Future-you (or another collaborator) can't tell *when* the doc changed without it.
- **Letting ADRs accumulate without status updates.** If you supersede a decision, mark the old ADR as superseded with a link to the new one.
- **Treating skills as static.** They evolve. The first version of any skill will look outdated in 3 months. That's fine — update it.

## Quarterly review (Phase 2+)

Once you're past Phase 0, set a recurring quarterly task:

- Re-read CLAUDE.md, PRD, architecture.md from scratch
- Find anything stale and either update or delete
- Read the last quarter's CHANGELOG and ADRs end-to-end
- Update phase, priorities, and metrics

Documentation rot is gradual. The quarterly sweep catches what daily updates miss.

## Closing principle

The reason this matters is simple: Claude Code (and any future collaborator) is only as good as the context they're given. **Investing 10 minutes in doc updates at the end of a session compounds into massive leverage over 12 months of building.**

The temptation will always be to skip the update step. Don't.
