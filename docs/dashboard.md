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
- `debug` (optional): enables adaptive graph debug output when `true`.
- `energy_sources` (optional): flat list of grid import mappings used as primary source for import energy/cost.

`energy_sources` item fields:

- `stat_energy_from` (required): statistic/entity id for grid import energy.
- `stat_cost` (optional): statistic/entity id for import cost.

Minimal example:

```yaml
title: Fortum
strategy:
  type: custom:fortum-energy
  energy_sources:
    - stat_energy_from: sensor.grid_import_energy
      stat_cost: sensor.grid_import_cost
```

## Source Priority

For adaptive graph and custom legend import calculations:

1. If `strategy.energy_sources` is present and non-empty, it is used first.
2. Otherwise, the dashboard uses Energy preferences (`energy/get_prefs`).

Export/compensation, solar, and battery data still come from Energy preferences.

## 15-Minute Scale Behavior

Adaptive graph resolution is chosen by visible range and chart width.

- Buckets can be 15m, 1h, 3h, 6h, 12h, or 1d.
- For sub-hour buckets, device consumption requests `5minute` statistics.
- If any required device series has no sub-hour data, graph falls back to hourly mode.
- Grid flows/cost and Fortum price/temperature overlays are queried hourly and aligned to graph buckets.
- At 15-minute scale, labels and tooltip time ranges include minutes (for example `14:15-14:30`).

## Tomorrow Price Graph Data Source

- The tomorrow-price card reads only area-scoped forecast statistics with id format `fortum:price_forecast_<area>`.
- Legacy non-area forecast statistic id (`fortum:price_forecast`) is intentionally excluded.
- Multiple detected area forecast series are rendered on a single card.
- If no area-scoped forecast statistics are available, the card shows no forecast graph.
