import { DEFAULT_COLLECTION_KEY, EMPTY_PREFS } from "/fortum-energy-static/strategy/shared/constants.js";
import { fetchEnergyPrefs } from "/fortum-energy-static/strategy/shared/energy-prefs.js";
import { normalizeEnergySourceOverrides } from "/fortum-energy-static/strategy/runtime-config.mjs";

export class FortumEnergyCustomLegendCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._energySourceOverrides = normalizeEnergySourceOverrides(this._config.energy_sources);
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._trySubscribe();
    this._render();
  }

  set hass(hass) {
    this._hassUpdateCount = (this._hassUpdateCount || 0) + 1;
    const languageChanged = this._hass?.locale?.language !== hass?.locale?.language;
    const currencyChanged = this._hass?.config?.currency !== hass?.config?.currency;
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

    if (this._energySourceOverrides.length) {
      this._energySourceOverrides.forEach((source) => {
        fromGridIds.push(source.stat_energy_from);
      });
    }

    prefs.energy_sources.forEach((source) => {
      if (source.type === "grid") {
        if (!this._energySourceOverrides.length) {
          this._getGridImportFlows(source).forEach((flow) => {
            if (flow.stat_energy_from) {
              fromGridIds.push(flow.stat_energy_from);
            }
          });
        }
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
    const gridSources = (prefs.energy_sources || []).filter((source) => source.type === "grid");

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

    if (this._energySourceOverrides.length) {
      this._energySourceOverrides.forEach((source) => {
        debug.gridFromIds.push(source.stat_energy_from);
        fromGrid += this._sumStatistic(stats, source.stat_energy_from);
        const importCostStat = source.stat_cost || info.cost_sensors[source.stat_energy_from];
        if (importCostStat) {
          debug.costImportIds.push(importCostStat);
        }
        importCost += this._sumStatistic(stats, importCostStat);
      });
    } else {
      gridSources.forEach((source) => {
        this._getGridImportFlows(source).forEach((flow) => {
          if (!flow.stat_energy_from) {
            return;
          }
          debug.gridFromIds.push(flow.stat_energy_from);
          fromGrid += this._sumStatistic(stats, flow.stat_energy_from);
          const importCostStat = flow.stat_cost || info.cost_sensors[flow.stat_energy_from];
          if (importCostStat) {
            debug.costImportIds.push(importCostStat);
          }
          importCost += this._sumStatistic(stats, importCostStat);
        });
      });
    }

    gridSources.forEach((source) => {
      this._getGridExportFlows(source).forEach((flow) => {
        if (!flow.stat_energy_to) {
          return;
        }
        debug.gridToIds.push(flow.stat_energy_to);
        toGrid += this._sumStatistic(stats, flow.stat_energy_to);
        const exportCompStat =
          flow.stat_compensation || flow.stat_cost || info.cost_sensors[flow.stat_energy_to];
        if (exportCompStat) {
          debug.costExportIds.push(exportCompStat);
        }
        exportCompensation += this._sumStatistic(stats, exportCompStat);
      });
    });

    for (const source of prefs.energy_sources) {
      if (source.type === "grid") {
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

    const totalConsumption = this._computeTotalConsumptionFromEnergyModel(prefs, stats);
    const totalCost = importCost - exportCompensation;

    const devices = prefs.device_consumption.map((device) => ({
      name: device.name || device.stat,
      consumption: this._sumStatistic(stats, device.stat),
    }));

    const trackedConsumption = devices.reduce((sum, item) => sum + item.consumption, 0);
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
        <ha-card><div class="content">Loading...</div></ha-card>
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
      console.error("[fortum-energy] custom legend render failed", err);
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:12px;color:var(--error-color);">Custom legend failed to render</div>
        </ha-card>
      `;
    }
  }
}
