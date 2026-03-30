import { FortumEnergyCustomLegendCard } from "/fortum-energy-static/strategy/cards/custom-legend-card.js";
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

class FortumEnergyDevicesDetailOverlayCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `
        <style>
          :host {
            display: block;
            height: 100%;
          }
          .container {
            height: 100%;
          }
        </style>
        <div class="container"></div>
      `;
    }
    this._ensureInnerCard();
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureInnerCard();
    if (this._innerCard) {
      this._innerCard.hass = hass;
    }
    this._subscribeCollection();
    if (!this._overlayInitialized) {
      this._overlayInitialized = true;
      this._scheduleOverlayApply();
    }
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = undefined;
    }
  }

  getCardSize() {
    return 3;
  }

  _ensureInnerCard() {
    if (!this.shadowRoot || this._innerCard) {
      return;
    }
    const container = this.shadowRoot.querySelector(".container");
    if (!container) {
      return;
    }

    this._innerCard = document.createElement("hui-card");
    this._innerCard.config = {
      ...this._config,
      type: "energy-devices-detail-graph",
    };
    if (this._hass) {
      this._innerCard.hass = this._hass;
    }
    container.appendChild(this._innerCard);
  }

  _subscribeCollection() {
    const key = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    const collection = this._hass?.connection?.[`_${key}`];
    if (!collection || collection === this._collection || !collection.subscribe) {
      return;
    }

    if (this._unsubscribe) {
      this._unsubscribe();
    }

    this._collection = collection;
    this._unsubscribe = collection.subscribe((data) => {
      this._energyData = data;
      this._scheduleOverlayApply();
    });
  }

  _scheduleOverlayApply() {
    if (this._overlayScheduled) {
      return;
    }
    this._overlayScheduled = true;
    requestAnimationFrame(() => {
      this._overlayScheduled = false;
      this._applyCostOverlay();
    });
  }

  _collectCostByTimestamp(data) {
    const totals = {};
    const prefs = data?.prefs || EMPTY_PREFS;
    const stats = data?.stats || {};
    const statsMetadata = data?.statsMetadata || {};
    const info = data?.info || { cost_sensors: {} };
    const costStatIds = new Set();

    const addStat = (statId, sign = 1) => {
      if (!statId || !stats[statId]) {
        return;
      }
      costStatIds.add(statId);
      stats[statId].forEach((point) => {
        if (point.change === null || point.change === undefined) {
          return;
        }
        totals[point.start] = (totals[point.start] || 0) + point.change * sign;
      });
    };

    prefs.energy_sources.forEach((source) => {
      if (source.type !== "grid") {
        return;
      }

      const importFlows = Array.isArray(source.flow_from)
        ? source.flow_from
        : [source];
      importFlows.forEach((flow) => {
        if (!flow.stat_energy_from) {
          return;
        }
        const costStatId = flow.stat_cost || info.cost_sensors[flow.stat_energy_from];
        addStat(costStatId, 1);
      });

      const exportFlows = Array.isArray(source.flow_to) ? source.flow_to : [source];
      exportFlows.forEach((flow) => {
        if (!flow.stat_energy_to) {
          return;
        }
        const compensationStatId =
          flow.stat_compensation ||
          flow.stat_cost ||
          info.cost_sensors[flow.stat_energy_to];
        addStat(compensationStatId, -1);
      });
    });

    for (const statId of costStatIds) {
      const unit = statsMetadata?.[statId]?.statistics_unit_of_measurement;
      if (unit) {
        this._costUnit = unit;
        break;
      }
    }

    return Object.keys(totals)
      .map((ts) => [Number(ts), totals[ts]])
      .sort((a, b) => a[0] - b[0]);
  }

  _toFortumPriceStatId(consumptionStatId) {
    if (!isFortumConsumptionStatId(consumptionStatId)) {
      return null;
    }
    return consumptionStatId.replace("hourly_consumption_", "hourly_price_");
  }

  _getStatsTimeBounds(data) {
    const start = data?.start instanceof Date ? data.start.getTime() : NaN;
    const end = data?.end instanceof Date ? data.end.getTime() : NaN;

    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      return { start, end };
    }

    if (Number.isFinite(start)) {
      return { start, end: Date.now() };
    }

    return null;
  }

  _normalizeExternalStats(series) {
    if (!Array.isArray(series)) {
      return [];
    }

    return series
      .map((point) => {
        const rawStart = point?.start;
        const rawEnd = point?.end;
        const start =
          typeof rawStart === "number"
            ? rawStart
            : typeof rawStart === "string"
              ? Date.parse(rawStart)
              : NaN;
        const end =
          typeof rawEnd === "number"
            ? rawEnd
            : typeof rawEnd === "string"
              ? Date.parse(rawEnd)
              : NaN;

        if (!Number.isFinite(start)) {
          return null;
        }

        const normalizeNullable = (value) =>
          value === null || value === undefined ? null : Number(value);

        return {
          start,
          end: Number.isFinite(end) ? end : start,
          change: normalizeNullable(point?.change),
          sum: normalizeNullable(point?.sum),
          mean: normalizeNullable(point?.mean),
          min: normalizeNullable(point?.min),
          max: normalizeNullable(point?.max),
          state: normalizeNullable(point?.state),
          last_reset: normalizeNullable(point?.last_reset),
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start);
  }

  _collectDetailStatIds(data) {
    return Object.keys(data?.stats || {}).filter(Boolean);
  }

  _ensureExternalDetailStats(data, onReady) {
    if (!this._hass || !data) {
      return;
    }

    const bounds = this._getStatsTimeBounds(data);
    if (!bounds) {
      return;
    }

    const statIds = this._collectDetailStatIds(data);
    if (!statIds.length) {
      return;
    }

    const sortedIds = [...new Set(statIds)].sort();
    const rangeKey = `${bounds.start}:${bounds.end}:${sortedIds.join("|")}`;

    if (this._externalDetailRangeKey !== rangeKey) {
      this._externalDetailRangeKey = rangeKey;
      this._externalDetailStats = {};
      this._externalDetailInflight = new Set();
    }
    if (!this._externalDetailInflight) {
      this._externalDetailInflight = new Set();
    }

    const missingIds = sortedIds.filter(
      (id) => !this._externalDetailStats?.[id] && !this._externalDetailInflight?.has(id)
    );

    if (!missingIds.length) {
      return;
    }

    missingIds.forEach((id) => this._externalDetailInflight.add(id));

    this._hass
      .callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(bounds.start).toISOString(),
        end_time: new Date(bounds.end).toISOString(),
        statistic_ids: missingIds,
        period: "hour",
        types: ["change", "sum", "state", "mean", "min", "max", "last_reset"],
      })
      .then((result) => {
        if (this._externalDetailRangeKey !== rangeKey) {
          return;
        }
        const next = { ...(this._externalDetailStats || {}) };
        missingIds.forEach((id) => {
          next[id] = this._normalizeExternalStats(result?.[id]);
        });
        this._externalDetailStats = next;
        if (typeof onReady === "function") {
          onReady();
        }
      })
      .catch((err) => {
        console.warn("[fortum-energy] detail statistics fetch failed", err);
      })
      .finally(() => {
        missingIds.forEach((id) => this._externalDetailInflight.delete(id));
      });
  }

  _withHourlyDetailStats(data, onReady) {
    if (!data) {
      return data;
    }

    this._ensureExternalDetailStats(data, onReady);

    if (!this._externalDetailStats || !Object.keys(this._externalDetailStats).length) {
      return data;
    }

    return {
      ...data,
      stats: {
        ...(data.stats || {}),
        ...this._externalDetailStats,
      },
    };
  }

  _normalizeExternalPriceSeries(series) {
    if (!Array.isArray(series)) {
      return [];
    }

    return series
      .map((point) => {
        const rawStart = point?.start;
        const start =
          typeof rawStart === "number"
            ? rawStart
            : typeof rawStart === "string"
              ? Date.parse(rawStart)
              : NaN;
        const value =
          point?.mean !== undefined && point?.mean !== null
            ? Number(point.mean)
            : point?.state !== undefined && point?.state !== null
              ? Number(point.state)
              : null;

        if (!Number.isFinite(start) || !Number.isFinite(value)) {
          return null;
        }

        return {
          start,
          change: value,
        };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start);
  }

  _ensureExternalPriceMetadata(statIds) {
    if (!this._hass || !statIds.length) {
      return;
    }

    if (!this._externalPriceMeta) {
      this._externalPriceMeta = {};
    }
    if (!this._externalPriceMetaInflight) {
      this._externalPriceMetaInflight = new Set();
    }

    const missingIds = statIds.filter(
      (id) =>
        id &&
        !this._externalPriceMeta?.[id] &&
        !this._externalPriceMetaInflight?.has(id)
    );

    if (!missingIds.length) {
      return;
    }

    missingIds.forEach((id) => this._externalPriceMetaInflight.add(id));

    this._hass
      .callWS({
        type: "recorder/get_statistics_metadata",
        statistic_ids: missingIds,
      })
      .then((result) => {
        const next = { ...(this._externalPriceMeta || {}) };
        result?.forEach((item) => {
          if (item?.statistic_id) {
            next[item.statistic_id] = item;
          }
        });
        this._externalPriceMeta = next;
      })
      .catch((err) => {
        console.warn("[fortum-energy] price metadata fetch failed", err);
      })
      .finally(() => {
        missingIds.forEach((id) => this._externalPriceMetaInflight.delete(id));
      });
  }

  _ensureExternalPriceStats(statIds, data) {
    if (!this._hass || !statIds.length) {
      return;
    }

    const bounds = this._getStatsTimeBounds(data);
    if (!bounds) {
      return;
    }

    const rangeKey = `${bounds.start}:${bounds.end}`;
    if (this._externalPriceRangeKey !== rangeKey) {
      this._externalPriceRangeKey = rangeKey;
      this._externalPriceStats = {};
      this._externalPriceInflight = new Set();
    }
    if (!this._externalPriceInflight) {
      this._externalPriceInflight = new Set();
    }

    const missingIds = statIds.filter(
      (id) =>
        id &&
        !this._externalPriceStats?.[id] &&
        !this._externalPriceInflight?.has(id)
    );

    if (!missingIds.length) {
      return;
    }

    missingIds.forEach((id) => this._externalPriceInflight.add(id));

    this._hass
      .callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(bounds.start).toISOString(),
        end_time: new Date(bounds.end).toISOString(),
        statistic_ids: missingIds,
        period: "hour",
      })
      .then((result) => {
        if (this._externalPriceRangeKey !== rangeKey) {
          return;
        }
        const next = { ...(this._externalPriceStats || {}) };
        missingIds.forEach((id) => {
          next[id] = this._normalizeExternalPriceSeries(result?.[id]);
        });
        this._externalPriceStats = next;
        this._scheduleOverlayApply();
      })
      .catch((err) => {
        console.warn("[fortum-energy] price statistics fetch failed", err);
      })
      .finally(() => {
        missingIds.forEach((id) => this._externalPriceInflight.delete(id));
      });
  }

  _collectPriceByTimestamp(data) {
    const totals = {};
    const prefs = data?.prefs || EMPTY_PREFS;
    const candidateIds = [];
    const foundIds = [];

    const addStat = (statId) => {
      if (!statId) {
        return;
      }
      const series = this._externalPriceStats?.[statId];
      if (!series) {
        return;
      }
      foundIds.push(statId);
      series.forEach((point) => {
        if (point.change === null || point.change === undefined) {
          return;
        }
        totals[point.start] = (totals[point.start] || 0) + point.change;
      });
    };

    prefs.energy_sources.forEach((source) => {
      if (source.type !== "grid") {
        return;
      }

      const importFlows = Array.isArray(source.flow_from)
        ? source.flow_from
        : [source];

      importFlows.forEach((flow) => {
        const priceStatId = this._toFortumPriceStatId(flow.stat_energy_from);
        if (priceStatId) {
          candidateIds.push(priceStatId);
        }
        addStat(priceStatId);
      });
    });

    const uniqueCandidateIds = Array.from(new Set(candidateIds));
    this._ensureExternalPriceStats(uniqueCandidateIds, data);
    this._ensureExternalPriceMetadata(uniqueCandidateIds);

    const foundId = foundIds[0];
    const unit = foundId
      ? this._externalPriceMeta?.[foundId]?.statistics_unit_of_measurement
      : undefined;
    this._priceUnit = unit || this._priceUnit || "";

    return Object.keys(totals)
      .map((ts) => [Number(ts), totals[ts]])
      .sort((a, b) => a[0] - b[0]);
  }

  _getOverlayColor() {
    const value = getComputedStyle(this).getPropertyValue("--warning-color").trim();
    return value || "#f59f00";
  }

  _getPriceOverlayColor() {
    const value = getComputedStyle(this).getPropertyValue("--info-color").trim();
    return value || "#2f7ed8";
  }

  _formatCost(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const unit = this._costUnit || this._hass?.config?.currency || "EUR";
    if (/^[A-Z]{3}$/.test(unit)) {
      return new Intl.NumberFormat(lang, {
        style: "currency",
        currency: unit,
        maximumFractionDigits: 2,
      }).format(amount);
    }
    const formatted = new Intl.NumberFormat(lang, {
      maximumFractionDigits: 2,
    }).format(amount);
    return `${formatted} ${unit}`;
  }

  _formatPrice(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(amount);
    return `${formatted} ${this._priceUnit || "EUR/kWh"}`;
  }

  _escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  _applyOverlayToDetailCard(detailCard, data) {
    const costSeriesData = this._collectCostByTimestamp(data);
    const priceSeriesData = this._collectPriceByTimestamp(data);
    if (
      (!costSeriesData.length && !priceSeriesData.length) ||
      !Array.isArray(detailCard._chartData)
    ) {
      return;
    }

    const costColor = this._getOverlayColor();
    const priceColor = this._getPriceOverlayColor();

    detailCard._chartData = detailCard._chartData
      .filter(
        (series) =>
          series.id !== "fortum-energy-cost-overlay" &&
          series.id !== "fortum-energy-price-overlay"
      );

    if (costSeriesData.length) {
      detailCard._chartData = detailCard._chartData.concat({
        id: "fortum-energy-cost-overlay",
        name: "Cost",
        type: "line",
        smooth: 0.2,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 1,
        z: 80,
        lineStyle: {
          width: 2,
          color: costColor,
        },
        itemStyle: {
          color: costColor,
        },
        tooltip: {
          valueFormatter: (value) => this._formatCost(value),
        },
        data: costSeriesData,
      });
    }

    if (priceSeriesData.length) {
      detailCard._chartData = detailCard._chartData.concat({
        id: "fortum-energy-price-overlay",
        name: "Price",
        type: "line",
        smooth: 0.05,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 2,
        z: 79,
        lineStyle: {
          width: 2,
          type: "dashed",
          color: priceColor,
        },
        itemStyle: {
          color: priceColor,
        },
        data: priceSeriesData,
      });
    }

    if (Array.isArray(detailCard._legendData)) {
      const legendWithoutOverlay = detailCard._legendData.filter(
        (item) =>
          item.id !== "fortum-energy-cost-overlay" &&
          item.id !== "fortum-energy-price-overlay"
      );
      if (costSeriesData.length) {
        legendWithoutOverlay.push({
          id: "fortum-energy-cost-overlay",
          secondaryIds: [],
          name: "Cost",
          itemStyle: {
            color: costColor,
            borderColor: costColor,
          },
        });
      }
      if (priceSeriesData.length) {
        legendWithoutOverlay.push({
          id: "fortum-energy-price-overlay",
          secondaryIds: [],
          name: "Price",
          itemStyle: {
            color: priceColor,
            borderColor: priceColor,
          },
        });
      }
      detailCard._legendData = legendWithoutOverlay;
    }

    if (typeof detailCard.requestUpdate === "function") {
      detailCard.requestUpdate();
    }
  }

  _applyCostOverlay() {
    const detailCard = this._innerCard?.querySelector(
      "hui-energy-devices-detail-graph-card"
    );
    if (!detailCard) {
      return;
    }

    const data = this._energyData || this._collection?.state;
    if (!data) {
      return;
    }

    if (!detailCard.__myEnergyOverlayPatched) {
      detailCard.__myEnergyOverlayPatched = true;

      const originalCreateOptions = detailCard._createOptions?.bind(detailCard);
      if (originalCreateOptions) {
        detailCard._createOptions = (...args) => {
          const options = originalCreateOptions(...args);
          const primaryYAxis = Array.isArray(options?.yAxis)
            ? options.yAxis[0] || { type: "value" }
            : options?.yAxis || { type: "value" };

          const secondaryYAxis = {
            type: "value",
            position: "right",
            splitLine: { show: false },
            axisLabel: {
              formatter: (value) => this._formatCost(value),
            },
          };

          const tertiaryYAxis = {
            type: "value",
            position: "right",
            offset: 56,
            splitLine: { show: false },
            axisLabel: {
              formatter: (value) => this._formatPrice(value),
            },
          };

          const originalTooltipFormatter = options?.tooltip?.formatter;
          const tooltip = {
            ...(options?.tooltip || {}),
            formatter: (params) => {
              const base =
                typeof originalTooltipFormatter === "function"
                  ? originalTooltipFormatter(params)
                  : originalTooltipFormatter;

              if (typeof base !== "string") {
                return base;
              }

              const rows = Array.isArray(params) ? params : [params];
              let out = base;
              rows.forEach((row) => {
                if (
                  !row ||
                  (row.seriesId !== "fortum-energy-cost-overlay" &&
                    row.seriesId !== "fortum-energy-price-overlay")
                ) {
                  return;
                }
                const label = row.seriesName || "Cost";
                const y = Array.isArray(row.value) ? Number(row.value[1] || 0) : 0;
                const valueText =
                  row.seriesId === "fortum-energy-price-overlay"
                    ? this._formatPrice(y)
                    : this._formatCost(y);
                const replacement = `${label}: <div style="direction:ltr; display: inline;">${valueText}</div>`;
                const pattern = new RegExp(
                  `${this._escapeRegExp(label)}: <div style=\"direction:ltr; display: inline;\">[^<]*?<\\/div>`
                );
                out = out.replace(pattern, replacement);
              });
              return out;
            },
          };

          const legend = options?.legend
            ? {
                ...options.legend,
                data: Array.isArray(detailCard._legendData)
                  ? detailCard._legendData
                  : options.legend.data,
              }
            : options?.legend;

          return {
            ...options,
            tooltip,
            legend,
            yAxis: [primaryYAxis, secondaryYAxis, tertiaryYAxis],
          };
        };
      }

      const originalProcess = detailCard._processStatistics?.bind(detailCard);
      if (originalProcess) {
        detailCard._processStatistics = () => {
          const latestData = this._energyData || this._collection?.state || detailCard._data;
          originalProcess();

          if (latestData) {
            this._applyOverlayToDetailCard(detailCard, latestData);
          }
        };
      }
    }

    this._applyOverlayToDetailCard(detailCard, data);
  }
}

