# Changelog

All notable changes to this project are documented in this file.

## [4.2.1]

### Changed
- Fix mypy typing issues and align pre-commit cheks with CI

## [4.2.0]

### Changed
- Added optional per-metering-point current month consumption and cost sensors, controlled by a dedicated integration option.
- Added a Norway-only Norgespris consumption limit sensor when the source value is available from Fortum metering-point data.
- Refactored coordinator/session runtime ownership by introducing `SessionManager` and splitting coordinators into dedicated modules, improving setup/runtime consistency.
- Refined authentication and coordinator logging to reduce debug noise while preserving useful diagnostics context.
- Expanded contributor and API reference documentation with architecture notes and sanitized Fortum API examples.

## [4.1.2]

### Changed
- Standardized area suffix naming in spot-price UI labels to use `[AREA]` consistently across docs and dashboard references.
- Refined README visuals and clarifications around price-area behavior.
- Updated CI workflow so HACS validation and Hassfest run on release tags (`v*`) and can still be started manually.
- Declared Home Assistant component ordering via manifest `after_dependencies` for `energy`, `http`, `lovelace`, and `recorder` to satisfy dependency validation.

## [4.1.1]

### Changed
- Aligned dashboard cost overlay aggregation with the active consumption bucket resolution to keep chart points and tooltip windows consistent.
- Set the Energy time-range picker to open downward in the dashboard strategy to avoid clipping above the card.
- Updated HACS minimum supported Home Assistant version to `2026.1.0`.

## [4.1.0]

### Changed
- Spot-price and tomorrow-price data are now area-scoped: entities and forecast statistics include region/area suffixes (for example `fortum:price_forecast_<area>`), and the tomorrow-price card renders multiple areas on one card.
- Spot-price fetch now requires explicit `priceArea` values from Fortum session data (no region fallback behavior).
- Dashboard forecast discovery now uses Recorder statistic id listing and accepts only area-scoped forecast ids.
- Documentation and README were updated for area-scoped behavior, dashboard debugging, and plain Markdown images.

## [4.0.1]

### Changed
- Improved authentication and session handling across regions (including Norway), with more resilient SSO flow behavior and cleaner failure handling.
- Improved integration architecture and reliability, including setup-path simplifications and safer startup behavior.
- Improved logging and diagnostics: added function-name log context, reduced noise, and improved diagnostics export/troubleshooting support.
- Improved long-term stability of entities/devices by migrating identity handling to config-entry-based IDs to avoid duplicate/orphaned entities across restarts.
- Improved dashboard UX and maintainability: better card/source behavior, clearer labels/errors, and stronger runtime config/test coverage.
- Improved developer quality gates and docs organization to keep releases more predictable.

## [4.0.0]

### Changed
- Major release after repository separation.
- Over 80% of code rewritten.
- Integration renamed to fortum.
- Added detailed statistics.
- Added custom dashboard.
