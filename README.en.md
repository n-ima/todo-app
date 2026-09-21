# copilot-sdlc-harness — Spec-Anchored SDLC Harness

English overview. The full documentation is in Japanese: see [README.md](README.md).

## What it is

A general-purpose **spec-anchored SDLC harness** for GitHub Copilot (VS Code) and
Claude Code, with a frozen adapter for Antigravity. It orchestrates the whole
lifecycle — requirements, design, implementation, test, release — through phase
gates, then keeps spec and code mutually consistent during operation via a
difference-driven change loop (`/12-change-request`) and periodic convergence
audits (`/13-converge`). It is a template: it contains no application code.
Behavior is defined once (`AGENTS.md` + `.github/`) and thin, machine-generated
adapters serve each platform. Copilot and Claude Code are verified end-to-end on
the real products (version 1.2.0; Claude Code re-verified on 2.1.278 on 2026-09-21, Copilot last verified with 1.1.0 on 2026-08-30); the Antigravity adapter ships as-is but its
verification is frozen (D057).

## Key differentiators

- **Machine-enforced phase gates** — shell hooks (plus `permissions.deny` on
  Claude Code) deny/ask dangerous edits and commands mechanically, so the rules
  hold even when the model forgets them or is prompt-injected.
- **Deterministic verification** — "done" is granted only on evidence
  (verification commands and runtime checks), never on the agent's claim.
  Independent reviewers (`spec-critic`, `reviewer`) run in clean contexts.
- **Measured, not assumed** — on an ambiguous-spec A/B benchmark the harness
  passed 2/2 stages vs 0/2 for bare Claude Code (n=2; **reference value only**:
  apparatus v1 with unequal budgets and Claude Code 2.1.201, so not a like-for-like
  comparison — re-audit 2026-09-09 EV-3/EV-10; to be rerun with n=5 on apparatus v2,
  A6-16); build-vs-delegate
  decisions against official built-ins are settled by measurement (security
  detection delegated at 7/7 vs 7/7; independent review kept at 5/5 vs 4/5).
- **Growth loop** — lessons are logged during development, retrospectives
  produce improvement proposals, and both flow back into this template so the
  next project starts from an improved state.
- **Effort metrics** — token usage is logged automatically per phase, agent,
  and model, and reviewed in the retrospective for cost efficiency.

## Status and roadmap (as of 2026-08-31)

An independent re-audit (41 agents, 27 dimensions, 177 findings) identified a
structural gap between "recorded as applied" and "actually applied everywhere",
including two criticals: the harness CI had never gone green since introduction (first fully green run: 2026-09-10, run 34406967789)
(missing dependency declaration) and the config-edit guard misses
interpreter-based writes. Fix order: **P0** make CI actually green and close the
guard bypasses → **P1** generate the explanatory docs/HTML from primary data so
partial fixes become impossible → **P2** re-run the A/B with equal budgets and
n=5 (Fisher exact two-sided p=0.008 on full separation; re-audit 2026-09-09 §6),
including direct comparisons against other harnesses. Details:
[audits/external-reaudit-2026-08-31.md](audits/external-reaudit-2026-08-31.md).

## Quick start

1. Create a repository with "Use this template" and open it in VS Code
   (Copilot) or Claude Code.
2. Write a rough memo of what you want to build in `requirements/memo.md`.
3. Run `/00-start-project` and follow the guidance.

## License

MIT License — see [LICENSE](LICENSE).
