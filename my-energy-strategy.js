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
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_devices_detail_graph_title",
        "Individual devices"
      ),
      type: "energy-devices-detail-graph",
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
  });

  mainCards.push({
    type: "custom:my-energy-quick-ranges-card",
    collection_key: collectionKey,
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

class MyEnergyDateSelectionCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._renderShell();
    this._ensureInnerCard();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._innerCard) {
      this._innerCard.hass = hass;
      this._scheduleEnhance();
    } else {
      this._ensureInnerCard();
    }
  }

  disconnectedCallback() {
    if (this._observer) {
      this._observer.disconnect();
      this._observer = undefined;
    }
  }

  getCardSize() {
    if (this._innerCard && typeof this._innerCard.getCardSize === "function") {
      return this._innerCard.getCardSize();
    }
    return 1;
  }

  _setDefaultRange(range) {
    const collectionKey = this._config?.collection_key || "energy";
    localStorage.setItem(`energy-default-period-_${collectionKey}`, range);
    window.location.reload();
  }

  _scheduleEnhance() {
    setTimeout(() => this._enhancePeriodSelector(), 0);
  }

  _enhancePeriodSelector() {
    const dateCardShadow = this._innerCard?.shadowRoot;
    if (!dateCardShadow) {
      return;
    }
    const selector = dateCardShadow.querySelector("hui-energy-period-selector");
    const selectorShadow = selector?.shadowRoot;
    if (!selectorShadow) {
      return;
    }

    const overflow = selectorShadow.querySelector(".date-actions .overflow");
    if (!overflow) {
      return;
    }

    const nowLabel =
      selector.hass?.localize?.(
        "ui.panel.lovelace.components.energy_period_selector.now"
      ) || "Now";

    if (!selectorShadow.querySelector(".my-energy-range-buttons")) {
      const group = document.createElement("div");
      group.className = "my-energy-range-buttons";

      const addButton = (label, range) => {
        const button = document.createElement("ha-button");
        button.setAttribute("appearance", "filled");
        button.setAttribute("size", "small");
        button.textContent = label;
        button.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this._setDefaultRange(range);
        });
        group.appendChild(button);
      };

      addButton("Today", "today");
      addButton("Week", "this_week");
      addButton("Month", "this_month");

      overflow.insertBefore(group, overflow.firstChild);
    }

    selectorShadow.querySelectorAll("ha-button").forEach((button) => {
      const text = button.textContent?.trim() || "";
      if (text === nowLabel || text.includes(nowLabel)) {
        button.remove();
      }
    });

    selectorShadow.querySelectorAll("ha-dropdown-item").forEach((item) => {
      const text = item.textContent?.trim() || "";
      if (text === nowLabel || text.includes(nowLabel)) {
        item.remove();
      }
    });

    if (!selectorShadow.querySelector("style[data-my-energy-ranges]")) {
      const style = document.createElement("style");
      style.dataset.myEnergyRanges = "1";
      style.textContent = `
        .my-energy-range-buttons {
          display: inline-flex;
          gap: 8px;
          margin-right: 8px;
        }
        .my-energy-range-buttons ha-button {
          --ha-button-theme-color: currentColor;
        }
      `;
      selectorShadow.appendChild(style);
    }

    if (!this._observer) {
      this._observer = new MutationObserver(() => this._enhancePeriodSelector());
      this._observer.observe(selectorShadow, { childList: true, subtree: true });
    }
  }

  async _ensureInnerCard() {
    if (this._creating || this._innerCard || !this._hass || !this.shadowRoot) {
      return;
    }

    this._creating = true;
    try {
      const helpers = await this._hass.loadCardHelpers();
      this._innerCard = await helpers.createCardElement({
        type: "energy-date-selection",
        ...this._config,
      });
      this._innerCard.hass = this._hass;
      const container = this.shadowRoot.querySelector(".container");
      if (container) {
        container.replaceChildren(this._innerCard);
      }
      this._scheduleEnhance();
    } catch (err) {
      console.error("[my-energy] date selection init failed", err);
      const container = this.shadowRoot.querySelector(".container");
      if (container) {
        container.innerHTML =
          '<div style="padding:12px;color:var(--error-color);">Date selector failed to load</div>';
      }
    } finally {
      this._creating = false;
    }
  }

  _renderShell() {
    if (!this.shadowRoot) {
      return;
    }
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
}

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
    if (!this._rendered || languageChanged) {
      this._render();
    }
  }

  getCardSize() {
    return 1;
  }

  _setDefaultRange(range) {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    localStorage.setItem(`energy-default-period-_${collectionKey}`, range);

    const now = new Date();
    const end = new Date(now);
    end.setHours(23, 59, 59, 999);

    let start = new Date(now);
    start.setHours(0, 0, 0, 0);

    if (range === "this_month") {
      start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
    } else if (range === "this_week") {
      const firstWeekday = this._hass?.locale?.first_weekday || "monday";
      const weekStartsOnSunday = firstWeekday === "sunday";
      const day = now.getDay();
      const offset = weekStartsOnSunday ? day : (day + 6) % 7;
      start = new Date(now);
      start.setDate(now.getDate() - offset);
      start.setHours(0, 0, 0, 0);
    }

    const collection = this._hass?.connection?.[`_${collectionKey}`];
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

    const todayLabel =
      this._hass.localize?.("ui.components.date-range-picker.ranges.today") ||
      "Today";
    const weekLabel =
      this._hass.localize?.("ui.components.date-range-picker.ranges.this_week") ||
      "Week";
    const monthLabel =
      this._hass.localize?.("ui.components.date-range-picker.ranges.this_month") ||
      "Month";

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
          <ha-button appearance="filled" size="small" data-range="today">${todayLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="this_week">${weekLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="this_month">${monthLabel}</ha-button>
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
        if (source.stat_energy_from) {
          fromGridIds.push(source.stat_energy_from);
        }
        if (source.stat_energy_to) {
          toGridIds.push(source.stat_energy_to);
        }
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
    };

    for (const source of prefs.energy_sources) {
      if (source.type === "grid") {
        if (source.stat_energy_from) {
          debug.gridFromIds.push(source.stat_energy_from);
          fromGrid += this._sumStatistic(stats, source.stat_energy_from);
          const importCostStat =
            source.stat_cost || info.cost_sensors[source.stat_energy_from];
          if (importCostStat) {
            debug.costImportIds.push(importCostStat);
          }
          importCost += this._sumStatistic(stats, importCostStat);
        }
        if (source.stat_energy_to) {
          debug.gridToIds.push(source.stat_energy_to);
          toGrid += this._sumStatistic(stats, source.stat_energy_to);
          const exportCompStat =
            source.stat_compensation || info.cost_sensors[source.stat_energy_to];
          if (exportCompStat) {
            debug.costExportIds.push(exportCompStat);
          }
          exportCompensation += this._sumStatistic(stats, exportCompStat);
        }
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

      const debugText = [
        `subscription updates: ${totals.__debug.updateCount}`,
        `last update: ${
          totals.__debug.lastUpdateAt
            ? new Date(totals.__debug.lastUpdateAt).toLocaleTimeString()
            : "n/a"
        }`,
        `collection prefs sources: ${totals.__debug.prefsEnergySources}`,
        `active prefs sources: ${totals.__debug.activePrefsEnergySources}`,
        `collection source types: ${totals.__debug.prefsTypes.join(", ") || "(none)"}`,
        `active source types: ${totals.__debug.activePrefsTypes.join(", ") || "(none)"}`,
        `stats keys loaded: ${totals.__debug.statKeys}`,
        `grid from ids: ${totals.__debug.gridFromIds.join(", ") || "(none)"}`,
        `grid to ids: ${totals.__debug.gridToIds.join(", ") || "(none)"}`,
        `import cost ids: ${totals.__debug.costImportIds.join(", ") || "(none)"}`,
        `export cost ids: ${totals.__debug.costExportIds.join(", ") || "(none)"}`,
        `sum from_grid: ${totals.__debug.fromGrid}`,
        `sum to_grid: ${totals.__debug.toGrid}`,
        `sum solar: ${totals.__debug.solar}`,
        `sum from_battery: ${totals.__debug.fromBattery}`,
        `sum to_battery: ${totals.__debug.toBattery}`,
        `import cost sum: ${totals.__debug.importCost}`,
        `export compensation sum: ${totals.__debug.exportCompensation}`,
        `computed total consumption: ${totals.totalConsumption}`,
        `computed total cost: ${totals.totalCost}`,
      ].join("\n");

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
        details {
          margin-top: 10px;
        }
        pre {
          margin: 8px 0 0;
          white-space: pre-wrap;
          word-break: break-word;
          color: var(--secondary-text-color);
          font-size: 12px;
          user-select: text;
          -webkit-user-select: text;
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
          <details open>
            <summary>Debug summary values</summary>
            <pre>${debugText}</pre>
          </details>
        </div>
      </ha-card>
    `;

      const details = this.shadowRoot.querySelector("details");
      if (details) {
        if (this._debugOpen === undefined) {
          this._debugOpen = true;
        }
        details.open = !!this._debugOpen;
        if (!this._boundDebugToggle) {
          this._boundDebugToggle = () => {
            this._debugOpen = details.open;
          };
        }
        details.addEventListener("toggle", this._boundDebugToggle);
      }

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
registerIfNeeded("my-energy-settings-redirect-card", MyEnergySettingsRedirectCard);
registerIfNeeded("ll-strategy-dashboard-my-energy", MyEnergyDashboardStrategy);
registerIfNeeded("ll-strategy-my-energy", MyEnergyDashboardStrategyAlias);
