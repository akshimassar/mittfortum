# Agent Notes

Guidance for agents working in this `my-energy` dashboard strategy repo.

## Scope
- This repo contains a Home Assistant dashboard strategy script (`my-energy-strategy.js`).
- Prefer minimal, focused changes that keep dashboard behavior stable.
- Home Assistant frontend source is available at `../frontend` for reference.

## Update Frequency and Loops
- Keep updates sparse: avoid re-rendering or re-subscribing on every `hass` tick.
- Prefer event-driven updates over polling.
- Avoid busy loops (`setInterval`, recursive `setTimeout`, repeated `requestAnimationFrame`) unless strictly required and guarded.

## Debugging Transparency
- During active debugging, do **not** deduplicate, throttle, or suppress debug logs.
- Log every trigger/event path until root cause is identified.
- After fix verification, reduce debug noise (remove logs or gate them behind an explicit debug flag).

## Safety
- Do not add destructive git commands unless explicitly requested.
- Keep user-visible labels and behavior deterministic.

## Commit Policy
- Always create a commit for completed changes in this repo, even when the user did not explicitly ask.
- When asked to commit in this repo, include all current `my-energy` changes unless the user narrows the scope.
