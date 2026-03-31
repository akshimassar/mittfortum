# Dashboard

This page describes how the Fortum dashboard works and how to configure it.

## Overview

The dashboard strategy (`custom:fortum-energy`) builds an Electricity view with:

- adaptive consumption graph (Energy-style totals + Fortum overlays)
- custom legend table (consumption and cost breakdown)
- tomorrow price graph

It reads data from the Home Assistant Energy collection and Recorder statistics.

## Strategy Options

- `collection_key` (optional): Energy collection key. Default: `energy_fortum_energy_dashboard`.
- `debug` (optional): enables dashboard debug output in the browser console when `true` (adaptive graph + tomorrow-price card).
- `fortum.metering_point_number` (optional): explicit Fortum metering point number for single strategy.
- `itemization` (optional): list of device itemizations. When present, including an empty list, it fully replaces Energy Dashboard itemization.
- `metering_points` (multipoint): non-empty list of points; each point requires `number` and `itemization`, with optional `name` and optional `address`.

Multipoint point fields:

- `number` (required): metering point number.
- `itemization` (required): itemization list for this point; empty list is allowed.
- `name` (optional): tab title override.
- `address` (optional): used as tab title when `name` is not provided.

Multipoint forecast resolution is strict per point:

- Entity id must exist as `sensor.metering_point_<number>`.
- `price_area` is read only from that entity's attributes.
- Forecast statistic id is derived as `fortum:price_forecast_<price_area_lowercase>`.
- If sensor is missing, `price_area` is missing, or derived statistic has no values, the tomorrow-price card shows an explicit in-card error.

`itemization` item fields:

- `stat_consumption` (required): statistic id for a device/itemized consumption source.
- `name` (optional): display name for the itemization row/series.

Minimal example:

```yaml
title: Fortum
strategy:
  type: custom:fortum-energy
  fortum:
    metering_point_number: "6094111"
  itemization: []
```

## Source Priority

Single strategy resolves two domains independently and only once (dashboard reload required to re-resolve):

1. Fortum source resolution:
   - `strategy.fortum.metering_point_number` when provided.
   - otherwise auto-discovery from Recorder statistics (`fortum:hourly_consumption_*`).
   - if auto-discovery finds multiple Fortum consumption sources, strategy returns an error (single strategy requires exactly one source).
2. Itemization resolution:
   - `strategy.itemization` when provided (including empty list).
   - otherwise `energy/get_prefs` device itemization (`device_consumption`).

Single strategy uses only Fortum grid-import derived statistics (`consumption`, `cost`, `price`, `temperature`) and does not use solar/battery/export flows.

## 15-Minute Scale Behavior

Adaptive graph resolution is chosen by visible range and chart width.

- Buckets can be 15m, 1h, 3h, 6h, 12h, or 1d.
- For sub-hour buckets, device consumption requests `5minute` statistics.
- If any required device series has no sub-hour data, graph falls back to hourly mode.
- Grid flows/cost and Fortum price/temperature overlays are queried hourly and aligned to graph buckets.
- At 15-minute scale, labels and tooltip time ranges include minutes (for example `14:15-14:30`).

## Tomorrow Price Graph Data Source

- The tomorrow-price card reads only Fortum area-scoped forecast statistics with id format `fortum:price_forecast_<area>`.
- Forecast ids are resolved by strategy from Recorder statistic id listing (`recorder/list_statistic_ids`) and passed to the card explicitly.
- Legacy non-area forecast statistic id (`fortum:price_forecast`) is intentionally excluded.
- Multiple detected area forecast series are rendered on a single card.
- If no Fortum forecast statistics are resolved, the card shows an in-card error.

## Tomorrow Price Graph Debugging

- Enable `debug: true` in strategy config to emit browser console diagnostics.
- The future-price card logs `[fortum-energy] future price debug` with discovery/fetch/result status.
- Debug logs are emitted only when result status changes to avoid repeated dumps.
