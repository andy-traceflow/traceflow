# Admin UI — Production-Grade Audit & Punch List

Audit date: 2026-06-17. Scope: `admin-ui/` (React 19 + Vite 6 + Tailwind v4),
served at `/admin`. Goal: production-grade for a portfolio/case-study piece.

**Anti-pattern verdict: PASS.** Avoids the AI-slop tells (no gradients, glass,
gradient text, drop-shadow card soup, AI palette). Real button hierarchy,
semantic status colors, strong microcopy, loading/empty/error states
everywhere. Main risk is the look isn't the problem — **accessibility is.**

Severity: 1 Critical · 5 High · 6 Medium · ~8 Low. Work through in phases.
Check items off as they land.

---

## Phase 1 — Accessibility blockers — ✅ DONE & VERIFIED 2026-06-17

> Built clean (`tsc -b && vite build`), no console errors, behaviors confirmed in
> the canned-data preview: h1 + ARIA tablist/tabpanel + labeled controls in the
> a11y tree; lead row keyboard-focusable (tabIndex 0) and Enter opens a
> `role="dialog"` `aria-modal` drawer with focus moved in; Escape closes it and
> restores focus to the row.

- [x] **CRITICAL — Lead rows are mouse-only.** `LeadsPanel.tsx` `LeadRow` `<tr onClick>` has no `tabIndex`/`onKeyDown`; keyboard users can't open a lead. → focusable + Enter/Space + `aria-label`. *WCAG 2.1.1*
- [x] **HIGH — Focus indicators stripped.** All inputs/selects/textareas use `outline-none focus:border-signal` (1px color change); `OutcomeForm` controls have no focus style at all. → `focus-visible:ring` on controls + base `:focus-visible` outline for buttons/links/rows. *WCAG 2.4.7 / 2.4.11*
- [x] **HIGH — Unlabeled controls.** Client switcher (`Shell.tsx:35`), activity window (`ActivityPanel.tsx:40`), classification filter (`LeadsPanel.tsx:42`), outcome select + `$ booked` input (`LeadsPanel.tsx:346,355`). → `aria-label`. *WCAG 1.3.1 / 4.1.2 / 3.3.2*
- [x] **HIGH — Secondary text fails contrast (systemic).** `text-zinc-600` ≈ 2.6:1, `text-zinc-500` ≈ 4:1 on the dark bg, both under AA. → promote secondary text to `zinc-400` (≈7.8:1). *WCAG 1.4.3*
- [x] **HIGH — Lead drawer isn't a real dialog.** `LeadsPanel.tsx` `LeadDrawer`: no `role="dialog"`/`aria-modal`/label, no focus-in/restore, no Escape. → dialog semantics + initial focus + Esc + focus restore. *WCAG 4.1.2*
- [x] **HIGH — Tabs aren't an ARIA tab pattern.** `Shell.tsx` nav buttons lack `role=tablist/tab`, `aria-selected`, tabpanel link, arrow-key nav. → full tablist pattern. *WCAG 4.1.2*
- [x] **MEDIUM — No `aria-live` on status/errors.** Login/panel errors + Config "Saved…"/"Done." not announced. → `role="alert"` / `role="status"`. *WCAG 4.1.3*
- [x] **MEDIUM — No `<h1>` after login.** Wordmark is a `<span>` (`Shell.tsx:32`); panels start at h2. → make the wordmark the `<h1>`.

## Phase 2 — Contrast finish + design tokens

- [ ] Promote remaining tiny `text-[10px]/[11px]` meta to a readable size/contrast.
- [ ] Replace hardcoded `accent-[#3b82f6]` (`ConfigPanel.tsx:147`, `LeadsPanel.tsx:58`) with `accent-signal`.
- [ ] Extract semantic tokens (success/warn/danger/surface/border) — only `--color-signal` exists today; emerald/amber/red/zinc are inline. → `/extract` or `/normalize`.
- [ ] Normalize border/background opacity steps (`zinc-800`, `/40`, `/60`, `/70` mixed). → `/normalize`.

## Phase 3 — Responsive + targets

- [ ] Tap targets under 24px (2.2 AA): tabs, "log out" (`Shell.tsx:56`), adjacent edit/delete links (`MappingsPanel.tsx:107-118`). → `/adapt`. *WCAG 2.5.8*
- [ ] Tables only `overflow-x-auto` on mobile — adapt to a stacked/card layout under `sm`. → `/adapt`.
- [ ] `sm:grid-cols-5` on the mappings form (`MappingsPanel.tsx:129`) is cramped — gate wide grids at `md`/`lg`.
- [ ] Replace native `confirm()` (`UsageCard.tsx:19`, `MappingsPanel.tsx:48`) with a styled confirm. → `/harden`.

## Phase 4 — Motion + finish

- [ ] Drawer slide-in + hover/tab transitions (none today). → `/animate`.
- [ ] `UsageCard` bar: if animated later, use `transform: scaleX` not `width`.
- [ ] Routing-log `key={i}` → stable key (`ActivityPanel.tsx:103`).
- [ ] Optional: stress-test the heavy uppercase-mono aesthetic. → `/critique`.
- [ ] Final spacing/alignment/consistency pass. → `/polish`.

## Keep (don't regress)

Distinctive non-AI aesthetic · primary/ghost/text button hierarchy · teaching
empty states · loading/empty/error states in every panel · drawer (not modal)
for detail · dirty-state Save button · clean `useCallback`-keyed data loading.
