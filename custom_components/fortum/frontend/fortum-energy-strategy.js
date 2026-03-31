import { FortumEnergyCustomLegendCard } from "/fortum-energy-static/strategy/cards/custom-legend-card.js";
import { FortumEnergyDevicesDetailOverlayCard } from "/fortum-energy-static/strategy/cards/devices-detail-overlay-card.js";
import { FortumEnergyDevicesAdaptiveGraphCard } from "/fortum-energy-static/strategy/cards/devices-adaptive-graph-card.js";
import { FortumEnergySettingsRedirectCard } from "/fortum-energy-static/strategy/cards/settings-redirect-card.js";
import { FortumEnergyQuickRangesCard } from "/fortum-energy-static/strategy/cards/quick-ranges-card.js";
import { FortumEnergySpacerCard } from "/fortum-energy-static/strategy/cards/spacer-card.js";
import { DEFAULT_COLLECTION_KEY, EMPTY_PREFS } from "/fortum-energy-static/strategy/shared/constants.js";
import { fetchEnergyPrefs } from "/fortum-energy-static/strategy/shared/energy-prefs.js";
import { discoverAreaForecastStatisticIds } from "/fortum-energy-static/strategy/shared/forecast-discovery.js";
import {
  computeAxisFractionDigits,
  formatForecastSeriesLabel,
  localize,
} from "/fortum-energy-static/strategy/shared/formatters.js";

const isFortumConsumptionStatId = (statId) =>
  typeof statId === "string" && /^[^:]*fortum:hourly_consumption_/.test(statId);

const hasAnyEnergyPrefs = (prefs) =>
  prefs &&
  (prefs.device_consumption.length > 0 || prefs.energy_sources.length > 0);

const normalizeEnergySourceOverrides = (energySources) => {
  if (!Array.isArray(energySources)) {
    return [];
  }

  return energySources
    .map((entry) => {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      const statEnergyFrom =
        typeof entry.stat_energy_from === "string" ? entry.stat_energy_from.trim() : "";
      if (!statEnergyFrom) {
        return null;
      }
      const statCost = typeof entry.stat_cost === "string" ? entry.stat_cost.trim() : "";
      return {
        stat_energy_from: statEnergyFrom,
        stat_cost: statCost || undefined,
      };
    })
    .filter(Boolean);
};

const getGridImportFlows = (source) => {
  if (Array.isArray(source?.flow_from) && source.flow_from.length) {
    return source.flow_from;
  }
  return [source];
};

const getGridExportFlows = (source) => {
  if (Array.isArray(source?.flow_to) && source.flow_to.length) {
    return source.flow_to;
  }
  return [source];
};

const toFortumPriceStatId = (consumptionStatId) => {
  if (!isFortumConsumptionStatId(consumptionStatId)) {
    return null;
  }
  return consumptionStatId.replace("hourly_consumption_", "hourly_price_");
};

const toFortumTemperatureStatId = (consumptionStatId) => {
  if (!isFortumConsumptionStatId(consumptionStatId)) {
    return null;
  }
  return consumptionStatId.replace("hourly_consumption_", "hourly_temperature_");
};

const toFortumPriceForecastStatId = (priceStatId) => {
  if (typeof priceStatId !== "string" || !priceStatId.includes("hourly_price_")) {
    return null;
  }
  return null;
};

