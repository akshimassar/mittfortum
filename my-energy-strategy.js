const DEFAULT_COLLECTION_KEY = "energy_my_energy_dashboard";

const EMPTY_PREFS = {
  energy_sources: [],
  device_consumption: [],
  device_consumption_water: [],
};

const localize = (hass, key, fallback) => hass.localize?.(key) || fallback;

const fetchEnergyPrefs = async (hass) => {
  try {
    const prefs = await hass.callWS({ type: "energy/get_prefs" });
    return prefs || EMPTY_PREFS;
  } catch (err) {
    if (err && err.code === "not_found") {
      return EMPTY_PREFS;
    }
    throw err;
  }
};

const RANGE_STORAGE_PREFIX = "my-energy-range-";

const _samePeriod = (startA, endA, startB, endB) => {
  const aStart = startA instanceof Date ? startA.getTime() : null;
  const bStart = startB instanceof Date ? startB.getTime() : null;
  const aEnd = endA instanceof Date ? endA.getTime() : null;
  const bEnd = endB instanceof Date ? endB.getTime() : null;
  return aStart === bStart && aEnd === bEnd;
};

const _getTodayRange = () => {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setHours(23, 59, 59, 999);
  return { start, end };
};

const _readStoredRange = (collectionKey) => {
  try {
    const raw = localStorage.getItem(`${RANGE_STORAGE_PREFIX}${collectionKey}`);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    const startMs = Number(parsed?.start);
    const endMs = Number(parsed?.end);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
      return null;
    }
    return { start: new Date(startMs), end: new Date(endMs) };
  } catch (_err) {
    return null;
  }
};

const _storeRange = (collectionKey, start, end) => {
  if (!(start instanceof Date) || !(end instanceof Date)) {
    return;
  }
  localStorage.setItem(
    `${RANGE_STORAGE_PREFIX}${collectionKey}`,
    JSON.stringify({
      start: start.getTime(),
      end: end.getTime(),
    })
  );
};

const ensureMyEnergyRangePersistence = (hass, collectionKey) => {
  const collection = hass?.connection?.[`_${collectionKey}`];
  if (!collection || typeof collection.setPeriod !== "function") {
    return;
  }

  if (!collection.__myEnergyRangePatched) {
    const originalSetPeriod = collection.setPeriod.bind(collection);
    collection.setPeriod = (start, end) => {
      originalSetPeriod(start, end);
      if (start instanceof Date && end instanceof Date) {
        _storeRange(collectionKey, start, end);
      }
    };
    collection.__myEnergyRangePatched = true;
  }

  if (collection.__myEnergyRangeInitialized) {
    return;
  }
  collection.__myEnergyRangeInitialized = true;

  const stored = _readStoredRange(collectionKey);
  const range = stored || _getTodayRange();
  if (_samePeriod(collection.start, collection.end, range.start, range.end)) {
    return;
  }

  collection.setPeriod(range.start, range.end);
  if (typeof collection.refresh === "function") {
    collection.refresh();
  }
};

const hasAnyEnergyPrefs = (prefs) =>
  prefs &&
  (prefs.device_consumption.length > 0 || prefs.energy_sources.length > 0);

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
      type: "custom:my-energy-settings-redirect-card",
    },
  ],
});

