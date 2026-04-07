---
name: Dashboard bug report
about: Report dashboard strategy/frontend issues (layout, cards, debug export)
title: "Dashboard bug: <short description>"
labels: bug,dashboard
assignees: akshimassar

---

## Describe the bug

Please describe the problem you observe in details. Add what should happen
instead for non-obvious cases.

## To Reproduce

How to reproduce the problem (if known) or when it happens.

## Required diagnostics (dashboard)

Please attach dashboard debug export instead of raw logs:
1. Enable `debug: true` in your dashboard strategy config.
2. Reload the dashboard once so debug mode is active.
3. Reproduce the issue.
4. Without reloading the page, click **Export Debug**.
5. Attach the exported JSON file to this issue.

If available, include browser console errors (message + stack) from the same reproduction.

## Additional context

Add any other context, screenshots, or recordings here.
