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
- Coordinators: `custom_components/fortum/coordinators/`
- Tests: `tests/unit`, `tests/integration`, `tests/e2e`
- Contributor docs: `docs/development.md`
- API examples doc: `docs/fortum-api.md`

## Quality
- Always find the root cause of the problem.
- If root cause is unclear, ask for more debug and propose a debugging plan.
- If several possible cause available, list them for the user.
- Don't use symptom-level fixes or band-aid quality fixes.
- Keep naming consistent with internal semantics, update naming when semantics changes.
- Do not avoid refactoring work. Always prefer cleaner consistent repository over localization of change.
- When doing a change, ask yourself if it can be done cleaner, with less logical conditions. If so, propose that change to user.
- Try to find best cleanest possible architectural solution.

## Architecture Invariants
- Preserve clear layer ownership and a single source of truth for each domain concern.
- Avoid cross-layer links and side-channel access; consumers must use the owning abstraction.
- When introducing a new manager/service (for example `SessionManager`), migrate all related reads/writes to it in the same change.
- Do not leave mixed access paths (new abstraction + legacy direct access) in runtime code.
- Treat bypassing the owner boundary as an architecture violation.

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

## Logging Level Policy

Choose log level by user impact and actionability.

- `exception`
  - Unexpected exceptions only, which require developer attention to fix
  - Do not use logger.exception for expected domain errors (APIError, AuthenticationError, FortumError, UpdateFailed).
- `error`
  - Persistent or severe failures requiring user attention
  - Setup cannot complete, unrecoverable runtime state
- `warning`
  - High-level error or stage transitions not requiring user attention but providing additional info.
  - Whole entry state transitions: service/API became unavailable or recovered, i.e. in Session Manager
  - This intentionally keeps entire integration state transition on higher level compared to HA recommendations.
  - Coordinator state transition but only if it could degrade user experience.
    For example, spot price coordinator became unavailable.
    But consumption coordinator works with historical data and its state should not be logged there.
- `info`
  - Events that provide experienced user with additional info.
  - Issue happened but integration continued to work and user won't notice the difference.
  - Some error happened despite retries
- `debug`
  - Only required for developer to debug.
  - Single retry error
  - Normal operation high-level calls but not too noisy.
    For example, single line for Coordinator update that happens every 5 minutes.
- Avoid logging duplicate information on several architectural levels.

## Coordinator and Availability Behavior

Coordinator:

- Raise `UpdateFailed` for fetch failures so entities become unavailable correctly.
- Raise `ConfigEntryAuthFailed` for auth failures to trigger reauth.
- Additional logging for (coordinator) state transitions might not be required.

Availability, i.e. Session Manager:
- For availability outages, prefer:
  - one `warning` when becoming unavailable
  - one `warning` when recovered
  - transient per-request details at `debug`/`info` as needed

## Validation
- Required baseline checks:
  - `uv run ruff check custom_components/fortum tests`
  - `uv run pytest` (or targeted subsets when appropriate)
- Backend-only changes: at least relevant unit/integration subset.
- Dashboard-only changes: validate strategy/resource loading path and affected frontend behavior.

## Safety and Git
- Avoid destructive git operations unless explicitly requested.
- Never revert unrelated user changes.

## Compatibility
- Do not add backward-compatibility aliases; use one canonical name/path and update references directly.

## Commit Guidelines
- Commit only when the user explicitly asks.
- Use an imperative, concise title.
- Follow standard git commit message formatting:
  - Subject line should be under 50 chars when possible.
  - Wrap body lines at 72 chars, do not break words by newline.
- Include a brief description/body (1-3 lines) explaining what changed and why.
- Keep commits atomic: isolate independent features/fixes from one another.
- Do not mix unrelated changes in a single commit; prefer one concern per commit (feature, fix, docs, refactor, release metadata).
- If a fix is discovered while implementing a feature, prefer a separate follow-up commit unless the fix is strictly required for the same change to work.

## Release Workflow
- When preparing a release, keep these in sync:
  - `custom_components/fortum/manifest.json` -> integration `version`
  - `hacs.json` -> minimum supported `homeassistant` version
  - `CHANGELOG.md` -> add/update release notes for the new version
- Verify docs reflect behavior changes before tagging (at minimum `README.md`, `docs/dashboard.md`, `docs/development.md`).
- Keep `CHANGELOG.md` end-user oriented, do not include updates that doesn't change UX.
- Keep GitHub release notes style consistent with recent releases: use a plain bullet list copied from the version changelog (no extra section headings).
- Typical release flow: edit `CHANGELOG.md`, get explicit user confirmation, commit and create annotated tag, user pushes commit/tag, then create the GitHub release with `gh`.
- Use annotated tags with `v` prefix (for example `v4.1.0`) and ensure old/non-canonical tags are not left behind.
- When creating the GitHub release with `gh`, use the tag version as title and copy plain bullet notes from `CHANGELOG.md`.