const buildElectricityViewConfig = (prefs, collectionKey, hass) => {
  const view = {
    title: localize(hass, "ui.panel.energy.title.electricity", "Electricity"),
    path: "electricity",
    type: "sections",
    sections: [],
  };

  const hasGrid = prefs.energy_sources.some(
    (source) =>
      source.type === "grid" &&
      (!!source.stat_energy_from || !!source.stat_energy_to)
  );
  const hasSolar = prefs.energy_sources.some((source) => source.type === "solar");
  const hasBattery = prefs.energy_sources.some(
    (source) => source.type === "battery"
  );

  const mainCards = [];

  mainCards.push({
    type: "custom:my-energy-spacer-card",
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
    vertical_opening_direction: "up",
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:my-energy-quick-ranges-card",
    collection_key: collectionKey,
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:my-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  mainCards.push({
    type: "energy-compare",
    collection_key: collectionKey,
    grid_options: { columns: 36 },
  });

  if (hasGrid || hasBattery) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_usage_graph_title",
        "Energy usage"
      ),
      type: "energy-usage-graph",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  if (hasSolar) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_solar_graph_title",
        "Solar production"
      ),
      type: "energy-solar-graph",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  if (prefs.device_consumption.length) {
    mainCards.push({
      title: "Individual devices (adaptive)",
      type: "custom:my-energy-devices-adaptive-graph-card",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });

    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_devices_detail_graph_title",
        "Individual devices"
      ),
      type: "custom:my-energy-devices-detail-overlay-card",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  if (prefs.energy_sources.length || prefs.device_consumption.length) {
    mainCards.push({
      title: "Summary",
      type: "custom:my-energy-consumption-summary-card",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  mainCards.push({
    type: "custom:my-energy-spacer-card",
  });

  view.sections.push({
    type: "grid",
    column_span: 3,
    cards: mainCards,
  });

  return view;
};

class MyEnergyDashboardStrategy {
  static async generate(config, hass) {
    try {
      const collectionKey =
        config.collection_key || config.collectionKey || DEFAULT_COLLECTION_KEY;
      const prefs = await fetchEnergyPrefs(hass);

      if (!hasAnyEnergyPrefs(prefs)) {
        return { views: [buildSetupView(), buildSettingsView(hass)] };
      }

      return {
        views: [
          buildElectricityViewConfig(prefs, collectionKey, hass),
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
                content: `Error loading my-energy strategy:\n> ${message}`,
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

class MyEnergyDashboardStrategyAlias extends MyEnergyDashboardStrategy {}

class MyEnergySpacerCard extends HTMLElement {
  setConfig(_config) {}

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { rows: 1, columns: 4 };
  }

  connectedCallback() {
    this.style.display = "block";
    this.style.height = "100%";
    this.style.pointerEvents = "none";
  }
}

class MyEnergyQuickRangesCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._render();
  }

  set hass(hass) {
    const languageChanged =
      this._hass?.locale?.language !== hass?.locale?.language;
    this._hass = hass;
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    ensureMyEnergyRangePersistence(hass, collectionKey);
    if (!this._rendered || languageChanged) {
      this._render();
    }
  }

  getCardSize() {
    return 1;
  }

  _setDefaultRange(range) {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    const collection = this._hass?.connection?.[`_${collectionKey}`];

    const getCenterDate = () => {
      const start = collection?.start instanceof Date ? collection.start : null;
      const end = collection?.end instanceof Date ? collection.end : null;
      if (!start || !end) {
        return new Date();
      }
      return new Date((start.getTime() + end.getTime()) / 2);
    };

    const center = getCenterDate();
    center.setHours(12, 0, 0, 0);

    const createCenteredRange = (days) => {
      const half = Math.floor((days - 1) / 2);
      const start = new Date(center);
      start.setDate(center.getDate() - half);
      start.setHours(0, 0, 0, 0);

      const end = new Date(start);
      end.setDate(start.getDate() + days - 1);
      end.setHours(23, 59, 59, 999);

      return { start, end };
    };

    let start;
    let end;
    if (range === "month") {
      ({ start, end } = createCenteredRange(31));
    } else if (range === "week") {
      ({ start, end } = createCenteredRange(7));
    } else {
      ({ start, end } = createCenteredRange(1));
    }

    if (collection && collection.setPeriod && collection.refresh) {
      collection.setPeriod(start, end);
      collection.refresh();
      return;
    }

    window.location.reload();
  }

  _render() {
    if (!this.shadowRoot || !this._hass) {
      return;
    }

    const dayLabel = "Day";
    const weekLabel = "Week";
    const monthLabel = "Month";

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color));
          border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color);
          box-sizing: border-box;
          height: 100%;
          display: flex;
          align-items: center;
        }
        .row {
          display: flex;
          gap: 8px;
          padding: 8px 12px;
          align-items: center;
          width: 100%;
        }
        ha-button {
          flex: 1;
          --ha-button-theme-color: currentColor;
        }
      </style>
      <div class="card">
        <div class="row">
          <ha-button appearance="filled" size="small" data-range="day">${dayLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="week">${weekLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="month">${monthLabel}</ha-button>
        </div>
      </div>
    `;

    if (!this._boundClick) {
      this._boundClick = (ev) => {
        const target = ev.target;
        if (!(target instanceof Element)) {
          return;
        }
        const button = target.closest("ha-button");
        if (!button) {
          return;
        }
        const range = button.getAttribute("data-range");
        if (range) {
          this._setDefaultRange(range);
        }
      };
      this.shadowRoot.addEventListener("click", this._boundClick);
    }

    this._rendered = true;
  }
}

class MyEnergyDevicesDetailOverlayCard extends HTMLElement {
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
    if (
      typeof consumptionStatId !== "string" ||
      !consumptionStatId.startsWith("mittfortum:hourly_consumption_")
    ) {
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
        console.warn("[my-energy] detail statistics fetch failed", err);
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
        console.warn("[my-energy] price metadata fetch failed", err);
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
        console.warn("[my-energy] price statistics fetch failed", err);
      })
      .finally(() => {
        missingIds.forEach((id) => this._externalPriceInflight.delete(id));
      });
  }

  _collectPriceByTimestamp(data) {
    const totals = {};
    const prefs = data?.prefs || EMPTY_PREFS;
    const debug = {
      candidates: [],
      found: [],
      missing: [],
      unit: "",
    };
    const candidateIds = [];

    const addStat = (statId) => {
      if (!statId) {
        return;
      }
      debug.candidates.push(statId);
      const series = this._externalPriceStats?.[statId];
      if (!series) {
        debug.missing.push(statId);
        return;
      }
      debug.found.push(statId);
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

    const foundId = debug.found[0];
    const unit = foundId
      ? this._externalPriceMeta?.[foundId]?.statistics_unit_of_measurement
      : undefined;
    this._priceUnit = unit || this._priceUnit || "";
    debug.unit = this._priceUnit;

    const series = Object.keys(totals)
      .map((ts) => [Number(ts), totals[ts]])
      .sort((a, b) => a[0] - b[0]);

    this._priceDebug = {
      ...debug,
      points: series.length,
    };

    return series;
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
          series.id !== "my-energy-cost-overlay" &&
          series.id !== "my-energy-price-overlay"
      );

    if (costSeriesData.length) {
      detailCard._chartData = detailCard._chartData.concat({
        id: "my-energy-cost-overlay",
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
        id: "my-energy-price-overlay",
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
          item.id !== "my-energy-cost-overlay" &&
          item.id !== "my-energy-price-overlay"
      );
      if (costSeriesData.length) {
        legendWithoutOverlay.push({
          id: "my-energy-cost-overlay",
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
          id: "my-energy-price-overlay",
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

    this._renderOverlayDebug({
      chartReady: !!detailCard,
      energySources: data?.prefs?.energy_sources?.length || 0,
    });

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
                  (row.seriesId !== "my-energy-cost-overlay" &&
                    row.seriesId !== "my-energy-price-overlay")
                ) {
                  return;
                }
                const label = row.seriesName || "Cost";
                const y = Array.isArray(row.value) ? Number(row.value[1] || 0) : 0;
                const valueText =
                  row.seriesId === "my-energy-price-overlay"
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

    this._renderOverlayDebug({
      chartReady: true,
      energySources: data?.prefs?.energy_sources?.length || 0,
      price: this._priceDebug,
      chartSeries: Array.isArray(detailCard._chartData)
        ? detailCard._chartData.length
        : 0,
    });
  }

  _renderOverlayDebug(debug) {
    if (!debug.price) {
      return;
    }

    const payload = {
      chartReady: !!debug.chartReady,
      energySources: debug.energySources || 0,
      chartSeries: debug.chartSeries ?? "n/a",
      pricePoints: debug.price?.points ?? 0,
      priceUnit: debug.price?.unit || "n/a",
      priceCandidates: debug.price?.candidates || [],
      priceFound: debug.price?.found || [],
      priceMissing: debug.price?.missing || [],
    };

    console.log("[my-energy] overlay debug", payload);
  }
}

class MyEnergyDevicesAdaptiveGraphCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
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
        .content { padding: 16px; }
        .empty { color: var(--secondary-text-color); }
      </style>
      <ha-card>
        ${this._config?.title ? `<h1 class="card-header">${this._config.title}</h1>` : ""}
        <div class="content">
          <ha-chart-base id="chart"></ha-chart-base>
          <div id="empty" class="empty" style="display:none;">No data</div>
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

  _mergeInto(target, source) {
    source.forEach((value, key) => {
      target.set(key, (target.get(key) || 0) + value);
    });
  }

  _fetchStats(statIds, start, end, period) {
    if (!statIds.length) {
      return Promise.resolve({});
    }

    return this._hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      statistic_ids: statIds,
      period,
      types: ["change"],
    });
  }

  _getGridImportFlows(source) {
    if (Array.isArray(source.flow_from) && source.flow_from.length) {
      return source.flow_from;
    }
    return [source];
  }

  _getGridExportFlows(source) {
    if (Array.isArray(source.flow_to) && source.flow_to.length) {
      return source.flow_to;
    }
    return [source];
  }

  _buildEnergyFlowIds(prefs) {
    const ids = {
      fromGrid: [],
      toGrid: [],
      solar: [],
      fromBattery: [],
      toBattery: [],
    };

    (prefs?.energy_sources || []).forEach((source) => {
      if (source.type === "grid") {
        this._getGridImportFlows(source).forEach((flow) => {
          if (flow.stat_energy_from) {
            ids.fromGrid.push(flow.stat_energy_from);
          }
        });
        this._getGridExportFlows(source).forEach((flow) => {
          if (flow.stat_energy_to) {
            ids.toGrid.push(flow.stat_energy_to);
          }
        });
        return;
      }
      if (source.type === "solar" && source.stat_energy_from) {
        ids.solar.push(source.stat_energy_from);
        return;
      }
      if (source.type === "battery") {
        if (source.stat_energy_from) {
          ids.fromBattery.push(source.stat_energy_from);
        }
        if (source.stat_energy_to) {
          ids.toBattery.push(source.stat_energy_to);
        }
      }
    });

    return ids;
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
    let period = bucketMs <= 15 * 60 * 1000 ? "5minute" : "hour";

    const flowIds = this._buildEnergyFlowIds(data.prefs);
    const allIds = Array.from(
      new Set([
        ...deviceIds,
        ...flowIds.fromGrid,
        ...flowIds.toGrid,
        ...flowIds.solar,
        ...flowIds.fromBattery,
        ...flowIds.toBattery,
      ])
    );

    const token = (this._token || 0) + 1;
    this._token = token;

    let raw = await this._fetchStats(allIds, bounds.start, bounds.end, period);
    if (this._token !== token) {
      return;
    }

    const missingSubHour =
      period === "5minute" &&
      deviceIds.some((id) => !Array.isArray(raw?.[id]) || raw[id].length === 0);
    if (missingSubHour) {
      period = "hour";
      bucketMs = this._pickBucketMs(bounds.start, bounds.end, widthPx, 60 * 60 * 1000);
      raw = await this._fetchStats(allIds, bounds.start, bounds.end, period);
      if (this._token !== token) {
        return;
      }
    }

    const normalized = {};
    Object.keys(raw || {}).forEach((id) => {
      normalized[id] = this._normalizeStatsSeries(raw[id]);
    });

    const palette = [
      "#5B8FF9",
      "#61DDAA",
      "#65789B",
      "#F6BD16",
      "#7262FD",
      "#78D3F8",
      "#9661BC",
      "#F6903D",
    ];

    const deviceTotalsByTs = new Map();
    const series = devicePrefs.map((device, index) => {
      const id = device.stat_consumption;
      const bucketed = this._bucketSeries(normalized[id] || [], bucketMs);
      this._mergeInto(deviceTotalsByTs, bucketed);
      const points = Array.from(bucketed.entries())
        .map(([ts, value]) => [ts, value])
        .sort((a, b) => a[0] - b[0]);
      const color = palette[index % palette.length];
      return {
        id: `adaptive-${id}`,
        name: device.name || id,
        type: "bar",
        stack: "consumption",
        barMaxWidth: 50,
        color,
        itemStyle: { borderColor: color },
        data: points,
      };
    });

    const fromGrid = new Map();
    const toGrid = new Map();
    const solar = new Map();
    const fromBattery = new Map();
    const toBattery = new Map();
    flowIds.fromGrid.forEach((id) =>
      this._mergeInto(fromGrid, this._bucketSeries(normalized[id] || [], bucketMs))
    );
    flowIds.toGrid.forEach((id) =>
      this._mergeInto(toGrid, this._bucketSeries(normalized[id] || [], bucketMs))
    );
    flowIds.solar.forEach((id) =>
      this._mergeInto(solar, this._bucketSeries(normalized[id] || [], bucketMs))
    );
    flowIds.fromBattery.forEach((id) =>
      this._mergeInto(fromBattery, this._bucketSeries(normalized[id] || [], bucketMs))
    );
    flowIds.toBattery.forEach((id) =>
      this._mergeInto(toBattery, this._bucketSeries(normalized[id] || [], bucketMs))
    );

    const allTs = new Set([
      ...fromGrid.keys(),
      ...toGrid.keys(),
      ...solar.keys(),
      ...fromBattery.keys(),
      ...toBattery.keys(),
      ...deviceTotalsByTs.keys(),
    ]);

    const untrackedPoints = Array.from(allTs)
      .sort((a, b) => a - b)
      .map((ts) => {
        const usedTotal =
          Math.max(fromGrid.get(ts) || 0, 0) +
          Math.max(solar.get(ts) || 0, 0) +
          Math.max(fromBattery.get(ts) || 0, 0) -
          Math.max(toGrid.get(ts) || 0, 0) -
          Math.max(toBattery.get(ts) || 0, 0);
        const untracked = Math.max(0, usedTotal - (deviceTotalsByTs.get(ts) || 0));
        return [ts, untracked];
      })
      .filter(([, value]) => value > 0);

    series.push({
      id: "adaptive-untracked",
      name: "Untracked",
      type: "bar",
      stack: "consumption",
      barMaxWidth: 50,
      color: "#9DA0A2",
      itemStyle: { borderColor: "#9DA0A2" },
      data: untrackedPoints,
    });

    const lang = this._hass?.locale?.language || "en";
    const intervalLabel =
      bucketMs >= 24 * 60 * 60 * 1000
        ? "1d"
        : bucketMs >= 60 * 60 * 1000
          ? `${Math.round(bucketMs / (60 * 60 * 1000))}h`
          : "15m";

    const options = {
      grid: { top: 20, bottom: 0, left: 1, right: 1, containLabel: true },
      legend: {
        show: true,
        type: "custom",
        data: series.map((entry) => ({
          id: entry.id,
          secondaryIds: [],
          name: entry.name,
          itemStyle: {
            color: entry.color,
            borderColor: entry.itemStyle?.borderColor || entry.color,
          },
        })),
      },
      xAxis: {
        type: "time",
        axisLabel: {
          formatter: (value) => {
            const d = new Date(value);
            return bucketMs >= 24 * 60 * 60 * 1000
              ? d.toLocaleDateString(lang, { month: "short", day: "numeric" })
              : d.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
          },
        },
      },
      yAxis: {
        type: "value",
      },
      tooltip: {
        trigger: "axis",
        formatter: (params) => {
          const rows = Array.isArray(params) ? params : [params];
          if (!rows.length) {
            return "";
          }
          const ts = Array.isArray(rows[0].value) ? rows[0].value[0] : rows[0].value;
          const title = `${new Date(Number(ts)).toLocaleString(lang)} (${intervalLabel})`;
          const lines = rows
            .filter((row) => Array.isArray(row.value) && Number(row.value[1]) > 0)
            .map(
              (row) =>
                `${row.marker} ${row.seriesName}: <div style="direction:ltr; display: inline;">${Number(row.value[1]).toFixed(2)} kWh</div>`
            )
            .join("<br>");
          return lines
            ? `<h4 style="text-align: center; margin: 0;">${title}</h4>${lines}`
            : "";
        },
      },
    };

    const hasData = series.some((entry) => Array.isArray(entry.data) && entry.data.length);
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (emptyEl) {
      emptyEl.style.display = hasData ? "none" : "block";
    }

    this._chart.hass = this._hass;
    this._chart.data = series;
    this._chart.options = options;
    this._chart.requestUpdate?.();
  }
}

class MyEnergyConsumptionSummaryCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._trySubscribe();
    this._render();
  }

  set hass(hass) {
    this._hassUpdateCount = (this._hassUpdateCount || 0) + 1;
    const languageChanged =
      this._hass?.locale?.language !== hass?.locale?.language;
    const currencyChanged =
      this._hass?.config?.currency !== hass?.config?.currency;
    this._hass = hass;
    this._trySubscribe();
    this._ensureLatestPrefs();
    if (!this._hasRendered || languageChanged || currencyChanged) {
      this._render();
    }
  }

  async _ensureLatestPrefs() {
    if (!this._hass || this._loadingPrefs) {
      return;
    }
    const now = Date.now();
    if (this._latestPrefs && this._lastPrefsFetch && now - this._lastPrefsFetch < 300000) {
      return;
    }
    this._loadingPrefs = true;
    try {
      const prefs = await fetchEnergyPrefs(this._hass);
      this._latestPrefs = prefs;
      this._lastPrefsFetch = Date.now();
      this._scheduleRender();
    } catch (_err) {
      // Ignore and keep collection prefs fallback.
    } finally {
      this._loadingPrefs = false;
    }
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = undefined;
    }
  }

  getCardSize() {
    return 5;
  }

  _getCollection() {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    const key = `_${collectionKey}`;
    return this._hass?.connection?.[key];
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
      this._updateCount = (this._updateCount || 0) + 1;
      this._lastUpdateAt = Date.now();
      this._scheduleRender();
    });
  }

  _scheduleRender() {
    if (this._renderQueued) {
      return;
    }
    this._renderQueued = true;
    requestAnimationFrame(() => {
      this._renderQueued = false;
      this._render();
    });
  }

  _sumStatistic(stats, statisticId) {
    if (!statisticId || !stats || !stats[statisticId]) {
      return 0;
    }
    return stats[statisticId].reduce((sum, point) => {
      const value = point?.change;
      return sum + (typeof value === "number" ? value : 0);
    }, 0);
  }

  _sumStatisticsByTimestamp(stats, statisticIds) {
    const totals = {};
    let sum = 0;

    statisticIds.forEach((id) => {
      const series = stats[id];
      if (!series) {
        return;
      }
      series.forEach((point) => {
        if (point.change === null || point.change === undefined) {
          return;
        }
        const value = point.change;
        sum += value;
        totals[point.start] = (totals[point.start] || 0) + value;
      });
    });

    return { totals, sum };
  }

  _getGridImportFlows(source) {
    if (Array.isArray(source.flow_from) && source.flow_from.length) {
      return source.flow_from;
    }
    return [source];
  }

  _getGridExportFlows(source) {
    if (Array.isArray(source.flow_to) && source.flow_to.length) {
      return source.flow_to;
    }
    return [source];
  }

  _computeConsumptionSingle(data) {
    let toGrid = Math.max(data.to_grid || 0, 0);
    let toBattery = Math.max(data.to_battery || 0, 0);
    let solar = Math.max(data.solar || 0, 0);
    let fromGrid = Math.max(data.from_grid || 0, 0);
    let fromBattery = Math.max(data.from_battery || 0, 0);

    const usedTotal = fromGrid + solar + fromBattery - toGrid - toBattery;

    let usedTotalRemaining = Math.max(usedTotal, 0);

    const excessGridInAfterConsumption = Math.max(
      0,
      Math.min(toBattery, fromGrid - usedTotalRemaining)
    );
    toBattery -= excessGridInAfterConsumption;
    fromGrid -= excessGridInAfterConsumption;

    const solarToBattery = Math.min(solar, toBattery);
    toBattery -= solarToBattery;
    solar -= solarToBattery;

    const solarToGrid = Math.min(solar, toGrid);
    toGrid -= solarToGrid;
    solar -= solarToGrid;

    const batteryToGrid = Math.min(fromBattery, toGrid);
    fromBattery -= batteryToGrid;
    toGrid -= batteryToGrid;

    const gridToBatterySecondPass = Math.min(fromGrid, toBattery);
    fromGrid -= gridToBatterySecondPass;

    const usedSolar = Math.min(usedTotalRemaining, solar);
    usedTotalRemaining -= usedSolar;

    const usedBattery = Math.min(fromBattery, usedTotalRemaining);
    usedTotalRemaining -= usedBattery;

    const usedGrid = Math.min(usedTotalRemaining, fromGrid);

    return {
      used_total: usedTotal,
      used_grid: usedGrid,
      used_solar: usedSolar,
      used_battery: usedBattery,
    };
  }

  _computeTotalConsumptionFromEnergyModel(prefs, stats) {
    const fromGridIds = [];
    const toGridIds = [];
    const solarIds = [];
    const toBatteryIds = [];
    const fromBatteryIds = [];

    prefs.energy_sources.forEach((source) => {
      if (source.type === "grid") {
        this._getGridImportFlows(source).forEach((flow) => {
          if (flow.stat_energy_from) {
            fromGridIds.push(flow.stat_energy_from);
          }
        });
        this._getGridExportFlows(source).forEach((flow) => {
          if (flow.stat_energy_to) {
            toGridIds.push(flow.stat_energy_to);
          }
        });
        return;
      }
      if (source.type === "solar") {
        solarIds.push(source.stat_energy_from);
        return;
      }
      if (source.type === "battery") {
        fromBatteryIds.push(source.stat_energy_from);
        toBatteryIds.push(source.stat_energy_to);
      }
    });

    const fromGrid = this._sumStatisticsByTimestamp(stats, fromGridIds).totals;
    const toGrid = this._sumStatisticsByTimestamp(stats, toGridIds).totals;
    const solar = this._sumStatisticsByTimestamp(stats, solarIds).totals;
    const fromBattery = this._sumStatisticsByTimestamp(stats, fromBatteryIds).totals;
    const toBattery = this._sumStatisticsByTimestamp(stats, toBatteryIds).totals;

    const timestamps = new Set([
      ...Object.keys(fromGrid),
      ...Object.keys(toGrid),
      ...Object.keys(solar),
      ...Object.keys(fromBattery),
      ...Object.keys(toBattery),
    ]);

    let usedTotal = 0;
    timestamps.forEach((ts) => {
      const t = Number(ts);
      const consumed = this._computeConsumptionSingle({
        from_grid: fromGrid[t] || 0,
        to_grid: toGrid[t] || 0,
        solar: solar[t] || 0,
        from_battery: fromBattery[t] || 0,
        to_battery: toBattery[t] || 0,
      });
      usedTotal += consumed.used_total || 0;
    });

    return Math.max(0, usedTotal);
  }

  _computeTotals(data) {
    const stats = data.stats || {};
    const prefs = this._latestPrefs || data.prefs || EMPTY_PREFS;
    const info = data.info || { cost_sensors: {} };

    let fromGrid = 0;
    let toGrid = 0;
    let solar = 0;
    let fromBattery = 0;
    let toBattery = 0;
    let importCost = 0;
    let exportCompensation = 0;

    const debug = {
      gridFromIds: [],
      gridToIds: [],
      costImportIds: [],
      costExportIds: [],
      statKeys: Object.keys(stats).length,
      prefsEnergySources: (data.prefs?.energy_sources || []).length,
      activePrefsEnergySources: prefs.energy_sources.length,
      prefsTypes: (data.prefs?.energy_sources || []).map((s) => s.type),
      activePrefsTypes: prefs.energy_sources.map((s) => s.type),
      firstCollectionSource: data.prefs?.energy_sources?.[0] || null,
      firstActiveSource: prefs.energy_sources?.[0] || null,
    };

    for (const source of prefs.energy_sources) {
      if (source.type === "grid") {
        this._getGridImportFlows(source).forEach((flow) => {
          if (!flow.stat_energy_from) {
            return;
          }
          debug.gridFromIds.push(flow.stat_energy_from);
          fromGrid += this._sumStatistic(stats, flow.stat_energy_from);
          const importCostStat =
            flow.stat_cost || info.cost_sensors[flow.stat_energy_from];
          if (importCostStat) {
            debug.costImportIds.push(importCostStat);
          }
          importCost += this._sumStatistic(stats, importCostStat);
        });

        this._getGridExportFlows(source).forEach((flow) => {
          if (!flow.stat_energy_to) {
            return;
          }
          debug.gridToIds.push(flow.stat_energy_to);
          toGrid += this._sumStatistic(stats, flow.stat_energy_to);
          const exportCompStat =
            flow.stat_compensation ||
            flow.stat_cost ||
            info.cost_sensors[flow.stat_energy_to];
          if (exportCompStat) {
            debug.costExportIds.push(exportCompStat);
          }
          exportCompensation += this._sumStatistic(stats, exportCompStat);
        });

        continue;
      }

      if (source.type === "solar") {
        solar += this._sumStatistic(stats, source.stat_energy_from);
        continue;
      }

      if (source.type === "battery") {
        fromBattery += this._sumStatistic(stats, source.stat_energy_from);
        toBattery += this._sumStatistic(stats, source.stat_energy_to);
      }
    }

    const totalConsumption = this._computeTotalConsumptionFromEnergyModel(
      prefs,
      stats
    );
    const totalCost = importCost - exportCompensation;

    const devices = prefs.device_consumption.map((device) => ({
      name: device.name || device.stat_consumption,
      consumption: this._sumStatistic(stats, device.stat_consumption),
    }));

    const trackedConsumption = devices.reduce(
      (sum, item) => sum + item.consumption,
      0
    );
    const unspecifiedConsumption = Math.max(0, totalConsumption - trackedConsumption);
    const unitCost = totalConsumption > 0 ? totalCost / totalConsumption : 0;

    return {
      totalConsumption,
      totalCost,
      devices: devices.map((device) => ({
        ...device,
        cost: device.consumption * unitCost,
      })),
      unspecifiedConsumption,
      unspecifiedCost: unspecifiedConsumption * unitCost,
      __debug: {
        ...debug,
        hassUpdateCount: this._hassUpdateCount || 0,
        renderCount: this._renderCount || 0,
        updateCount: this._updateCount || 0,
        lastUpdateAt: this._lastUpdateAt || 0,
        fromGrid,
        toGrid,
        solar,
        fromBattery,
        toBattery,
        importCost,
        exportCompensation,
      },
    };
  }

  _formatEnergy(value) {
    const lang = this._hass?.locale?.language || "en";
    return `${new Intl.NumberFormat(lang, {
      maximumFractionDigits: 2,
    }).format(value)} kWh`;
  }

  _formatCost(value) {
    const lang = this._hass?.locale?.language || "en";
    return new Intl.NumberFormat(lang, {
      style: "currency",
      currency: this._hass.config.currency || "EUR",
      maximumFractionDigits: 2,
    }).format(value);
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }

    if (!this._hass) {
      this.shadowRoot.innerHTML = "";
      return;
    }

    const data = this._energyData || this._getCollection()?.state;

    if (!data || !data.prefs || !data.stats) {
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          .content { padding: 16px; color: var(--secondary-text-color); }
        </style>
        <ha-card><div class="content">Loading summary...</div></ha-card>
      `;
      return;
    }

    try {
      this._renderCount = (this._renderCount || 0) + 1;
      const totals = this._computeTotals(data);
      const rows = [
        {
          name: "Total",
          consumption: totals.totalConsumption,
          cost: totals.totalCost,
          bold: true,
        },
        ...totals.devices.map((device) => ({
          name: device.name,
          consumption: device.consumption,
          cost: device.cost,
        })),
        {
          name: "Unspecified",
          consumption: totals.unspecifiedConsumption,
          cost: totals.unspecifiedCost,
        },
      ];

      const body = rows
        .map(
          (row) => `
          <tr class="${row.bold ? "bold" : ""}">
            <td>${row.name}</td>
            <td class="num">${this._formatEnergy(row.consumption)}</td>
            <td class="num">${this._formatCost(row.cost)}</td>
          </tr>
        `
        )
        .join("");

      this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
          user-select: text;
          -webkit-user-select: text;
        }
        ha-card {
          height: 100%;
        }
        .wrap {
          padding: 12px 16px 14px;
        }
        h3 {
          margin: 0 0 8px;
          font-size: var(--ha-font-size-m);
          font-weight: var(--ha-font-weight-medium);
        }
        table {
          width: 100%;
          border-collapse: collapse;
          font-size: var(--ha-font-size-s);
        }
        th,
        td {
          padding: 8px 0;
          border-bottom: 1px solid var(--divider-color);
          user-select: text;
          -webkit-user-select: text;
        }
        th {
          text-align: left;
          color: var(--secondary-text-color);
          font-weight: var(--ha-font-weight-medium);
        }
        .num {
          text-align: right;
          white-space: nowrap;
        }
        tr.bold td {
          font-weight: var(--ha-font-weight-medium);
        }
      </style>
      <ha-card>
        <div class="wrap">
          <h3>Consumption summary</h3>
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th class="num">Consumption</th>
                <th class="num">Cost</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </ha-card>
    `;

      this._hasRendered = true;
    } catch (err) {
      console.error("[my-energy] summary render failed", err);
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:12px;color:var(--error-color);">Summary failed to render</div>
        </ha-card>
      `;
    }
  }
}

class MyEnergySettingsRedirectCard extends HTMLElement {
  setConfig(_config) {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._render();
  }

  connectedCallback() {
    this._redirectTimeout = window.setTimeout(() => {
      const targetPath = "/config/energy/electricity?historyBack=1";
      if (window.location.pathname.includes("/config/energy")) {
        return;
      }

      window.history.replaceState(window.history.state ?? null, "", targetPath);
      window.dispatchEvent(
        new CustomEvent("location-changed", {
          detail: { replace: true },
        })
      );
    }, 50);
  }

  disconnectedCallback() {
    if (this._redirectTimeout) {
      clearTimeout(this._redirectTimeout);
      this._redirectTimeout = undefined;
    }
  }

  getCardSize() {
    return 2;
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card {
          background: var(--ha-card-background, var(--card-background-color));
          border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color);
          padding: 16px;
          color: var(--secondary-text-color);
        }
        a {
          color: var(--primary-color);
        }
      </style>
      <div class="card">
        Redirecting to Energy settings... If nothing happens,
        <a href="/config/energy/electricity?historyBack=1">open settings</a>.
      </div>
    `;
  }
}

const registerIfNeeded = (tag, klass) => {
  if (!customElements.get(tag)) {
    customElements.define(tag, klass);
  }
};

registerIfNeeded(
  "my-energy-consumption-summary-card",
  MyEnergyConsumptionSummaryCard
);
registerIfNeeded("my-energy-spacer-card", MyEnergySpacerCard);
registerIfNeeded("my-energy-quick-ranges-card", MyEnergyQuickRangesCard);
registerIfNeeded(
  "my-energy-devices-detail-overlay-card",
  MyEnergyDevicesDetailOverlayCard
);
registerIfNeeded(
  "my-energy-devices-adaptive-graph-card",
  MyEnergyDevicesAdaptiveGraphCard
);
registerIfNeeded("my-energy-settings-redirect-card", MyEnergySettingsRedirectCard);
registerIfNeeded("ll-strategy-dashboard-my-energy", MyEnergyDashboardStrategy);
registerIfNeeded("ll-strategy-my-energy", MyEnergyDashboardStrategyAlias);
