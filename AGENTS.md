# Agent Notes

Guidance for AI/code agents working in this repository.

## Scope
- This repo contains a Home Assistant custom integration at `custom_components/fortum`.
- Work usually falls into one of two independent tracks:
  - API integration/backend (Python)
  - Dashboard strategy/frontend (JavaScript)
- Prefer small, focused changes that preserve existing behavior and startup performance.

## Repository Areas
- Integration code: `custom_components/fortum`
- Dashboard strategy: `custom_components/fortum/frontend/fortum-energy-strategy.js`
- Coordinators: `custom_components/fortum/coordinators.py`
- Tests: `tests/unit`, `tests/integration`, `tests/e2e`
- Contributor docs: `DEVELOPMENT.md`

## Workflow (Common)
1. Read relevant files first.
2. Implement the smallest safe change.
3. Add or update tests for behavior changes.
4. Run checks before committing.

## API Integration (Backend) Rules
- Keep startup/setup work efficient and avoid unnecessary I/O in setup paths.
- Preserve entity/statistics behavior unless the change explicitly targets it.
- Prefer deterministic, idempotent setup logic (safe on reload/restart).

## Dashboard Strategy (Frontend) Rules
- Keep updates sparse: avoid re-rendering or re-subscribing on every `hass` tick.
- Prefer event-driven updates over polling.
- Avoid busy loops (`setInterval`, recursive `setTimeout`, repeated `requestAnimationFrame`) unless strictly required and guarded.
- Do not rearrange visual layout/order of cards or sections without user confirmation.

## Debugging and Logging
- Never log secrets/tokens/cookies/session payloads.
- During active dashboard debugging, do not suppress or deduplicate logs that are needed to trace lifecycle/order issues.
- After verification, reduce debug noise (remove logs or gate behind explicit debug controls).
- Keep debug logs useful and concise.

## Fix Quality
- Prefer root-cause fixes over symptom-level timing/retry workarounds.
- If a temporary workaround is unavoidable, mark it clearly and follow up with a root-cause fix.
- If internal behavior changes materially, rename methods/functions to match new semantics (avoid stale names that describe old behavior).

## Validation
- Required baseline checks:
  - `uv run ruff check custom_components/fortum tests`
  - `uv run pytest` (or targeted subsets when appropriate)
- Backend-only changes: at least relevant unit/integration subset.
- Dashboard-only changes: validate strategy/resource loading path and affected frontend behavior.

## Safety and Git
- Avoid destructive git operations unless explicitly requested.
- Never revert unrelated user changes.

## Commit Guidelines
- Commit only when the user explicitly asks.
- Use an imperative, concise title.
- Include a brief description/body (1-3 lines) explaining what changed and why.