const deriveEnergyRuntimeConfig = ({
  prefs,
  info,
  overrides,
  strictOverride = false,
}) => {
  const safePrefs = prefs || EMPTY_PREFS;
  const safeInfo = info || { cost_sensors: {} };
  const normalizedOverrides = normalizeEnergySourceOverrides(overrides);
  const hasOverrideInput = Array.isArray(overrides);
  const useOverride = strictOverride ? hasOverrideInput : normalizedOverrides.length > 0;

  const flowIds = {
    fromGrid: [],
    toGrid: [],
    solar: [],
    fromBattery: [],
    toBattery: [],
  };
  const overlayIds = {
    importCost: [],
    exportCompensation: [],
    price: [],
    temperature: [],
  };
  const forecastIds = [];
  const issues = [];

  if (useOverride) {
    if (!normalizedOverrides.length) {
      issues.push("override_provided_but_no_valid_energy_sources");
    }
    normalizedOverrides.forEach((source) => {
      flowIds.fromGrid.push(source.stat_energy_from);
      const costId = source.stat_cost || safeInfo.cost_sensors[source.stat_energy_from];
      if (costId) {
        overlayIds.importCost.push(costId);
      }
      const priceId = toFortumPriceStatId(source.stat_energy_from);
      if (priceId) {
        overlayIds.price.push(priceId);
        const forecastId = toFortumPriceForecastStatId(priceId);
        if (forecastId) {
          forecastIds.push(forecastId);
        }
      }
      const temperatureId = toFortumTemperatureStatId(source.stat_energy_from);
      if (temperatureId) {
        overlayIds.temperature.push(temperatureId);
      }
    });
  }

  (safePrefs.energy_sources || []).forEach((source) => {
    if (source.type === "grid") {
      if (!useOverride) {
        getGridImportFlows(source).forEach((flow) => {
          if (!flow?.stat_energy_from) {
            return;
          }
          flowIds.fromGrid.push(flow.stat_energy_from);
          const costId = flow.stat_cost || safeInfo.cost_sensors[flow.stat_energy_from];
          if (costId) {
            overlayIds.importCost.push(costId);
          }
          const priceId = toFortumPriceStatId(flow.stat_energy_from);
          if (priceId) {
            overlayIds.price.push(priceId);
            const forecastId = toFortumPriceForecastStatId(priceId);
            if (forecastId) {
              forecastIds.push(forecastId);
            }
          }
          const temperatureId = toFortumTemperatureStatId(flow.stat_energy_from);
          if (temperatureId) {
            overlayIds.temperature.push(temperatureId);
          }
        });
      }

      getGridExportFlows(source).forEach((flow) => {
        if (!flow?.stat_energy_to) {
          return;
        }
        flowIds.toGrid.push(flow.stat_energy_to);
        const compensationId =
          flow.stat_compensation ||
          flow.stat_cost ||
          safeInfo.cost_sensors[flow.stat_energy_to];
        if (compensationId) {
          overlayIds.exportCompensation.push(compensationId);
        }
      });
      return;
    }

    if (source.type === "solar" && source.stat_energy_from) {
      flowIds.solar.push(source.stat_energy_from);
      return;
    }

    if (source.type === "battery") {
      if (source.stat_energy_from) {
        flowIds.fromBattery.push(source.stat_energy_from);
      }
      if (source.stat_energy_to) {
        flowIds.toBattery.push(source.stat_energy_to);
      }
    }
  });

  return {
    source: useOverride ? "override" : "prefs",
    strictOverride: !!strictOverride,
    hasOverrideInput,
    overridesCount: normalizedOverrides.length,
    issues,
    flowIds: {
      fromGrid: Array.from(new Set(flowIds.fromGrid)),
      toGrid: Array.from(new Set(flowIds.toGrid)),
      solar: Array.from(new Set(flowIds.solar)),
      fromBattery: Array.from(new Set(flowIds.fromBattery)),
      toBattery: Array.from(new Set(flowIds.toBattery)),
    },
    overlayIds: {
      importCost: Array.from(new Set(overlayIds.importCost)),
      exportCompensation: Array.from(new Set(overlayIds.exportCompensation)),
      price: Array.from(new Set(overlayIds.price)),
      temperature: Array.from(new Set(overlayIds.temperature)),
    },
    forecastIds: Array.from(new Set(forecastIds)),
  };
};

const buildSetupView = () => ({
  title: "Setup",
  path: "setup",
  cards: [
    {
      type: "markdown",
      content:
        "No Energy preferences found yet. Open **Settings -> Dashboards -> Energy** and complete setup first.",
    },
  ],
});

const buildSettingsView = (hass) => ({
  title: localize(hass, "ui.panel.config.energy.caption", "Settings"),
  path: "settings",
  icon: "mdi:cog",
  cards: [
    {
      type: "custom:fortum-energy-settings-redirect-card",
    },
  ],
});

const buildElectricityViewConfig = (
  prefs,
  collectionKey,
  hass,
  debug = false,
  energySources = []
) => {
  const view = {
    title: localize(hass, "ui.panel.energy.title.electricity", "Electricity"),
    path: "electricity",
    type: "sections",
    sections: [],
  };

  const mainCards = [];

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  mainCards.push({
    title: localize(
      hass,
      "ui.panel.energy.cards.energy_date_selection_title",
      "Time range"
    ),
    type: "energy-date-selection",
    collection_key: collectionKey,
    disable_compare: true,
    opening_direction: "right",
    vertical_opening_direction: "down",
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:fortum-energy-quick-ranges-card",
    collection_key: collectionKey,
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  if (prefs.device_consumption.length) {
    mainCards.push({
      type: "custom:fortum-energy-devices-adaptive-graph-card",
      collection_key: collectionKey,
      debug,
      energy_sources: energySources,
      grid_options: { columns: 36 },
    });

    mainCards.push({
      title: "Price of Tomorrow",
      type: "custom:fortum-energy-future-price-card",
      collection_key: collectionKey,
      debug,
      energy_sources: energySources,
      grid_options: { columns: 36 },
    });
  }

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
  });

  view.sections.push({
    type: "grid",
    column_span: 3,
    cards: mainCards,
  });

  return view;
};

class FortumEnergyDashboardStrategy extends HTMLElement {
  static async generate(config, hass) {
    try {
      const collectionKey =
        config.collection_key || config.collectionKey || DEFAULT_COLLECTION_KEY;
      const debug = config.debug === true;
      const energySources = normalizeEnergySourceOverrides(config.energy_sources);
      const prefs = await fetchEnergyPrefs(hass);

      if (!hasAnyEnergyPrefs(prefs)) {
        return { views: [buildSetupView(), buildSettingsView(hass)] };
      }

      return {
        views: [
          buildElectricityViewConfig(prefs, collectionKey, hass, debug, energySources),
          buildSettingsView(hass),
        ],
      };
    } catch (err) {
      const message = err && err.message ? err.message : String(err);
      return {
        views: [
          {
            title: "Error",
            path: "error",
            cards: [
              {
                type: "markdown",
                content: `Error loading fortum-energy strategy:\n> ${message}`,
              },
            ],
          },
        ],
      };
    }
  }

