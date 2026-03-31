import { DEFAULT_COLLECTION_KEY } from "/fortum-energy-static/strategy/shared/constants.js";
import { deriveEnergyRuntimeConfig } from "/fortum-energy-static/strategy/runtime-config.mjs";
import { computeAxisFractionDigits } from "/fortum-energy-static/strategy/shared/formatters.js";

export class FortumEnergyDevicesAdaptiveGraphCard extends HTMLElement {
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