class FortumEnergyDevicesAdaptiveGraphCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._energySourceOverridesInput = this._config.energy_sources;
    this._strictEnergySourceOverrides = Array.isArray(this._energySourceOverridesInput);
    this._debugEnabled = this._config.debug === true;
    if (!this._debugEnabled) {
      this._lastAdaptiveDebugSignature = undefined;
    }
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._renderBase();
    this._trySubscribe();
  }

  set hass(hass) {
    this._hass = hass;
    this._trySubscribe();
    this._ensureChart();
    this._scheduleUpdate();
  }

  connectedCallback() {
    this._ensureResizeObserver();
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
  }

  getCardSize() {
    return 3;
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

  _ensureResizeObserver() {
    if (this._resizeObserver || typeof ResizeObserver === "undefined") {
      return;
    }
    this._resizeObserver = new ResizeObserver(() => this._scheduleUpdate());
    this._resizeObserver.observe(this);
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
        .consumption-stats {
          margin-top: 12px;
          border-top: 1px solid var(--divider-color);
          padding-top: 10px;
          font-size: var(--ha-font-size-s);
          color: var(--primary-text-color);
        }
        .consumption-stats table {
          width: 100%;
          border-collapse: collapse;
          table-layout: fixed;
        }
        .consumption-stats th,
        .consumption-stats td {
          padding: 4px 0;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .consumption-stats th {
          color: var(--secondary-text-color);
          font-weight: 500;
        }
        .consumption-stats .series {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }
        .consumption-stats .dot {
          width: 10px;
          height: 10px;
          border-radius: 999px;
          border: 1px solid currentColor;
          flex: 0 0 auto;
        }
        .consumption-stats .label {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .consumption-stats th.num,
        .consumption-stats td.num {
          text-align: right;
        }
        .consumption-stats tr.toggleable {
          cursor: pointer;
        }
        .consumption-stats tr.hidden {
          color: var(--secondary-text-color);
        }
        .consumption-stats tr.hidden .dot {
          background: transparent !important;
        }
      </style>
      <ha-card>
        ${this._config?.title ? `<h1 class="card-header">${this._config.title}</h1>` : ""}
        <div class="content ${this._config?.title ? "has-header" : ""}">
          <ha-chart-base id="chart"></ha-chart-base>
          <div id="empty" class="empty" style="display:none;">No data</div>
          <div id="consumption-stats" class="consumption-stats"></div>
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
      this._chart.height = "320px";
    }
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

  _getBounds(data) {
    const start = data?.start instanceof Date ? data.start : null;
    const end = data?.end instanceof Date ? data.end : null;
    if (!start || !end) {
      return null;
    }
    return { start, end };
  }

  _normalizeStatsSeries(series) {
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
        const change = Number(point?.change);
        if (!Number.isFinite(start) || !Number.isFinite(change)) {
          return null;
        }
        return { start, change };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start);
  }

  _pickBucketMs(start, end, widthPx, minBucketMs) {
    const rangeMs = Math.max(1, end.getTime() - start.getTime());
    const maxBuckets = Math.max(1, Math.floor(Math.max(240, widthPx || 0) / 12));
    const options = [
      15 * 60 * 1000,
      60 * 60 * 1000,
      3 * 60 * 60 * 1000,
      6 * 60 * 60 * 1000,
      12 * 60 * 60 * 1000,
      24 * 60 * 60 * 1000,
    ].filter((ms) => ms >= minBucketMs);

    for (const ms of options) {
      if (Math.ceil(rangeMs / ms) <= maxBuckets) {
        return ms;
      }
    }
    return 24 * 60 * 60 * 1000;
  }

  _bucketStart(ts, bucketMs) {
    const date = new Date(ts);
    date.setHours(0, 0, 0, 0);
    const dayStart = date.getTime();
    if (bucketMs >= 24 * 60 * 60 * 1000) {
      return dayStart;
    }
    return dayStart + Math.floor((ts - dayStart) / bucketMs) * bucketMs;
  }

  _bucketSeries(series, bucketMs) {
    const totals = new Map();
    (series || []).forEach((point) => {
      const ts = this._bucketStart(point.start, bucketMs);
      totals.set(ts, (totals.get(ts) || 0) + point.change);
    });
    return totals;
  }

  _accumulateSeriesAverage(series, bucketMs, sums, counts) {
    (series || []).forEach((point) => {
      const ts = this._bucketStart(point.start, bucketMs);
      sums.set(ts, (sums.get(ts) || 0) + point.change);
      counts.set(ts, (counts.get(ts) || 0) + 1);
    });
  }

  _mergeInto(target, source) {
    source.forEach((value, key) => {
      target.set(key, (target.get(key) || 0) + value);
    });
  }

  _fetchStats(statIds, start, end, period, types = ["change"]) {
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

  _normalizePriceSeries(series) {
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
        const value = Number(point?.mean);
        if (!Number.isFinite(start) || !Number.isFinite(value)) {
          return null;
        }
        return { start, change: value };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start);
  }


  _fetchStatsMetadata(statIds) {
    const uniqueIds = Array.from(new Set((statIds || []).filter(Boolean)));
    if (!uniqueIds.length) {
      return Promise.resolve({});
    }

    return this._hass
      .callWS({
        type: "recorder/get_statistics_metadata",
        statistic_ids: uniqueIds,
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

  _getGraphColorByIndex(index) {
    const style = getComputedStyle(this);
    const color =
      style.getPropertyValue(`--graph-color-${index + 1}`) ||
      style.getPropertyValue(`--color-${(index % 54) + 1}`);
    return color.trim() || "#5B8FF9";
  }

  _getUntrackedColor() {
    const style = getComputedStyle(this);
    return style.getPropertyValue("--history-unknown-color").trim() || "#9DA0A2";
  }

  _getCostColor() {
    const style = getComputedStyle(this);
    return style.getPropertyValue("--warning-color").trim() || "#f59f00";
  }

  _getPriceColor() {
    const style = getComputedStyle(this);
    return style.getPropertyValue("--info-color").trim() || "#2f7ed8";
  }

  _getTemperatureColor() {
    const style = getComputedStyle(this);
    return style.getPropertyValue("--error-color").trim() || "#d9480f";
  }

  _formatCostValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const unit = this._costUnit || "";
    if (/^[A-Z]{3}$/.test(unit)) {
      return new Intl.NumberFormat(lang, {
        style: "currency",
        currency: unit,
        maximumFractionDigits: 2,
      }).format(amount);
    }
    const formatted = new Intl.NumberFormat(lang, {
      maximumFractionDigits: 2,
    }).format(amount);
    return unit ? `${formatted} ${unit}` : formatted;
  }

  _formatCostAxisValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const unit = this._costUnit || "";
    const digits = this._costAxisDigits || 0;
    if (/^[A-Z]{3}$/.test(unit)) {
      return new Intl.NumberFormat(lang, {
        style: "currency",
        currency: unit,
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(amount);
    }
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount);
    return unit ? `${formatted} ${unit}` : formatted;
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

  _formatTemperatureAxisValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const digits = this._temperatureAxisDigits || 0;
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount);
    return this._temperatureUnit ? `${formatted} ${this._temperatureUnit}` : formatted;
  }

  _formatTemperatureValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(amount);
    return this._temperatureUnit ? `${formatted} ${this._temperatureUnit}` : formatted;
  }

  _formatEnergyStatValue(value) {
    const amount = typeof value === "number" ? value : Number(value || 0);
    const lang = this._hass?.locale?.language || "en";
    const formatted = new Intl.NumberFormat(lang, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    }).format(amount);
    return this._energyUnit ? `${formatted} ${this._energyUnit}` : formatted;
  }

  _renderCustomLegendTable(rows, hiddenIds) {
    const container = this.shadowRoot?.querySelector("#consumption-stats");
    if (!container) {
      return;
    }
    const formatValue = (row, value) => {
      if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return "";
      }
      if (row?.kind === "cost") {
        return this._formatCostValue(value);
      }
      if (row?.kind === "price") {
        return this._formatPriceValue(value);
      }
      if (row?.kind === "temperature") {
        return this._formatTemperatureValue(value);
      }
      return this._formatEnergyStatValue(value);
    };

    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Series</th>
            <th class="num">Min</th>
            <th class="num">Max</th>
            <th class="num">Avg</th>
            <th class="num">Sum</th>
            <th class="num">Last</th>
          </tr>
        </thead>
        <tbody>
          ${(rows || [])
            .map(
              (row) => `
            <tr class="${row.id ? "toggleable" : ""} ${row.id && hiddenIds?.has(row.id) ? "hidden" : ""}" ${row.id ? `data-series-id="${row.id}"` : ""}>
              <td><span class="series"><span class="dot" style="color: ${row.color}; background-color: ${row.color};"></span><span class="label">${row.name}</span></span></td>
              <td class="num">${formatValue(row, row.min)}</td>
              <td class="num">${formatValue(row, row.max)}</td>
              <td class="num">${formatValue(row, row.avg)}</td>
              <td class="num">${formatValue(row, row.sum)}</td>
              <td class="num">${formatValue(row, row.last)}</td>
            </tr>
          `
            )
            .join("")}
        </tbody>
      </table>
    `;

    container.onclick = (ev) => {
      const row = ev.target?.closest?.("tr[data-series-id]");
      const seriesId = row?.getAttribute?.("data-series-id");
      if (!seriesId) {
        return;
      }
      this._toggleSeriesVisibility(seriesId);
    };
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

  _initializeSeriesVisibility(series) {
    const ids = new Set((series || []).map((entry) => entry?.id).filter(Boolean));
    if (!this._hiddenSeriesIds) {
      this._hiddenSeriesIds = new Set();
    }
    if (!this._defaultHiddenSeriesIdsApplied) {
      this._defaultHiddenSeriesIdsApplied = new Set();
    }

    ["adaptive-price-overlay", "adaptive-temperature-overlay"].forEach((id) => {
      if (ids.has(id) && !this._defaultHiddenSeriesIdsApplied.has(id)) {
        this._hiddenSeriesIds.add(id);
        this._defaultHiddenSeriesIdsApplied.add(id);
      }
    });
    this._seriesVisibilityInitialized = true;
  }

  _formatDebugDateTime(ts) {
    if (!Number.isFinite(ts)) {
      return null;
    }
    const date = new Date(ts);
    return {
      ts,
      iso: date.toISOString(),
      local: date.toString(),
    };
  }

  _getSeriesEdge(seriesEntry) {
    const points = (seriesEntry?.data || [])
      .map((point) => {
        if (!Array.isArray(point) || point.length < 2) {
          return null;
        }
        const x = Number(point[0]);
        const y = Number(point[1]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return null;
        }
        return [x, y];
      })
      .filter(Boolean)
      .sort((a, b) => a[0] - b[0]);

    if (!points.length) {
      return {
        id: seriesEntry?.id || "",
        name: seriesEntry?.name || "",
        pointCount: 0,
        nonZeroPointCount: 0,
        xStart: null,
        xEnd: null,
        yMin: null,
        yMax: null,
        firstPoint: null,
        lastPoint: null,
      };
    }

    const xs = points.map((point) => point[0]);
    const ys = points.map((point) => point[1]);
    return {
      id: seriesEntry?.id || "",
      name: seriesEntry?.name || "",
      pointCount: points.length,
      nonZeroPointCount: ys.filter((value) => Math.abs(value) > 1e-12).length,
      xStart: this._formatDebugDateTime(Math.min(...xs)),
      xEnd: this._formatDebugDateTime(Math.max(...xs)),
      yMin: Math.min(...ys),
      yMax: Math.max(...ys),
      firstPoint: {
        at: this._formatDebugDateTime(points[0][0]),
        value: points[0][1],
      },
      lastPoint: {
        at: this._formatDebugDateTime(points[points.length - 1][0]),
        value: points[points.length - 1][1],
      },
    };
  }

  _getOverallSeriesEdges(seriesEntries) {
    const points = [];
    (seriesEntries || []).forEach((entry) => {
      (entry?.data || []).forEach((point) => {
        if (!Array.isArray(point) || point.length < 2) {
          return;
        }
        const x = Number(point[0]);
        const y = Number(point[1]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return;
        }
        points.push([x, y]);
      });
    });

    if (!points.length) {
      return {
        pointCount: 0,
        nonZeroPointCount: 0,
        xStart: null,
        xEnd: null,
        yMin: null,
        yMax: null,
      };
    }

    const xs = points.map((point) => point[0]);
    const ys = points.map((point) => point[1]);
    return {
      pointCount: points.length,
      nonZeroPointCount: ys.filter((value) => Math.abs(value) > 1e-12).length,
      xStart: this._formatDebugDateTime(Math.min(...xs)),
      xEnd: this._formatDebugDateTime(Math.max(...xs)),
      yMin: Math.min(...ys),
      yMax: Math.max(...ys),
    };
  }

  _buildFetchPointCounts(ids, rawStats) {
    return (ids || []).map((id) => ({
      id,
      points: Array.isArray(rawStats?.[id]) ? rawStats[id].length : 0,
    }));
  }

  _buildAdaptiveDebugSignature(payload) {
    return JSON.stringify(payload);
  }

  _logAdaptiveGraphDebug({
    bounds,
    bucketMs,
    devicePeriod,
    flowPeriod,
    deviceIds,
    flowAndCostIds,
    flowIds,
    overlayIds,
    runtimeConfig,
    deviceRaw,
    flowRaw,
    priceRaw,
    temperatureRaw,
    series,
    costPoints,
    pricePoints,
    temperaturePoints,
    untrackedPoints,
  }) {
    if (!this._debugEnabled) {
      return;
    }

    const hiddenIds = this._hiddenSeriesIds || new Set();
    const visibleSeries = (series || []).filter((entry) => !hiddenIds.has(entry.id));
    const visibleEdges = visibleSeries.map((entry) => this._getSeriesEdge(entry));

    const payload = {
      range: {
        start: this._formatDebugDateTime(bounds?.start?.getTime?.()),
        end: this._formatDebugDateTime(bounds?.end?.getTime?.()),
      },
      bucketMs,
      period: {
        device: devicePeriod,
        flowAndCost: flowPeriod,
        price: "hour",
        temperature: "hour",
      },
      runtimeConfig: {
        source: runtimeConfig?.source || "prefs",
        strictOverride: !!runtimeConfig?.strictOverride,
        overridesCount: runtimeConfig?.overridesCount || 0,
        issues: runtimeConfig?.issues || [],
      },
      ids: {
        deviceIds: deviceIds || [],
        flowAndCostIds: flowAndCostIds || [],
        flowFromGrid: flowIds?.fromGrid || [],
        flowToGrid: flowIds?.toGrid || [],
        flowSolar: flowIds?.solar || [],
        flowFromBattery: flowIds?.fromBattery || [],
        flowToBattery: flowIds?.toBattery || [],
        costImport: overlayIds?.importCost || [],
        costExportComp: overlayIds?.exportCompensation || [],
        price: overlayIds?.price || [],
        temperature: overlayIds?.temperature || [],
      },
      fetchPointCounts: {
        device: this._buildFetchPointCounts(deviceIds, deviceRaw),
        flowAndCost: this._buildFetchPointCounts(flowAndCostIds, flowRaw),
        price: this._buildFetchPointCounts(overlayIds?.price, priceRaw),
        temperature: this._buildFetchPointCounts(overlayIds?.temperature, temperatureRaw),
      },
      chart: {
        allSeriesIds: (series || []).map((entry) => entry.id),
        hiddenSeriesIds: Array.from(hiddenIds),
        visibleSeriesIds: visibleSeries.map((entry) => entry.id),
        pointCounts: {
          untracked: untrackedPoints.length,
          cost: costPoints.length,
          price: pricePoints.length,
          temperature: temperaturePoints.length,
        },
        nonZeroPointCounts: {
          untracked: untrackedPoints.filter((point) => Math.abs(Number(point?.[1]) || 0) > 1e-12)
            .length,
          cost: costPoints.filter((point) => Math.abs(Number(point?.[1]) || 0) > 1e-12).length,
          price: pricePoints.filter((point) => Math.abs(Number(point?.[1]) || 0) > 1e-12)
            .length,
          temperature: temperaturePoints.filter(
            (point) => Math.abs(Number(point?.[1]) || 0) > 1e-12
          ).length,
        },
        visibleSeriesEdges: visibleEdges,
        overallVisibleEdges: this._getOverallSeriesEdges(visibleSeries),
        overallAllSeriesEdges: this._getOverallSeriesEdges(series || []),
      },
    };

    const signature = this._buildAdaptiveDebugSignature(payload);
    if (signature === this._lastAdaptiveDebugSignature) {
      return;
    }
    this._lastAdaptiveDebugSignature = signature;

    console.groupCollapsed("[fortum-energy] adaptive graph debug");
    console.log(payload);
    if ((overlayIds?.importCost?.length || overlayIds?.exportCompensation?.length) && !costPoints.length) {
      console.warn("[fortum-energy] cost overlay has IDs but no chart points", {
        importCostIds: overlayIds.importCost,
        exportCompensationIds: overlayIds.exportCompensation,
      });
    }
    if (!untrackedPoints.length || !untrackedPoints.some((point) => Math.abs(Number(point?.[1]) || 0) > 1e-12)) {
      console.warn("[fortum-energy] untracked series has no non-zero points");
    }
    console.groupEnd();
  }

  _applySeriesVisibility() {
    if (!this._chart || !this._allSeries || !this._chartOptions) {
      return;
    }
    const hidden = this._hiddenSeriesIds || new Set();
    const visibleSeries = this._allSeries.filter((entry) => !hidden.has(entry.id));

    const hasData = visibleSeries.some(
      (entry) => Array.isArray(entry.data) && entry.data.length
    );
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (emptyEl) {
      emptyEl.style.display = hasData ? "none" : "block";
    }

    this._chart.hass = this._hass;
    this._chart.data = visibleSeries;
    this._chart.options = this._chartOptions;
    this._chart.requestUpdate?.();

    this._renderCustomLegendTable(this._legendRows || [], hidden);
  }

  _resolveEnergyUnit(data, candidateIds) {
    const statsMetadata = data?.statsMetadata || {};
    const found = (candidateIds || [])
      .map((id) => statsMetadata?.[id]?.statistics_unit_of_measurement)
      .find((unit) => typeof unit === "string" && unit.length);
    return found || "";
  }

  _formatBucketDate(ts, lang) {
    const d = new Date(ts);
    return d.toLocaleDateString(lang, {
      day: "2-digit",
      month: "short",
    });
  }

  _formatHourRange(ts, bucketMs) {
    const start = new Date(ts);
    const end = new Date(ts + bucketMs);
    const two = (value) => String(value).padStart(2, "0");
    if (bucketMs < 60 * 60 * 1000) {
      return `${two(start.getHours())}:${two(start.getMinutes())}-${two(end.getHours())}:${two(
        end.getMinutes()
      )}`;
    }
    return `${two(start.getHours())}-${two(end.getHours())}`;
  }

  _formatBucketLabel(ts, bucketMs, rangeMs, lang) {
    if (bucketMs >= 24 * 60 * 60 * 1000) {
      return this._formatBucketDate(ts, lang);
    }

    const overOneDay = rangeMs > 24 * 60 * 60 * 1000;
    const hourRange = this._formatHourRange(ts, bucketMs);
    if (!overOneDay) {
      return hourRange;
    }
    return `${this._formatBucketDate(ts, lang)} ${hourRange}`;
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
    const bounds = this._getBounds(data);
    if (!data || !bounds || !data.prefs) {
      return;
    }

    const devicePrefs = data.prefs.device_consumption || [];
    const deviceIds = devicePrefs
      .map((device) => device?.stat_consumption)
      .filter((id) => typeof id === "string" && id.length);
    if (!deviceIds.length) {
      return;
    }

    const widthPx = this._chart.clientWidth || this.clientWidth || 0;
    let bucketMs = this._pickBucketMs(bounds.start, bounds.end, widthPx, 15 * 60 * 1000);
    let devicePeriod = bucketMs <= 15 * 60 * 1000 ? "5minute" : "hour";
    const flowPeriod = "hour";
    const flowBucketMs = 60 * 60 * 1000;

    const runtimeConfig = deriveEnergyRuntimeConfig({
      prefs: data.prefs,
      info: data.info,
      overrides: this._energySourceOverridesInput,
      strictOverride: this._strictEnergySourceOverrides,
    });
    const flowIds = runtimeConfig.flowIds;
    const overlayIds = runtimeConfig.overlayIds;
    this._energyUnit = this._resolveEnergyUnit(data, [
      ...deviceIds,
      ...flowIds.fromGrid,
      ...flowIds.toGrid,
      ...flowIds.solar,
      ...flowIds.fromBattery,
      ...flowIds.toBattery,
    ]);
    const flowAndCostIds = Array.from(
      new Set([
        ...flowIds.fromGrid,
        ...flowIds.toGrid,
        ...flowIds.solar,
        ...flowIds.fromBattery,
        ...flowIds.toBattery,
        ...overlayIds.importCost,
        ...overlayIds.exportCompensation,
      ])
    );

    const token = (this._token || 0) + 1;
    this._token = token;

    let deviceRaw = await this._fetchStats(deviceIds, bounds.start, bounds.end, devicePeriod);
    if (this._token !== token) {
      return;
    }

    const missingSubHour =
      devicePeriod === "5minute" &&
      deviceIds.some((id) => !Array.isArray(deviceRaw?.[id]) || deviceRaw[id].length === 0);
    if (missingSubHour) {
      devicePeriod = "hour";
      bucketMs = this._pickBucketMs(bounds.start, bounds.end, widthPx, 60 * 60 * 1000);
      deviceRaw = await this._fetchStats(deviceIds, bounds.start, bounds.end, devicePeriod);
      if (this._token !== token) {
        return;
      }
    }

    const flowRaw = await this._fetchStats(flowAndCostIds, bounds.start, bounds.end, flowPeriod);
    if (this._token !== token) {
      return;
    }

    const raw = {
      ...(flowRaw || {}),
      ...(deviceRaw || {}),
    };

    const normalized = {};
    Object.keys(raw || {}).forEach((id) => {
      normalized[id] = this._normalizeStatsSeries(raw[id]);
    });

    const priceRaw = await this._fetchStats(
      overlayIds.price,
      bounds.start,
      bounds.end,
      "hour",
      ["mean"]
    );
    if (this._token !== token) {
      return;
    }
    const normalizedPrice = {};
    Object.keys(priceRaw || {}).forEach((id) => {
      normalizedPrice[id] = this._normalizePriceSeries(priceRaw[id]);
    });

    const temperatureRaw = await this._fetchStats(
      overlayIds.temperature,
      bounds.start,
      bounds.end,
      "hour",
      ["mean"]
    );
    if (this._token !== token) {
      return;
    }
    const normalizedTemperature = {};
    Object.keys(temperatureRaw || {}).forEach((id) => {
      normalizedTemperature[id] = this._normalizePriceSeries(temperatureRaw[id]);
    });

    const statsMetadata = data?.statsMetadata || {};
    const firstCostId = [...overlayIds.importCost, ...overlayIds.exportCompensation].find(
      (id) => statsMetadata?.[id]?.statistics_unit_of_measurement
    );
    this._costUnit = firstCostId
      ? statsMetadata[firstCostId].statistics_unit_of_measurement
      : "";

    this._priceUnit = "";
    this._temperatureUnit = "";
    if (overlayIds.price.length || overlayIds.temperature.length) {
      try {
        const overlayMeta = await this._fetchStatsMetadata([
          ...overlayIds.price,
          ...overlayIds.temperature,
        ]);
        if (this._token !== token) {
          return;
        }
        const firstPriceMeta = overlayIds.price
          .map((id) => overlayMeta[id])
          .find((item) => item?.statistics_unit_of_measurement);
        const firstTemperatureMeta = overlayIds.temperature
          .map((id) => overlayMeta[id])
          .find((item) => item?.statistics_unit_of_measurement);
        if (firstPriceMeta?.statistics_unit_of_measurement) {
          this._priceUnit = firstPriceMeta.statistics_unit_of_measurement;
        }
        if (firstTemperatureMeta?.statistics_unit_of_measurement) {
          this._temperatureUnit = firstTemperatureMeta.statistics_unit_of_measurement;
        }
      } catch (_err) {
        this._priceUnit = "";
        this._temperatureUnit = "";
      }
    }

    const deviceTotalsByTs = new Map();
    const series = devicePrefs.map((device, index) => {
      const id = device.stat_consumption;
      const bucketed = this._bucketSeries(normalized[id] || [], bucketMs);
      this._mergeInto(deviceTotalsByTs, bucketed);
      const color = this._getGraphColorByIndex(index);
      return {
        id: `adaptive-${id}`,
        name: device.name || id,
        type: "bar",
        stack: "consumption",
        barMaxWidth: 50,
        color,
        itemStyle: {
          borderColor: color,
          borderWidth: 1,
          borderRadius: [4, 4, 0, 0],
          opacity: 0.5,
        },
        data: [],
        __bucketMap: bucketed,
      };
    });

    const fromGrid = new Map();
    const toGrid = new Map();
    const solar = new Map();
    const fromBattery = new Map();
    const toBattery = new Map();
    flowIds.fromGrid.forEach((id) =>
      this._mergeInto(fromGrid, this._bucketSeries(normalized[id] || [], flowBucketMs))
    );
    flowIds.toGrid.forEach((id) =>
      this._mergeInto(toGrid, this._bucketSeries(normalized[id] || [], flowBucketMs))
    );
    flowIds.solar.forEach((id) =>
      this._mergeInto(solar, this._bucketSeries(normalized[id] || [], flowBucketMs))
    );
    flowIds.fromBattery.forEach((id) =>
      this._mergeInto(fromBattery, this._bucketSeries(normalized[id] || [], flowBucketMs))
    );
    flowIds.toBattery.forEach((id) =>
      this._mergeInto(toBattery, this._bucketSeries(normalized[id] || [], flowBucketMs))
    );

    const flowBuckets = new Set([
      ...fromGrid.keys(),
      ...toGrid.keys(),
      ...solar.keys(),
      ...fromBattery.keys(),
      ...toBattery.keys(),
    ]);

    const deviceTotalsByFlowBucket = new Map();
    deviceTotalsByTs.forEach((value, ts) => {
      const flowTs = this._bucketStart(ts, flowBucketMs);
      deviceTotalsByFlowBucket.set(
        flowTs,
        (deviceTotalsByFlowBucket.get(flowTs) || 0) + value
      );
    });

    const sortedFlowBuckets = Array.from(flowBuckets).sort((a, b) => a - b);
    const totalConsumedByBucket = new Map();
    const untrackedByBucket = new Map();
    const subdividesFlowBuckets = bucketMs < flowBucketMs;
    const bucketsPerFlow = subdividesFlowBuckets
      ? Math.max(1, Math.round(flowBucketMs / bucketMs))
      : 1;

    sortedFlowBuckets.forEach((ts) => {
      const usedTotal =
        Math.max(fromGrid.get(ts) || 0, 0) +
        Math.max(solar.get(ts) || 0, 0) +
        Math.max(fromBattery.get(ts) || 0, 0) -
        Math.max(toGrid.get(ts) || 0, 0) -
        Math.max(toBattery.get(ts) || 0, 0);
      const untracked = Math.max(
        0,
        usedTotal - (deviceTotalsByFlowBucket.get(ts) || 0)
      );

      if (subdividesFlowBuckets) {
        for (let idx = 0; idx < bucketsPerFlow; idx += 1) {
          const bucketTs = ts + idx * bucketMs;
          const bucketUsedTotal = idx === 0 ? usedTotal : 0;
          const bucketUntracked = idx === 0 ? untracked : 0;
          totalConsumedByBucket.set(
            bucketTs,
            (totalConsumedByBucket.get(bucketTs) || 0) + bucketUsedTotal
          );
          untrackedByBucket.set(
            bucketTs,
            (untrackedByBucket.get(bucketTs) || 0) + bucketUntracked
          );
        }
        return;
      }

      const bucketTs = this._bucketStart(ts, bucketMs);
      totalConsumedByBucket.set(bucketTs, (totalConsumedByBucket.get(bucketTs) || 0) + usedTotal);
      untrackedByBucket.set(bucketTs, (untrackedByBucket.get(bucketTs) || 0) + untracked);
    });

    const alignedBuckets = new Set([
      ...Array.from(untrackedByBucket.keys()),
      ...Array.from(totalConsumedByBucket.keys()),
    ]);
    series.forEach((entry) => {
      const bucketMap = entry.__bucketMap || new Map();
      bucketMap.forEach((_value, ts) => alignedBuckets.add(ts));
    });
    const sortedAlignedBuckets = Array.from(alignedBuckets).sort((a, b) => a - b);

    const untrackedPoints = sortedAlignedBuckets.map((ts) => [
      ts,
      untrackedByBucket.get(ts) || 0,
    ]);

    series.forEach((entry) => {
      const bucketMap = entry.__bucketMap || new Map();
      entry.data = sortedAlignedBuckets.map((ts) => [ts, bucketMap.get(ts) || 0]);
      delete entry.__bucketMap;
    });

    const untrackedColor = this._getUntrackedColor();
    series.push({
      id: "adaptive-untracked",
      name: "Untracked",
      type: "bar",
      stack: "consumption",
      barMaxWidth: 50,
      color: untrackedColor,
      itemStyle: {
        borderColor: untrackedColor,
        borderWidth: 1,
        borderRadius: [4, 4, 0, 0],
        opacity: 0.5,
      },
      data: untrackedPoints,
    });

    const costMap = new Map();
    overlayIds.importCost.forEach((id) => {
      this._mergeInto(costMap, this._bucketSeries(normalized[id] || [], bucketMs));
    });
    overlayIds.exportCompensation.forEach((id) => {
      const negativeMap = new Map();
      this._bucketSeries(normalized[id] || [], bucketMs).forEach((value, ts) => {
        negativeMap.set(ts, -value);
      });
      this._mergeInto(costMap, negativeMap);
    });
    const costPoints = Array.from(costMap.entries())
      .map(([ts, value]) => [ts, value])
      .sort((a, b) => a[0] - b[0]);

    const priceSums = new Map();
    const priceCounts = new Map();
    overlayIds.price.forEach((id) => {
      this._accumulateSeriesAverage(
        normalizedPrice[id] || [],
        bucketMs,
        priceSums,
        priceCounts
      );
    });
    const pricePoints = Array.from(priceSums.entries())
      .map(([ts, sum]) => [ts, sum / Math.max(1, priceCounts.get(ts) || 1)])
      .sort((a, b) => a[0] - b[0]);

    const temperatureSums = new Map();
    const temperatureCounts = new Map();
    overlayIds.temperature.forEach((id) => {
      this._accumulateSeriesAverage(
        normalizedTemperature[id] || [],
        bucketMs,
        temperatureSums,
        temperatureCounts
      );
    });
    const temperaturePoints = Array.from(temperatureSums.entries())
      .map(([ts, sum]) => [ts, sum / Math.max(1, temperatureCounts.get(ts) || 1)])
      .sort((a, b) => a[0] - b[0]);

    this._costAxisDigits = computeAxisFractionDigits(
      costPoints.map((point) => Number(point[1]))
    );
    this._priceAxisDigits = computeAxisFractionDigits(
      pricePoints.map((point) => Number(point[1]))
    );
    this._temperatureAxisDigits = computeAxisFractionDigits(
      temperaturePoints.map((point) => Number(point[1]))
    );

    if (costPoints.length) {
      const costColor = this._getCostColor();
      series.push({
        id: "adaptive-cost-overlay",
        name: "Cost",
        type: "line",
        smooth: 0.2,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 1,
        z: 80,
        lineStyle: {
          width: 2,
          color: costColor,
        },
        itemStyle: {
          color: costColor,
        },
        data: costPoints,
      });
    }

    if (pricePoints.length) {
      const priceColor = this._getPriceColor();
      series.push({
        id: "adaptive-price-overlay",
        name: "Price",
        type: "line",
        smooth: 0.05,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 2,
        z: 79,
        lineStyle: {
          width: 2,
          type: "dashed",
          color: priceColor,
        },
        itemStyle: {
          color: priceColor,
        },
        data: pricePoints,
      });
    }

    if (temperaturePoints.length) {
      const temperatureColor = this._getTemperatureColor();
      series.push({
        id: "adaptive-temperature-overlay",
        name: "Temperature",
        type: "line",
        smooth: 0.1,
        symbol: "none",
        showSymbol: false,
        yAxisIndex: 3,
        z: 78,
        lineStyle: {
          width: 2,
          type: "dotted",
          color: temperatureColor,
        },
        itemStyle: {
          color: temperatureColor,
        },
        data: temperaturePoints,
      });
    }

    const lang = this._hass?.locale?.language || "en";
    const rangeMs = bounds.end.getTime() - bounds.start.getTime();
    const intervalLabel =
      bucketMs >= 24 * 60 * 60 * 1000
        ? "1d"
        : bucketMs >= 60 * 60 * 1000
          ? `${Math.round(bucketMs / (60 * 60 * 1000))}h`
          : "15m";

    const options = {
      grid: { top: 20, bottom: 0, left: 1, right: 1, containLabel: true },
      legend: {
        show: false,
        type: "custom",
        data: [
          {
            id: "adaptive-total",
            secondaryIds: [],
            name: "Total",
            itemStyle: {
              color: "var(--primary-text-color)",
              borderColor: "var(--primary-text-color)",
            },
          },
          ...series.map((entry) => {
            const legendColor =
              entry?.itemStyle?.color ||
              entry?.lineStyle?.color ||
              entry?.itemStyle?.borderColor ||
              entry?.color;
            return {
              id: entry.id,
              secondaryIds: [],
              name: entry.name,
              itemStyle: {
                color: legendColor,
                borderColor: legendColor,
              },
            };
          }),
        ],
      },
      xAxis: {
        type: "time",
        axisLabel: {
          formatter: (value) =>
            this._formatBucketLabel(Number(value), bucketMs, rangeMs, lang),
        },
      },
      yAxis: [
        {
          type: "value",
          axisLabel: {
            formatter: (value) =>
              this._energyUnit ? `${value} ${this._energyUnit}` : `${value}`,
          },
        },
        {
          type: "value",
          position: "right",
          splitLine: { show: false },
          axisLabel: {
            formatter: (value) => this._formatCostAxisValue(value),
          },
        },
        {
          type: "value",
          position: "right",
          offset: 56,
          splitLine: { show: false },
          axisLabel: {
            formatter: (value) => this._formatPriceAxisValue(value),
          },
        },
        {
          type: "value",
          position: "right",
          offset: 112,
          splitLine: { show: false },
          axisLabel: {
            formatter: (value) => this._formatTemperatureAxisValue(value),
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
          const totalBucketTs = this._bucketStart(Number(ts), bucketMs);
          const title = `${this._formatBucketLabel(
            totalBucketTs,
            bucketMs,
            rangeMs,
            lang
          )} (${intervalLabel})`;
          const totalValue = Number(totalConsumedByBucket.get(totalBucketTs) || 0);
          const totalLine = `Total: <div style="direction:ltr; display: inline;">${this._formatEnergyStatValue(totalValue)}</div>`;
          const lines = rows
            .filter(
              (row) =>
                Array.isArray(row.value) && Math.abs(Number(row.value[1]) || 0) > 0
            )
            .map((row) => {
              const value = Number(row.value[1]);
              const text =
                row.seriesId === "adaptive-cost-overlay"
                  ? this._formatCostValue(value)
                  : row.seriesId === "adaptive-price-overlay"
                    ? this._formatPriceValue(value)
                    : row.seriesId === "adaptive-temperature-overlay"
                      ? this._formatTemperatureValue(value)
                    : this._energyUnit
                      ? `${value.toFixed(2)} ${this._energyUnit}`
                      : `${value.toFixed(2)}`;
              return `${row.marker} ${row.seriesName}: <div style="direction:ltr; display: inline;">${text}</div>`;
            })
            .join("<br>");
          return `<h4 style="text-align: center; margin: 0;">${title}</h4>${totalLine}${lines ? `<br>${lines}` : ""}`;
        },
      },
    };

    const legendRowsFromSeries = series.map((entry) => {
      const values = (Array.isArray(entry?.data) ? entry.data : [])
        .map((p) => (Array.isArray(p) ? Number(p[1]) : NaN))
        .filter((v) => Number.isFinite(v));
      const min = values.length ? Math.min(...values) : 0;
      const max = values.length ? Math.max(...values) : 0;
      const avg = values.length
        ? values.reduce((acc, value) => acc + value, 0) / values.length
        : 0;
      const sum = values.length ? values.reduce((acc, value) => acc + value, 0) : 0;
      const last = values.length ? values[values.length - 1] : 0;
      let kind = "energy";
      if (entry.id === "adaptive-cost-overlay") {
        kind = "cost";
      } else if (entry.id === "adaptive-price-overlay") {
        kind = "price";
      } else if (entry.id === "adaptive-temperature-overlay") {
        kind = "temperature";
      }
      return {
        id: entry.id || "",
        name: entry.name || "",
        color:
          entry?.itemStyle?.color ||
          entry?.lineStyle?.color ||
          entry?.itemStyle?.borderColor ||
          entry?.color ||
          "var(--primary-color)",
        min,
        max,
        avg,
        sum: kind === "price" || kind === "temperature" ? null : sum,
        last,
        kind,
      };
    });

    const totalValues = Array.from(totalConsumedByBucket.values()).filter((v) =>
      Number.isFinite(v)
    );
    const totalRow = {
      id: "",
      name: "Total",
      color: "var(--primary-text-color)",
      min: totalValues.length ? Math.min(...totalValues) : 0,
      max: totalValues.length ? Math.max(...totalValues) : 0,
      avg: totalValues.length
        ? totalValues.reduce((acc, value) => acc + value, 0) / totalValues.length
        : 0,
      sum: totalValues.length
        ? totalValues.reduce((acc, value) => acc + value, 0)
        : 0,
      last: totalValues.length ? totalValues[totalValues.length - 1] : 0,
      kind: "energy",
    };

    this._allSeries = series;
    this._chartOptions = options;
    this._legendRows = [totalRow, ...legendRowsFromSeries];
    this._initializeSeriesVisibility(series);
    this._logAdaptiveGraphDebug({
      bounds,
      bucketMs,
      devicePeriod,
      flowPeriod,
      deviceIds,
      flowAndCostIds,
      flowIds,
      overlayIds,
      runtimeConfig,
      deviceRaw,
      flowRaw,
      priceRaw,
      temperatureRaw,
      series,
      costPoints,
      pricePoints,
      temperaturePoints,
      untrackedPoints,
    });
    this._applySeriesVisibility();
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