  static async generateDashboard(args) {
    return this.generate(args.strategy || {}, args.hass);
  }
}

class FortumEnergyFuturePriceCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._energySourceOverridesInput = this._config.energy_sources;
    this._strictEnergySourceOverrides = Array.isArray(this._energySourceOverridesInput);
    this._debugEnabled = this._config.debug === true;
    if (!this._debugEnabled) {
      this._lastRuntimeConfigSignature = undefined;
      this._lastFuturePriceDebugStatus = undefined;
    }
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._renderBase();
  }

  set hass(hass) {
    this._hass = hass;
    this._trySubscribe();
    this._ensureChart();
    this._scheduleUpdate();
  }

  connectedCallback() {
    if (!this._resizeObserver && typeof ResizeObserver !== "undefined") {
      this._resizeObserver = new ResizeObserver(() => this._scheduleUpdate());
      this._resizeObserver.observe(this);
    }
    this._scheduleNowTick();
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = undefined;
    }
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = undefined;
    }
    this._clearNowTick();
    this._unbindShadeFromChart();
  }

  getCardSize() {
    return 3;
  }

  _renderBase() {
    if (!this.shadowRoot) {
      return;
    }
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { height: 100%; }
        .card-header { padding-bottom: 0; }
        .content { padding: 16px; }
        .content.has-header { padding-top: 0; }
        .empty {
          color: var(--secondary-text-color);
          user-select: text;
          -webkit-user-select: text;
          cursor: text;
          white-space: pre-wrap;
        }
        .chart-wrap {
          position: relative;
          isolation: isolate;
        }
        .chart-wrap ha-chart-base {
          position: relative;
          z-index: 3;
          pointer-events: auto;
        }
        .tomorrow-shade {
          position: absolute;
          pointer-events: none !important;
          user-select: none;
          display: none;
          z-index: 0;
        }
        .now-indicator {
          position: absolute;
          pointer-events: none !important;
          user-select: none;
          display: none;
          z-index: 4;
          border-left: 2px solid color-mix(in srgb, var(--error-color) 80%, white);
        }
        .now-indicator.offscreen {
          border-left-color: transparent;
        }
        .now-indicator-label {
          position: absolute;
          top: 4px;
          left: 6px;
          font-size: var(--ha-font-size-xs);
          font-weight: 600;
          color: color-mix(in srgb, var(--error-color) 80%, white);
          letter-spacing: 0.06em;
          text-transform: uppercase;
          white-space: nowrap;
        }
        .now-indicator-time {
          display: block;
          margin-top: 2px;
          font-size: var(--ha-font-size-2xs);
          font-weight: 500;
          letter-spacing: 0.02em;
          text-transform: none;
        }
        .now-indicator-hint {
          display: block;
          margin-top: 2px;
          font-size: 2.5em;
          line-height: 1;
          font-weight: 700;
          letter-spacing: 0.01em;
          text-transform: none;
        }
        .now-indicator.offscreen-right .now-indicator-label {
          left: auto;
          right: 6px;
          text-align: right;
        }
        .now-indicator.offscreen .now-indicator-time {
          opacity: 0.8;
        }
        .day-shade-label {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          width: calc(100% - 8px);
          text-align: center;
          font-size: var(--ha-font-size-xs);
          color: transparent;
          -webkit-text-stroke: 0;
          text-shadow: 0 0 0 var(--card-background-color);
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          white-space: nowrap;
          pointer-events: none !important;
        }
        .stats {
          margin-top: 12px;
          border-top: 1px solid var(--divider-color);
          padding-top: 10px;
          font-size: var(--ha-font-size-s);
          color: var(--primary-text-color);
        }
        .stats table {
          width: 100%;
          border-collapse: collapse;
          table-layout: fixed;
        }
        .stats th,
        .stats td {
          padding: 4px 0;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .stats th {
          color: var(--secondary-text-color);
          font-weight: 500;
        }
        .stats .series {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }
        .stats .dot {
          width: 10px;
          height: 10px;
          border-radius: 999px;
          border: 1px solid currentColor;
          flex: 0 0 auto;
        }
        .stats .label {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .stats th.num,
        .stats td.num {
          text-align: right;
        }
        .stats tr.toggleable {
          cursor: pointer;
        }
        .stats tr.hidden {
          color: var(--secondary-text-color);
        }
        .stats tr.hidden .dot {
          background: transparent !important;
        }
      </style>
      <ha-card>
        ${this._config?.title ? `<h1 class="card-header">${this._config.title}</h1>` : ""}
        <div class="content ${this._config?.title ? "has-header" : ""}">
          <div id="chart-wrap" class="chart-wrap">
            <ha-chart-base id="chart"></ha-chart-base>
            <div id="today-shade" class="tomorrow-shade">
              <span class="day-shade-label">Today</span>
            </div>
            <div id="tomorrow-shade" class="tomorrow-shade">
              <span class="day-shade-label">Tomorrow</span>
            </div>
            <div id="now-indicator" class="now-indicator">
              <span class="now-indicator-label">Now<span id="now-indicator-time" class="now-indicator-time"></span><span id="now-indicator-hint" class="now-indicator-hint"></span></span>
            </div>
          </div>
          <div id="empty" class="empty" style="display:none;">No data</div>
          <div id="stats" class="stats"></div>
        </div>
      </ha-card>
    `;
    this._ensureChart();
  }

  _ensureChart() {
    if (!this.shadowRoot) {
      return;
    }
    this._chart = this.shadowRoot.querySelector("#chart");
    if (this._chart && this._hass) {
      this._chart.hass = this._hass;
      this._chart.height = "280px";
    }
  }

  _getCollection() {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    return this._hass?.connection?.[`_${collectionKey}`];
  }

  _trySubscribe() {
    const collection = this._getCollection();
    if (!collection || collection === this._collection || !collection.subscribe) {
      return;
    }
    if (this._unsubscribe) {
      this._unsubscribe();
    }
    this._collection = collection;
    this._unsubscribe = collection.subscribe((data) => {
      this._energyData = data;
      this._scheduleUpdate();
    });
  }

  _scheduleUpdate() {
    if (this._updateScheduled) {
      return;
    }
    this._updateScheduled = true;
    requestAnimationFrame(() => {
      this._updateScheduled = false;
      this._updateChart();
    });
  }

  _scheduleNowTick() {
    this._clearNowTick();
    this._nowTickInterval = setInterval(() => {
      this._applyTomorrowShadeGraphic();
    }, 60000);
  }

  _clearNowTick() {
    if (this._nowTickInterval) {
      clearInterval(this._nowTickInterval);
      this._nowTickInterval = undefined;
    }
  }

  _fetchStats(statIds, start, end, period, types) {
    if (!statIds.length) {
      return Promise.resolve({});
    }
    return this._hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      statistic_ids: statIds,
      period,
      types,
    });
  }

  _fetchStatsMetadata(statIds) {
    const ids = Array.from(new Set((statIds || []).filter(Boolean)));
    if (!ids.length) {
      return Promise.resolve({});
    }
    return this._hass
      .callWS({
        type: "recorder/get_statistics_metadata",
        statistic_ids: ids,
      })
      .then((items) => {
        const meta = {};
        (items || []).forEach((item) => {
          if (item?.statistic_id) {
            meta[item.statistic_id] = item;
          }
        });
        return meta;
      });
  }

  _normalizeMaxSeries(series) {
    if (!Array.isArray(series)) {
      return [];
    }
    return series
      .map((point) => {
        const start =
          typeof point?.start === "number"
            ? point.start
            : typeof point?.start === "string"
              ? Date.parse(point.start)
              : NaN;
        const value = Number(point?.max);
        if (!Number.isFinite(start) || !Number.isFinite(value)) {
          return null;
        }
        return [start, value];
      })
      .filter(Boolean)
      .sort((a, b) => a[0] - b[0]);
  }

  _getFixedRange() {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    end.setHours(23, 59, 59, 999);
    return { start, end };
  }

  _formatDate(ts) {
    const lang = this._hass?.locale?.language || "en";
    return new Date(ts).toLocaleDateString(lang, { day: "2-digit", month: "short" });
  }

  _formatHourRange(ts) {
    const start = new Date(ts);
    const end = new Date(ts + 60 * 60 * 1000);
    const pad = (v) => String(v).padStart(2, "0");
    return `${pad(start.getHours())}-${pad(end.getHours())}`;
  }

  _formatBucketLabel(ts) {
    return `${this._formatDate(ts)} ${this._formatHourRange(ts)}`;
  }

  _formatClock(ts) {
    const date = new Date(ts);
    const pad = (value) => String(value).padStart(2, "0");
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  _getNowForecastValue(points) {
    if (!Array.isArray(points) || !points.length) {
      return 0;
    }

    const nowMs = Date.now();
    const nowHourStart = new Date(nowMs);
    nowHourStart.setMinutes(0, 0, 0);
    const nowBucketStartMs = nowHourStart.getTime();

    const exactPoint = points.find((item) => Number(item?.[0]) === nowBucketStartMs);
    if (exactPoint && Number.isFinite(Number(exactPoint[1]))) {
      return Number(exactPoint[1]);
    }

    const latestPastPoint = [...points]
      .filter((item) => Number.isFinite(Number(item?.[0])) && Number(item[0]) <= nowMs)
      .sort((a, b) => Number(b[0]) - Number(a[0]))[0];
    if (latestPastPoint && Number.isFinite(Number(latestPastPoint[1]))) {
      return Number(latestPastPoint[1]);
    }

    const latestPoint = points[points.length - 1];
    return Number.isFinite(Number(latestPoint?.[1])) ? Number(latestPoint[1]) : 0;
  }

  _formatPriceValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(amount);
    return this._priceUnit ? `${formatted} ${this._priceUnit}` : formatted;
  }

  _formatPriceAxisValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const digits = this._priceAxisDigits || 0;
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount);
    const unit = (this._priceUnit || "").split("/")[0].trim();
    return unit ? `${formatted} ${unit}` : formatted;
  }

  _getPriceForecastColor(index = 0) {
    const style = getComputedStyle(this);
    const palette = [
      style.getPropertyValue("--info-color").trim() || "#2f7ed8",
      style.getPropertyValue("--warning-color").trim() || "#f59e0b",
      style.getPropertyValue("--success-color").trim() || "#16a34a",
      style.getPropertyValue("--accent-color").trim() || "#0ea5e9",
      style.getPropertyValue("--error-color").trim() || "#ef4444",
    ];
    return palette[index % palette.length];
  }

  _toggleSeriesVisibility(seriesId) {
    if (!this._hiddenSeriesIds) {
      this._hiddenSeriesIds = new Set();
    }
    if (this._hiddenSeriesIds.has(seriesId)) {
      this._hiddenSeriesIds.delete(seriesId);
    } else {
      this._hiddenSeriesIds.add(seriesId);
    }
    this._applySeriesVisibility();
  }

  _renderLegendTable(rows, hiddenIds) {
    const container = this.shadowRoot?.querySelector("#stats");
    if (!container) {
      return;
    }
    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Series</th>
            <th class="num">Min</th>
            <th class="num">Max</th>
            <th class="num">Avg</th>
            <th class="num">Now</th>
          </tr>
        </thead>
        <tbody>
          ${(rows || [])
            .map(
              (row) => `
            <tr class="${row.id && hiddenIds?.has(row.id) ? "hidden" : ""}">
              <td><span class="series"><span class="dot" style="color: ${row.color}; background-color: ${row.color};"></span><span class="label">${row.name}</span></span></td>
              <td class="num">${this._formatPriceValue(row.min)}</td>
              <td class="num">${this._formatPriceValue(row.max)}</td>
              <td class="num">${this._formatPriceValue(row.avg)}</td>
              <td class="num">${this._formatPriceValue(row.now ?? row.last)}</td>
            </tr>
          `
            )
            .join("")}
        </tbody>
      </table>
    `;
  }

  _applySeriesVisibility() {
    if (!this._chart || !this._allSeries || !this._chartOptions) {
      return;
    }
    const hidden = this._hiddenSeriesIds || new Set();
    const visible = this._allSeries.filter((entry) => !hidden.has(entry.id));
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (emptyEl) {
      emptyEl.style.display = visible.some((entry) => entry.data?.length) ? "none" : "block";
    }
    this._chart.hass = this._hass;
    this._chart.data = visible;
    this._chart.options = this._chartOptions;
    this._chart.requestUpdate?.();
    this._bindShadeToChart();
    requestAnimationFrame(() => this._applyTomorrowShadeGraphic());
    this._renderLegendTable(this._legendRows || [], hidden);
  }

  _bindShadeToChart() {
    if (!this.isConnected) {
      return;
    }
    const ech = this._chart?.chart;
    if (!ech) {
      requestAnimationFrame(() => this._bindShadeToChart());
      return;
    }

    if (this._shadeBoundChart === ech) {
      return;
    }

    this._unbindShadeFromChart();

    this._shadeFinishedHandler = () => this._applyTomorrowShadeGraphic();
    ech.on("finished", this._shadeFinishedHandler);
    this._shadeBoundChart = ech;
  }

  _unbindShadeFromChart() {
    if (this._shadeBoundChart && this._shadeFinishedHandler) {
      this._shadeBoundChart.off("finished", this._shadeFinishedHandler);
    }
    this._shadeBoundChart = undefined;
    this._shadeFinishedHandler = undefined;
  }

  _applyTomorrowShadeGraphic() {
    const ech = this._chart?.chart;
    if (!ech || !Number.isFinite(this._tomorrowStartMs)) {
      return;
    }

    const todayShadeEl = this.shadowRoot?.querySelector("#today-shade");
    const tomorrowShadeEl = this.shadowRoot?.querySelector("#tomorrow-shade");
    const nowIndicatorEl = this.shadowRoot?.querySelector("#now-indicator");
    const nowIndicatorTimeEl = this.shadowRoot?.querySelector("#now-indicator-time");
    const nowIndicatorHintEl = this.shadowRoot?.querySelector("#now-indicator-hint");
    if (!todayShadeEl || !tomorrowShadeEl || !nowIndicatorEl) {
      return;
    }
    const todayLabelEl = todayShadeEl.querySelector(".day-shade-label");
    const tomorrowLabelEl = tomorrowShadeEl.querySelector(".day-shade-label");

    const hideShades = () => {
      todayShadeEl.style.display = "none";
      tomorrowShadeEl.style.display = "none";
      nowIndicatorEl.style.display = "none";
      nowIndicatorEl.classList.remove("offscreen", "offscreen-left", "offscreen-right");
      if (nowIndicatorHintEl) {
        nowIndicatorHintEl.textContent = "";
      }
    };

    if (!Array.isArray(this._allSeries) || !this._allSeries.length) {
      hideShades();
      return;
    }

    const gridComponent = ech.getModel()?.getComponent?.("grid", 0);
    const rect = gridComponent?.coordinateSystem?.getRect?.();
    if (!rect) {
      hideShades();
      return;
    }

    const x = Number(ech.convertToPixel({ xAxisIndex: 0 }, this._tomorrowStartMs));
    if (!Number.isFinite(x)) {
      hideShades();
      return;
    }

    if (rect.width <= 0 || rect.height <= 0) {
      hideShades();
      return;
    }

    const clampedX = Math.max(rect.x, Math.min(rect.x + rect.width, x));
    const todayWidth = Math.max(0, clampedX - rect.x);
    const tomorrowWidth = Math.max(0, rect.x + rect.width - clampedX);
    const isDarkTheme =
      this._hass?.themes?.darkMode ??
      (typeof window !== "undefined" &&
        window.matchMedia?.("(prefers-color-scheme: dark)")?.matches);
    const todayShadeColor = isDarkTheme
      ? "rgba(34, 197, 94, 0.07)"
      : "rgba(148, 163, 184, 0.12)";
    const tomorrowShadeColor = isDarkTheme
      ? "rgba(250, 204, 21, 0.11)"
      : "rgba(100, 116, 139, 0.16)";
    const labelSize = Math.max(12, Math.round(rect.height * 0.25));
    if (todayLabelEl) {
      todayLabelEl.style.fontSize = `${labelSize}px`;
    }
    if (tomorrowLabelEl) {
      tomorrowLabelEl.style.fontSize = `${labelSize}px`;
    }

    if (todayWidth > 0) {
      todayShadeEl.style.display = "block";
      todayShadeEl.style.left = `${rect.x}px`;
      todayShadeEl.style.top = `${rect.y}px`;
      todayShadeEl.style.width = `${todayWidth}px`;
      todayShadeEl.style.height = `${rect.height}px`;
      todayShadeEl.style.background = todayShadeColor;
    } else {
      todayShadeEl.style.display = "none";
    }

    if (tomorrowWidth > 0) {
      tomorrowShadeEl.style.display = "block";
      tomorrowShadeEl.style.left = `${clampedX}px`;
      tomorrowShadeEl.style.top = `${rect.y}px`;
      tomorrowShadeEl.style.width = `${tomorrowWidth}px`;
      tomorrowShadeEl.style.height = `${rect.height}px`;
      tomorrowShadeEl.style.background = tomorrowShadeColor;
    } else {
      tomorrowShadeEl.style.display = "none";
    }

    const nowMs = Date.now();
    if (
      Number.isFinite(this._rangeStartMs) &&
      Number.isFinite(this._rangeEndMs) &&
      nowMs >= this._rangeStartMs &&
      nowMs <= this._rangeEndMs
    ) {
      const nowX = Number(ech.convertToPixel({ xAxisIndex: 0 }, nowMs));
      if (Number.isFinite(nowX)) {
        nowIndicatorEl.style.display = "block";
        nowIndicatorEl.style.top = `${rect.y}px`;
        nowIndicatorEl.style.height = `${rect.height}px`;
        if (nowIndicatorTimeEl) {
          nowIndicatorTimeEl.textContent = this._formatClock(nowMs);
        }
        if (nowX < rect.x) {
          nowIndicatorEl.classList.add("offscreen", "offscreen-left");
          nowIndicatorEl.classList.remove("offscreen-right");
          nowIndicatorEl.style.left = `${rect.x}px`;
          if (nowIndicatorHintEl) {
            nowIndicatorHintEl.textContent = "\u2190";
          }
          return;
        }
        if (nowX > rect.x + rect.width) {
          nowIndicatorEl.classList.add("offscreen", "offscreen-right");
          nowIndicatorEl.classList.remove("offscreen-left");
          nowIndicatorEl.style.left = `${rect.x + rect.width}px`;
          if (nowIndicatorHintEl) {
            nowIndicatorHintEl.textContent = "\u2192";
          }
          return;
        }
        nowIndicatorEl.classList.remove("offscreen", "offscreen-left", "offscreen-right");
        nowIndicatorEl.style.left = `${nowX}px`;
        if (nowIndicatorHintEl) {
          nowIndicatorHintEl.textContent = "";
        }
      } else {
        nowIndicatorEl.style.display = "none";
        nowIndicatorEl.classList.remove("offscreen", "offscreen-left", "offscreen-right");
        if (nowIndicatorHintEl) {
          nowIndicatorHintEl.textContent = "";
        }
      }
    } else {
      nowIndicatorEl.style.display = "none";
      nowIndicatorEl.classList.remove("offscreen", "offscreen-left", "offscreen-right");
      if (nowIndicatorHintEl) {
        nowIndicatorHintEl.textContent = "";
      }
    }
  }

  _deriveRuntimeConfig(data) {
    return deriveEnergyRuntimeConfig({
      prefs: data?.prefs,
      info: data?.info,
      overrides: this._energySourceOverridesInput,
      strictOverride: this._strictEnergySourceOverrides,
    });
  }

  _logRuntimeConfigDebug(runtimeConfig) {
    if (!this._debugEnabled) {
      return;
    }
    const payload = {
      source: runtimeConfig?.source || "prefs",
      strictOverride: !!runtimeConfig?.strictOverride,
      overridesCount: runtimeConfig?.overridesCount || 0,
      issues: runtimeConfig?.issues || [],
      forecastIds: runtimeConfig?.forecastIds || [],
      overlayIds: runtimeConfig?.overlayIds || {
        importCost: [],
        exportCompensation: [],
        price: [],
        temperature: [],
      },
      flowIds: runtimeConfig?.flowIds || {
        fromGrid: [],
        toGrid: [],
        solar: [],
        fromBattery: [],
        toBattery: [],
      },
    };
    const signature = JSON.stringify(payload);
    if (signature === this._lastRuntimeConfigSignature) {
      return;
    }
    this._lastRuntimeConfigSignature = signature;
    console.log("[fortum-energy] runtime config", payload);
  }

  _formatDebugTime(value) {
    if (!Number.isFinite(value)) {
      return null;
    }
    const date = new Date(value);
    return {
      ts: value,
      iso: date.toISOString(),
      local: date.toString(),
    };
  }

  _logFuturePriceDebug(payload) {
    if (!this._debugEnabled) {
      return;
    }
    const status = payload?.result?.status || "unknown";
    if (status === this._lastFuturePriceDebugStatus) {
      return;
    }
    this._lastFuturePriceDebugStatus = status;
    if (status !== "ok") {
      console.warn("[fortum-energy] future price debug", payload);
      return;
    }
    console.log("[fortum-energy] future price debug", payload);
  }

  _showCardError(message) {
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (emptyEl) {
      emptyEl.textContent = message;
      emptyEl.style.display = "block";
    }
    this._allSeries = [];
    this._chartOptions = {
      legend: { show: false, type: "custom" },
      xAxis: { type: "time" },
      yAxis: [{ type: "value", position: "right", splitLine: { show: false } }],
      tooltip: { show: false },
    };
    this._legendRows = [];
    if (this._chart) {
      this._chart.hass = this._hass;
      this._chart.data = [];
      this._chart.options = this._chartOptions;
      this._chart.requestUpdate?.();
    }
    const todayShadeEl = this.shadowRoot?.querySelector("#today-shade");
    const tomorrowShadeEl = this.shadowRoot?.querySelector("#tomorrow-shade");
    const nowIndicatorEl = this.shadowRoot?.querySelector("#now-indicator");
    if (todayShadeEl) todayShadeEl.style.display = "none";
    if (tomorrowShadeEl) tomorrowShadeEl.style.display = "none";
    if (nowIndicatorEl) nowIndicatorEl.style.display = "none";
    this._renderLegendTable([], this._hiddenSeriesIds || new Set());
  }

  async _updateChart() {
    if (!this._hass) {
      return;
    }
    this._ensureChart();
    if (!this._chart) {
      return;
    }

    const data = this._energyData || this._collection?.state;
    const runtimeConfig = this._deriveRuntimeConfig(data);
    this._logRuntimeConfigDebug(runtimeConfig);
    const debugPayload = {
      runtimeConfig: {
        source: runtimeConfig?.source || "prefs",
        strictOverride: !!runtimeConfig?.strictOverride,
        overridesCount: runtimeConfig?.overridesCount || 0,
        issues: runtimeConfig?.issues || [],
      },
      discovery: {
        status: "pending",
        forecastIds: [],
      },
      fetch: {
        requestedIds: [],
        pointCounts: {},
        metadataUnit: "",
      },
      result: {
        status: "pending",
      },
    };
    let forecastIds = [];
    try {
      const discoveredAreaForecastIds = await discoverAreaForecastStatisticIds(this._hass);
      if (Array.isArray(discoveredAreaForecastIds) && discoveredAreaForecastIds.length) {
        forecastIds = discoveredAreaForecastIds;
      }
      debugPayload.discovery = {
        status: "ok",
        forecastIds,
      };
    } catch (err) {
      // Use only explicit area-coded forecast statistic ids.
      debugPayload.discovery = {
        status: "error",
        forecastIds: [],
        errorCode: err?.code || err?.error?.code || null,
        errorMessage: err?.message || err?.error?.message || String(err),
      };
    }

    if (!forecastIds.length) {
      debugPayload.result = {
        status: "no_area_ids",
        message: "No area-specific price forecast statistics found.",
      };
      this._logFuturePriceDebug(debugPayload);
      this._showCardError("No area-specific price forecast statistics found.");
      return;
    }

    const { start, end } = this._getFixedRange();
    this._rangeStartMs = start.getTime();
    this._rangeEndMs = end.getTime();
    const token = (this._token || 0) + 1;
    this._token = token;

    debugPayload.range = {
      start: this._formatDebugTime(start.getTime()),
      end: this._formatDebugTime(end.getTime()),
    };
    debugPayload.fetch.requestedIds = forecastIds;

    const raw = await this._fetchStats(forecastIds, start, end, "hour", ["max"]);
    if (this._token !== token) {
      return;
    }
    const pointsByStatId = {};
    forecastIds.forEach((statId) => {
      pointsByStatId[statId] = this._normalizeMaxSeries(raw?.[statId]);
      debugPayload.fetch.pointCounts[statId] = pointsByStatId[statId].length;
    });

    this._priceUnit = "";
    let meta = {};
    try {
      meta = await this._fetchStatsMetadata(forecastIds);
      if (this._token !== token) {
        return;
      }
      const unit = forecastIds
        .map((statId) => meta?.[statId]?.statistics_unit_of_measurement)
        .find((value) => typeof value === "string");
      this._priceUnit = typeof unit === "string" ? unit : "";
      debugPayload.fetch.metadataUnit = this._priceUnit;
    } catch (_err) {
      this._priceUnit = "";
      meta = {};
      debugPayload.fetch.metadataError = true;
    }

    const series = [];
    const legendRows = [];
    const values = [];
    forecastIds.forEach((statId, index) => {
      const points = pointsByStatId[statId] || [];
      const color = this._getPriceForecastColor(index);
      const seriesId = `future-price-overlay-${index}`;
      const seriesName = formatForecastSeriesLabel(statId, index);
      const pointValues = points
        .map((item) => Number(item[1]))
        .filter((v) => Number.isFinite(v));
      values.push(...pointValues);
      series.push({
        id: seriesId,
        name: seriesName,
        type: "line",
        smooth: 0.05,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 0,
        z: 10,
        lineStyle: {
          width: 2,
          type: "dashed",
          color,
        },
        itemStyle: {
          color,
        },
        data: points,
      });
      legendRows.push({
        id: seriesId,
        name: seriesName,
        color,
        min: pointValues.length ? Math.min(...pointValues) : 0,
        max: pointValues.length ? Math.max(...pointValues) : 0,
        avg: pointValues.length
          ? pointValues.reduce((acc, v) => acc + v, 0) / pointValues.length
          : 0,
        now: this._getNowForecastValue(points),
      });
    });

    if (!series.some((entry) => Array.isArray(entry.data) && entry.data.length)) {
      debugPayload.result = {
        status: "no_points",
        message: "No forecast price data available for detected price areas.",
      };
      this._logFuturePriceDebug(debugPayload);
      this._showCardError("No forecast price data available for detected price areas.");
      return;
    }

    this._priceAxisDigits = computeAxisFractionDigits(values);
    const tomorrowStart = new Date(start);
    tomorrowStart.setDate(tomorrowStart.getDate() + 1);

    const options = {
      grid: { top: 20, bottom: 0, left: 1, right: 1, containLabel: true },
      legend: {
        show: false,
        type: "custom",
      },
      xAxis: {
        type: "time",
        min: start,
        max: end,
        axisLabel: {
          formatter: (value) => this._formatClock(Number(value)),
        },
      },
      yAxis: [
        {
          type: "value",
          position: "right",
          splitLine: { show: false },
          axisLabel: {
            formatter: (value) => this._formatPriceAxisValue(value),
          },
        },
      ],
      tooltip: {
        show: true,
        trigger: "axis",
        formatter: (params) => {
          const rows = Array.isArray(params) ? params : [params];
          if (!rows.length) {
            return "";
          }
          const ts = Array.isArray(rows[0].value) ? rows[0].value[0] : rows[0].value;
          const title = this._formatClock(Number(ts));
          const lines = rows
            .filter((row) => Array.isArray(row.value))
            .map((row) => {
              const value = Number(row.value[1]);
              return `${row.marker} ${row.seriesName}: <div style="direction:ltr; display: inline;">${this._formatPriceValue(value)}</div>`;
            })
            .join("<br>");
          return `<h4 style="text-align: center; margin: 0;">${title}</h4>${lines}`;
        },
      },
    };

    this._allSeries = series;
    this._chartOptions = options;
    this._legendRows = legendRows;
    this._tomorrowStartMs = tomorrowStart.getTime();
    debugPayload.result = {
      status: "ok",
      seriesCount: series.length,
      legendRows: legendRows.map((row) => row.id),
    };
    this._logFuturePriceDebug(debugPayload);
    this._applySeriesVisibility();
  }
}

const registerIfNeeded = (tag, klass) => {
  if (typeof customElements === "undefined") {
    return;
  }
  if (!customElements.get(tag)) {
    customElements.define(tag, klass);
  }
};

if (typeof process !== "undefined" && process?.versions?.node) {
  globalThis.__fortumEnergyStrategyTestHooks = {
    normalizeEnergySourceOverrides,
    deriveEnergyRuntimeConfig,
  };
}

registerIfNeeded(
  "fortum-energy-custom-legend-card",
  FortumEnergyCustomLegendCard
);
registerIfNeeded("fortum-energy-spacer-card", FortumEnergySpacerCard);
registerIfNeeded("fortum-energy-quick-ranges-card", FortumEnergyQuickRangesCard);
registerIfNeeded(
  "fortum-energy-devices-detail-overlay-card",
  FortumEnergyDevicesDetailOverlayCard
);
registerIfNeeded(
  "fortum-energy-devices-adaptive-graph-card",
  FortumEnergyDevicesAdaptiveGraphCard
);
registerIfNeeded("fortum-energy-future-price-card", FortumEnergyFuturePriceCard);
registerIfNeeded("fortum-energy-settings-redirect-card", FortumEnergySettingsRedirectCard);
try {
  registerIfNeeded("ll-strategy-dashboard-fortum-energy", FortumEnergyDashboardStrategy);
} catch (err) {
  console.error("[fortum-energy] strategy registration failed", err);
}
