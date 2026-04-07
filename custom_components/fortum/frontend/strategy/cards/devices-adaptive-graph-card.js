import { DEFAULT_COLLECTION_KEY } from "/fortum-energy-static/strategy/shared/constants.js";
import { computeAxisFractionDigits } from "/fortum-energy-static/strategy/shared/formatters.js";
import { computeTotalAndUntrackedByBucket } from "/fortum-energy-static/strategy/shared/adaptive-bucket-math.mjs";
import {
  buildDashboardDebugExport,
  getDebugClientContext,
  recordAdaptiveDebugInfo,
  recordAdaptiveDebugEvent,
  setDashboardCardConfig,
} from "/fortum-energy-static/strategy/shared/debug-info-store.js";

export class FortumEnergyDevicesAdaptiveGraphCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._resolvedMetrics = this._config.resolved_metrics || {};
    this._debugEnabled = this._config.debug === true;
    setDashboardCardConfig("adaptive_graph", this._config);
    if (!this._debugEnabled) {
      this._lastAdaptiveDebugSignature = undefined;
      this._latestAdaptiveDebugInfo = undefined;
    }
    this._syncDebugLifecycleListeners();
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
    const rangeKey = this._getCollectionRangeKey();
    if (rangeKey && rangeKey !== this._lastCollectionRangeKey) {
      this._lastCollectionRangeKey = rangeKey;
      this._scheduleUpdate("hass_range_changed");
    }
  }

  connectedCallback() {
    this._ensureDebugIdentity();
    this._ensureResizeObserver();
    if (!this._rangeChangedHandler) {
      this._rangeChangedHandler = (event) => this._handleRangeChangedEvent(event);
      window.addEventListener("fortum-energy:range-changed", this._rangeChangedHandler);
    }
    if (!this._exportDebugInfoHandler) {
      this._exportDebugInfoHandler = (event) => this._handleExportDebugInfoRequest(event);
      window.addEventListener(
        "fortum-energy:export-debug-info",
        this._exportDebugInfoHandler
      );
    }
    this._syncDebugLifecycleListeners();
    this._recordDebugEvent("card_connected");
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = undefined;
    }
    this._collection = undefined;
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = undefined;
    }
    if (this._rangeChangedHandler) {
      window.removeEventListener("fortum-energy:range-changed", this._rangeChangedHandler);
      this._rangeChangedHandler = undefined;
    }
    if (this._exportDebugInfoHandler) {
      window.removeEventListener(
        "fortum-energy:export-debug-info",
        this._exportDebugInfoHandler
      );
      this._exportDebugInfoHandler = undefined;
    }
    this._teardownDebugLifecycleListeners();
    this._recordDebugEvent("subscription_state", {
      action: "disconnected",
      has_active_subscription: false,
      collection_key: this._getCollectionKey(),
    });
    this._recordDebugEvent("card_disconnected");
  }

  getCardSize() {
    return 3;
  }

  _buildCardInstanceId() {
    const randomPart = Math.random().toString(36).slice(2, 10);
    return `adaptive_card_${Date.now().toString(36)}_${randomPart}`;
  }

  _ensureDebugIdentity() {
    if (this._debugIdentity) {
      return this._debugIdentity;
    }
    const clientContext = getDebugClientContext();
    const locationPath =
      typeof window !== "undefined" && window.location
        ? `${window.location.pathname}${window.location.search}${window.location.hash}`
        : "unknown";
    this._debugIdentity = {
      ...clientContext,
      card_instance_id: this._buildCardInstanceId(),
      collection_key: this._getCollectionKey(),
      location_path: locationPath,
    };
    return this._debugIdentity;
  }

  _buildDebugContext(extra = {}) {
    const identity = this._ensureDebugIdentity();
    const visibilityState =
      typeof document !== "undefined" && typeof document.visibilityState === "string"
        ? document.visibilityState
        : "unknown";
    return {
      ...identity,
      visibility_state: visibilityState,
      ...extra,
    };
  }

  _recordDebugEvent(eventType, extra = {}) {
    if (!this._debugEnabled) {
      return;
    }
    recordAdaptiveDebugEvent({
      source: "adaptive_graph",
      event_type: eventType,
      context: this._buildDebugContext(),
      payload: extra,
    });
  }

  _syncDebugLifecycleListeners() {
    if (!this.isConnected) {
      return;
    }
    if (!this._debugEnabled) {
      this._teardownDebugLifecycleListeners();
      return;
    }
    this._ensureDebugIdentity();
    if (!this._visibilityHandler && typeof document !== "undefined") {
      this._visibilityHandler = () => {
        this._recordDebugEvent("document_visibilitychange");
      };
      document.addEventListener("visibilitychange", this._visibilityHandler);
    }
    if (!this._focusHandler && typeof window !== "undefined") {
      this._focusHandler = () => this._recordDebugEvent("window_focus");
      window.addEventListener("focus", this._focusHandler);
    }
    if (!this._blurHandler && typeof window !== "undefined") {
      this._blurHandler = () => this._recordDebugEvent("window_blur");
      window.addEventListener("blur", this._blurHandler);
    }
    if (!this._pageshowHandler && typeof window !== "undefined") {
      this._pageshowHandler = (event) =>
        this._recordDebugEvent("window_pageshow", {
          persisted: event?.persisted === true,
        });
      window.addEventListener("pageshow", this._pageshowHandler);
    }
    if (!this._pagehideHandler && typeof window !== "undefined") {
      this._pagehideHandler = (event) =>
        this._recordDebugEvent("window_pagehide", {
          persisted: event?.persisted === true,
        });
      window.addEventListener("pagehide", this._pagehideHandler);
    }
    if (!this._storageHandler && typeof window !== "undefined") {
      this._storageHandler = (event) => {
        this._recordDebugEvent("window_storage", {
          key: event?.key || null,
        });
      };
      window.addEventListener("storage", this._storageHandler);
    }
  }

  _teardownDebugLifecycleListeners() {
    if (this._visibilityHandler && typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = undefined;
    }
    if (this._focusHandler && typeof window !== "undefined") {
      window.removeEventListener("focus", this._focusHandler);
      this._focusHandler = undefined;
    }
    if (this._blurHandler && typeof window !== "undefined") {
      window.removeEventListener("blur", this._blurHandler);
      this._blurHandler = undefined;
    }
    if (this._pageshowHandler && typeof window !== "undefined") {
      window.removeEventListener("pageshow", this._pageshowHandler);
      this._pageshowHandler = undefined;
    }
    if (this._pagehideHandler && typeof window !== "undefined") {
      window.removeEventListener("pagehide", this._pagehideHandler);
      this._pagehideHandler = undefined;
    }
    if (this._storageHandler && typeof window !== "undefined") {
      window.removeEventListener("storage", this._storageHandler);
      this._storageHandler = undefined;
    }
  }

  _getCollection() {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    return this._hass?.connection?.[`_${collectionKey}`];
  }

  _getCollectionKey() {
    return this._config?.collection_key || DEFAULT_COLLECTION_KEY;
  }

  _getCollectionRangeKey() {
    const collection = this._getCollection();
    const startMs = collection?.start instanceof Date ? collection.start.getTime() : null;
    const endMs = collection?.end instanceof Date ? collection.end.getTime() : null;
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
      return null;
    }
    return `${startMs}:${endMs}`;
  }

  _handleRangeChangedEvent(event) {
    const detail = event?.detail || {};
    if (detail.collectionKey && detail.collectionKey !== this._getCollectionKey()) {
      return;
    }
    const startMs = Number(detail.start);
    const endMs = Number(detail.end);
    const rangeKey =
      Number.isFinite(startMs) && Number.isFinite(endMs)
        ? `${startMs}:${endMs}`
        : this._getCollectionRangeKey();
    if (!rangeKey || rangeKey === this._lastCollectionRangeKey) {
      return;
    }
    this._lastCollectionRangeKey = rangeKey;
    this._recordDebugEvent("range_changed_event", {
      detail,
      resolved_range_key: rangeKey,
    });
    this._scheduleUpdate("range_changed_event");
  }

  _trySubscribe() {
    const collection = this._getCollection();
    if (!collection || !collection.subscribe) {
      this._recordDebugEvent("subscription_state", {
        action: "missing_collection_or_subscribe",
        has_collection: Boolean(collection),
        has_subscribe: Boolean(collection?.subscribe),
      });
      return;
    }
    if (collection === this._collection && this._unsubscribe) {
      this._recordDebugEvent("subscription_state", {
        action: "already_subscribed",
        has_active_subscription: true,
        collection_key: this._getCollectionKey(),
      });
      return;
    }
    if (this._unsubscribe) {
      this._unsubscribe();
    }
    this._collection = collection;
    this._unsubscribe = collection.subscribe(() => {
      const state = this._collection?.state;
      const bounds = this._getBounds(state);
      const rangeKey = bounds
        ? `${bounds.start.getTime()}:${bounds.end.getTime()}`
        : this._getCollectionRangeKey();
      if (rangeKey && rangeKey === this._lastSubscribedRangeKey) {
        return;
      }
      this._lastSubscribedRangeKey = rangeKey || null;
      this._recordDebugEvent("collection_subscribe_range", {
        range_key: rangeKey || null,
      });
      this._scheduleUpdate("collection_subscribe_range");
    });
    this._recordDebugEvent("subscription_state", {
      action: "subscribed",
      has_active_subscription: true,
      collection_key: this._getCollectionKey(),
    });
  }

  _ensureResizeObserver() {
    if (this._resizeObserver || typeof ResizeObserver === "undefined") {
      return;
    }
    this._resizeObserver = new ResizeObserver((entries) => {
      const entry = Array.isArray(entries) && entries.length ? entries[0] : null;
      const width = Number(entry?.contentRect?.width);
      const height = Number(entry?.contentRect?.height);
      const hasSize = Number.isFinite(width) && Number.isFinite(height);
      const prevWidth = Number.isFinite(this._lastObservedWidth) ? this._lastObservedWidth : null;
      const prevHeight = Number.isFinite(this._lastObservedHeight) ? this._lastObservedHeight : null;
      const deltaWidth = hasSize && Number.isFinite(prevWidth) ? width - prevWidth : null;
      const deltaHeight = hasSize && Number.isFinite(prevHeight) ? height - prevHeight : null;
      if (hasSize) {
        this._lastObservedWidth = width;
        this._lastObservedHeight = height;
      }
      const resizeContext = {
        width: hasSize ? width : null,
        height: hasSize ? height : null,
        prev_width: prevWidth,
        prev_height: prevHeight,
        delta_width: deltaWidth,
        delta_height: deltaHeight,
      };
      this._recordDebugEvent("resize_observer", resizeContext);
      this._scheduleUpdate("resize", resizeContext);
    });
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
        .warning {
          margin-top: 10px;
          color: var(--warning-color);
          user-select: text;
          -webkit-user-select: text;
          cursor: text;
          white-space: pre-wrap;
          font-size: var(--ha-font-size-s);
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
          <div id="warning" class="warning" style="display:none;"></div>
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

  _scheduleUpdate(trigger = "unspecified", triggerContext = null) {
    if (!Number.isFinite(this._updateSequence)) {
      this._updateSequence = 0;
    }
    if (!Number.isFinite(this._pendingUpdateId)) {
      this._pendingUpdateId = this._updateSequence + 1;
      this._updateSequence = this._pendingUpdateId;
    }
    if (!Array.isArray(this._pendingUpdateTriggers)) {
      this._pendingUpdateTriggers = [];
    }
    this._pendingUpdateTriggers.push(trigger);
    if (!this._pendingTriggerContexts) {
      this._pendingTriggerContexts = {};
    }
    if (triggerContext && typeof triggerContext === "object") {
      this._pendingTriggerContexts[trigger] = triggerContext;
    }
    if (this._updateScheduled) {
      return;
    }
    this._updateScheduled = true;
    requestAnimationFrame(() => {
      this._updateScheduled = false;
      const triggerChain = this._pendingUpdateTriggers?.length
        ? this._pendingUpdateTriggers.slice()
        : ["unspecified"];
      const primaryTrigger = triggerChain[0] || "unspecified";
      const finalTrigger = triggerChain[triggerChain.length - 1] || "unspecified";
      const updateId = this._pendingUpdateId;
      this._lastUpdateMeta = {
        updateId,
        primaryTrigger,
        finalTrigger,
        triggerChain,
        triggerContexts: { ...(this._pendingTriggerContexts || {}) },
      };
      this._lastUpdateTrigger = finalTrigger;
      this._pendingUpdateId = undefined;
      this._pendingUpdateTriggers = [];
      this._pendingTriggerContexts = undefined;
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

  _resolveItemizationName(device, statsMeta) {
    const explicitName = typeof device?.name === "string" ? device.name.trim() : "";
    if (explicitName) {
      return explicitName;
    }

    const id = typeof device?.stat === "string" ? device.stat : "";
    if (!id) {
      return "";
    }

    const deviceName = this._resolveDeviceNameFromEntity(id);
    if (deviceName) {
      return deviceName;
    }

    const friendlyName = this._hass?.states?.[id]?.attributes?.friendly_name;
    if (typeof friendlyName === "string" && friendlyName.trim()) {
      return friendlyName.trim();
    }

    const metadataName = typeof statsMeta?.name === "string" ? statsMeta.name.trim() : "";
    if (metadataName) {
      return metadataName;
    }

    return id;
  }

  _resolveDeviceNameFromEntity(entityId) {
    const entityEntry = this._hass?.entities?.[entityId];
    if (!entityEntry || typeof entityEntry !== "object") {
      return "";
    }
    const deviceId = entityEntry.device_id;
    if (typeof deviceId !== "string" || !deviceId) {
      return "";
    }
    const deviceEntry = this._hass?.devices?.[deviceId];
    if (!deviceEntry || typeof deviceEntry !== "object") {
      return "";
    }
    const userName =
      typeof deviceEntry.name_by_user === "string" ? deviceEntry.name_by_user.trim() : "";
    if (userName) {
      return userName;
    }
    const name = typeof deviceEntry.name === "string" ? deviceEntry.name.trim() : "";
    return name;
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

  _buildRangeTransition(bounds) {
    const startMs = bounds?.start?.getTime?.();
    const endMs = bounds?.end?.getTime?.();
    const prevStartMs = this._lastDebugBounds?.start ?? null;
    const prevEndMs = this._lastDebugBounds?.end ?? null;
    const result = {
      relation: "unknown",
      gapMs: null,
      previous: {
        start: this._formatDebugDateTime(prevStartMs),
        end: this._formatDebugDateTime(prevEndMs),
      },
      current: {
        start: this._formatDebugDateTime(startMs),
        end: this._formatDebugDateTime(endMs),
      },
    };

    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
      return result;
    }
    if (!Number.isFinite(prevStartMs) || !Number.isFinite(prevEndMs)) {
      this._lastDebugBounds = { start: startMs, end: endMs };
      result.relation = "initial";
      return result;
    }
    if (startMs === prevStartMs && endMs === prevEndMs) {
      result.relation = "same";
      this._lastDebugBounds = { start: startMs, end: endMs };
      return result;
    }
    if (startMs === prevEndMs + 1) {
      result.relation = "adjacent_forward";
      this._lastDebugBounds = { start: startMs, end: endMs };
      return result;
    }
    if (endMs + 1 === prevStartMs) {
      result.relation = "adjacent_backward";
      this._lastDebugBounds = { start: startMs, end: endMs };
      return result;
    }
    if (startMs <= prevEndMs && endMs >= prevStartMs) {
      result.relation = "overlap";
      this._lastDebugBounds = { start: startMs, end: endMs };
      return result;
    }
    if (startMs > prevEndMs) {
      result.relation = "gap_after_previous";
      result.gapMs = startMs - prevEndMs - 1;
      this._lastDebugBounds = { start: startMs, end: endMs };
      return result;
    }
    result.relation = "gap_before_previous";
    result.gapMs = prevStartMs - endMs - 1;
    this._lastDebugBounds = { start: startMs, end: endMs };
    return result;
  }

  _buildAdaptiveDebugSignature(payload) {
    return JSON.stringify(payload);
  }

  _logAdaptiveGraphDebug({
    updateMeta,
    bounds,
    bucketMs,
    devicePeriod,
    flowPeriod,
    deviceIds,
    flowAndCostIds,
    flowIds,
    overlayIds,
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
    const resolvedUpdateMeta = updateMeta || {
      primaryTrigger: "unspecified",
      finalTrigger: "unspecified",
      triggerChain: ["unspecified"],
      triggerContexts: {},
    };
    const rangeTransition = this._buildRangeTransition(bounds);
    const chartWidth = Number(this._chart?.clientWidth);
    const hostWidth = Number(this.clientWidth);

    const payload = {
      range: {
        start: this._formatDebugDateTime(bounds?.start?.getTime?.()),
        end: this._formatDebugDateTime(bounds?.end?.getTime?.()),
      },
      updateTrigger: resolvedUpdateMeta.finalTrigger,
      updatePrimaryTrigger: resolvedUpdateMeta.primaryTrigger,
      updateTriggerChain: resolvedUpdateMeta.triggerChain,
      updateTriggerContext: resolvedUpdateMeta.triggerContexts,
      rangeTransition,
      renderContext: {
        chartWidth: Number.isFinite(chartWidth) ? chartWidth : null,
        hostWidth: Number.isFinite(hostWidth) ? hostWidth : null,
      },
      bucketMs,
      period: {
        device: devicePeriod,
        flowAndCost: flowPeriod,
        price: "hour",
        temperature: "hour",
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

    const warnings = [];
    if ((overlayIds?.importCost?.length || overlayIds?.exportCompensation?.length) && !costPoints.length) {
      warnings.push({
        code: "cost_overlay_without_points",
        importCostIds: overlayIds.importCost,
        exportCompensationIds: overlayIds.exportCompensation,
      });
    }
    if (!untrackedPoints.length || !untrackedPoints.some((point) => Math.abs(Number(point?.[1]) || 0) > 1e-12)) {
      warnings.push({ code: "untracked_without_non_zero_points" });
    }

    const debugInfo = {
      source: "adaptive_graph",
      context: this._buildDebugContext(),
      payload,
      warnings,
    };
    this._latestAdaptiveDebugInfo = debugInfo;
    recordAdaptiveDebugInfo(debugInfo);
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
    const updateId = this._lastUpdateMeta?.updateId ?? null;
    const appliedRangeStart = this._latestAdaptiveDebugInfo?.payload?.range?.start?.iso || null;
    const appliedRangeEnd = this._latestAdaptiveDebugInfo?.payload?.range?.end?.iso || null;
    this._recordDebugEvent("update_applied", {
      update_id: updateId,
      range_start: appliedRangeStart,
      range_end: appliedRangeEnd,
      visible_series_count: visibleSeries.length,
    });
    this._chart.requestUpdate?.();
    const updateComplete = this._chart.updateComplete;
    if (updateComplete && typeof updateComplete.then === "function") {
      updateComplete
        .then(() => {
          this._recordDebugEvent("render_committed", {
            update_id: updateId,
            range_start: appliedRangeStart,
            range_end: appliedRangeEnd,
          });
        })
        .catch(() => {
          this._recordDebugEvent("render_commit_failed", {
            update_id: updateId,
          });
        });
    }

    this._renderCustomLegendTable(this._legendRows || [], hidden);
  }

  _showCardError(message) {
    this._setLoadingState(false);
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (emptyEl) {
      emptyEl.textContent = message;
      emptyEl.style.display = "block";
    }
    this._allSeries = [];
    this._chartOptions = {
      legend: { show: false, type: "custom" },
      xAxis: { type: "time" },
      yAxis: [{ type: "value" }],
      tooltip: { show: false },
    };
    this._legendRows = [];
    if (this._chart) {
      this._chart.hass = this._hass;
      this._chart.data = [];
      this._chart.options = this._chartOptions;
      this._chart.requestUpdate?.();
    }
    this._renderCustomLegendTable([], this._hiddenSeriesIds || new Set());
  }

  _setCardWarning(message) {
    const warningEl = this.shadowRoot?.querySelector("#warning");
    if (!warningEl) {
      return;
    }
    const text = typeof message === "string" ? message.trim() : "";
    warningEl.textContent = text;
    warningEl.style.display = text ? "block" : "none";
  }

  _setLoadingState(isLoading, text = "Loading consumption data...") {
    this._isLoading = isLoading === true;
    const emptyEl = this.shadowRoot?.querySelector("#empty");
    if (!emptyEl) {
      return;
    }
    if (this._isLoading) {
      emptyEl.textContent = text;
      emptyEl.style.display = "block";
      return;
    }
    if (emptyEl.textContent === text) {
      emptyEl.style.display = "none";
    }
  }

  _buildUpdateSignature(rangeKey, metrics) {
    const compact = {
      rangeKey,
      consumption: Array.isArray(metrics?.consumption) ? metrics.consumption : [],
      itemizations: Array.isArray(metrics?.itemizations)
        ? metrics.itemizations
            .map((entry) => ({ stat: entry?.stat || "", name: entry?.name || "" }))
            .sort((left, right) => String(left.stat).localeCompare(String(right.stat)))
        : [],
      cost: Array.isArray(metrics?.cost) ? metrics.cost : [],
      price: Array.isArray(metrics?.price) ? metrics.price : [],
      temperature: Array.isArray(metrics?.temperature) ? metrics.temperature : [],
      temperatureOverride: metrics?.temperature_override === true,
    };
    return JSON.stringify(compact);
  }

  _queueRetryForRange(rangeKey, delayMs = 800) {
    if (!rangeKey || this._pendingRetryRangeKey === rangeKey) {
      return;
    }
    this._pendingRetryRangeKey = rangeKey;
    window.setTimeout(() => {
      if (this._pendingRetryRangeKey !== rangeKey) {
        return;
      }
      this._pendingRetryRangeKey = null;
      this._scheduleUpdate("retry");
    }, delayMs);
  }

  _energyUnitToJouleFactor(unit) {
    const normalized = typeof unit === "string" ? unit.trim() : "";
    const factors = {
      J: 1,
      kJ: 1e3,
      MJ: 1e6,
      GJ: 1e9,
      cal: 4.184,
      kcal: 4184,
      Mcal: 4.184e6,
      Gcal: 4.184e9,
      mWh: 3.6,
      Wh: 3600,
      kWh: 3.6e6,
      MWh: 3.6e9,
      GWh: 3.6e12,
      TWh: 3.6e15,
    };
    return Number.isFinite(factors[normalized]) ? factors[normalized] : null;
  }

  _energyUnitConversionFactor(fromUnit, toUnit) {
    if (!fromUnit || !toUnit) {
      return null;
    }
    if (fromUnit === toUnit) {
      return 1;
    }
    const fromFactor = this._energyUnitToJouleFactor(fromUnit);
    const toFactor = this._energyUnitToJouleFactor(toUnit);
    if (!fromFactor || !toFactor) {
      return null;
    }
    return fromFactor / toFactor;
  }

  _normalizeStatsSeriesWithFactor(series, factor = 1) {
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
        return { start, change: change * factor };
      })
      .filter(Boolean)
      .sort((a, b) => a.start - b.start);
  }

  _serializeMap(map) {
    return Array.from((map || new Map()).entries())
      .map(([ts, value]) => [Number(ts), Number(value)])
      .sort((a, b) => a[0] - b[0]);
  }

  _downloadDebugInfo(debugInfo) {
    const payload = JSON.stringify(debugInfo, null, 2);
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download = `fortum-dashboard-debug-${stamp}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  _handleExportDebugInfoRequest(event) {
    if (!this._debugEnabled) {
      return;
    }
    const detail = event?.detail || {};
    if (detail.collectionKey && detail.collectionKey !== this._getCollectionKey()) {
      return;
    }
    const fallback = {
      source: "adaptive_graph",
      error: "No adaptive graph debug info available yet. Wait for chart data to load.",
    };
    const debugInfo = buildDashboardDebugExport({
      collectionKey: this._getCollectionKey(),
      hass: this._hass,
      adaptiveDebugInfo: this._latestAdaptiveDebugInfo || fallback,
      adaptiveExportData: this._latestAdaptiveExportData,
    });
    if (detail.download !== false) {
      this._downloadDebugInfo(debugInfo);
    }
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

    try {
      const data = this._collection?.state;
      const bounds = this._getBounds(data);
      if (!data || !bounds) {
        this._showCardError("Energy data is unavailable.");
        return;
      }

      const metrics = this._resolvedMetrics || {};
      const rangeKey = `${bounds.start.getTime()}:${bounds.end.getTime()}`;
      const updateSignature = this._buildUpdateSignature(rangeKey, metrics);
      if (updateSignature === this._lastRenderedUpdateSignature) {
        this._recordDebugEvent("update_skipped_same_signature", {
          update_id: this._lastUpdateMeta?.updateId ?? null,
          range_key: rangeKey,
        });
        return;
      }

      this._setLoadingState(true);
      const token = (this._token || 0) + 1;
      this._token = token;
      const itemizations = Array.isArray(metrics.itemizations) ? metrics.itemizations : [];
      const deviceIds = itemizations
        .map((device) => device?.stat)
        .filter((id) => typeof id === "string" && id.length);

      const consumptionIds = Array.isArray(metrics.consumption)
        ? metrics.consumption.filter((id) => typeof id === "string" && id.length)
        : [];
      if (!consumptionIds.length) {
        this._showCardError("No Fortum consumption source configured for single strategy.");
        return;
      }

      const energyMeta = await this._fetchStatsMetadata([...consumptionIds, ...deviceIds]);
      if (this._token !== token) {
        return;
      }

      const missingConsumptionIds = consumptionIds.filter((id) => !energyMeta?.[id]);
      if (missingConsumptionIds.length === consumptionIds.length) {
        const configuredPointNo = this._resolvedMetrics?.consumption?.[0]
          ?.replace("fortum:hourly_consumption_", "")
          ?.toUpperCase?.();
        const configuredHint = configuredPointNo
          ? `Configured metering point ${configuredPointNo} has no Fortum consumption data.`
          : "Configured Fortum metering point has no consumption data.";
        this._showCardError(`${configuredHint} Check strategy metering point number.`);
        return;
      }

      const missingDeviceIds = deviceIds.filter((id) => !energyMeta?.[id]);
      const primaryEnergyUnit = consumptionIds
        .map((id) => energyMeta?.[id]?.statistics_unit_of_measurement)
        .find((unit) => typeof unit === "string" && unit.length);

      const unknownUnitDevices = [];
      const validItemizations = itemizations.filter((device) => {
        const id = device?.stat;
        if (typeof id !== "string" || !id.length || missingDeviceIds.includes(id)) {
          return false;
        }
        const deviceUnit = energyMeta?.[id]?.statistics_unit_of_measurement;
        const factor = this._energyUnitConversionFactor(deviceUnit, primaryEnergyUnit);
        if (factor === null) {
          unknownUnitDevices.push(
            `${id} (${typeof deviceUnit === "string" && deviceUnit ? deviceUnit : "unknown unit"})`
          );
          return false;
        }
        return true;
      });

      const warnings = [];
      if (missingDeviceIds.length) {
        warnings.push(
          ...missingDeviceIds.map((id) => `Missing itemization statistic: ${id}.`)
        );
      }
      if (unknownUnitDevices.length) {
        warnings.push(
          ...unknownUnitDevices.map(
            (item) => `Excluded itemization statistic with unsupported unit conversion: ${item}.`
          )
        );
      }
      this._setCardWarning(warnings.join("\n"));

      const overlayIds = {
        importCost: Array.isArray(metrics.cost)
          ? metrics.cost.filter((id) => typeof id === "string" && id.length)
          : [],
        exportCompensation: [],
        price: Array.isArray(metrics.price)
          ? metrics.price.filter((id) => typeof id === "string" && id.length)
          : [],
        temperature: Array.isArray(metrics.temperature)
          ? metrics.temperature.filter((id) => typeof id === "string" && id.length)
          : [],
      };

      const flowIds = {
        fromGrid: consumptionIds,
        toGrid: [],
        solar: [],
        fromBattery: [],
        toBattery: [],
      };

    const widthPx = this._chart.clientWidth || this.clientWidth || 0;
    let bucketMs = this._pickBucketMs(bounds.start, bounds.end, widthPx, 15 * 60 * 1000);
    let devicePeriod = bucketMs <= 15 * 60 * 1000 ? "5minute" : "hour";
    const flowPeriod = "hour";
    const flowBucketMs = 60 * 60 * 1000;

    this._energyUnit =
      primaryEnergyUnit ||
      this._resolveEnergyUnit(data, [
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
        ...overlayIds.importCost,
      ])
    );

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

    this._costUnit = "";
    this._priceUnit = "";
    this._temperatureUnit = "";
    const overlayMetaIds = [
      ...overlayIds.importCost,
      ...overlayIds.exportCompensation,
      ...overlayIds.price,
      ...overlayIds.temperature,
    ];
    if (overlayMetaIds.length) {
      try {
        const overlayMeta = await this._fetchStatsMetadata(overlayMetaIds);
        if (this._token !== token) {
          return;
        }
        const firstCostUnit = [...overlayIds.importCost, ...overlayIds.exportCompensation]
          .map((id) => overlayMeta[id]?.statistics_unit_of_measurement)
          .find((unit) => typeof unit === "string" && unit.length);
        const firstPriceMeta = overlayIds.price
          .map((id) => overlayMeta[id])
          .find((item) => item?.statistics_unit_of_measurement);
        const firstTemperatureMeta = overlayIds.temperature
          .map((id) => overlayMeta[id])
          .find((item) => item?.statistics_unit_of_measurement);
        if (firstCostUnit) {
          this._costUnit = firstCostUnit;
        }
        if (firstPriceMeta?.statistics_unit_of_measurement) {
          this._priceUnit = firstPriceMeta.statistics_unit_of_measurement;
        }
        if (firstTemperatureMeta?.statistics_unit_of_measurement) {
          this._temperatureUnit = firstTemperatureMeta.statistics_unit_of_measurement;
        }
      } catch (_err) {
        this._costUnit = "";
        this._priceUnit = "";
        this._temperatureUnit = "";
      }
    }

    const deviceTotalsByTs = new Map();
    const series = validItemizations.map((device, index) => {
      const id = device.stat;
      const deviceUnit = energyMeta?.[id]?.statistics_unit_of_measurement;
      const factor = this._energyUnitConversionFactor(deviceUnit, this._energyUnit) || 1;
      const bucketed = this._bucketSeries(
        this._normalizeStatsSeriesWithFactor(deviceRaw[id], factor),
        bucketMs
      );
      this._mergeInto(deviceTotalsByTs, bucketed);
      const color = this._getGraphColorByIndex(index);
      return {
        id: `adaptive-${id}`,
        name: this._resolveItemizationName(device, energyMeta?.[id]),
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

    const mathBucketMs = bucketMs < flowBucketMs ? flowBucketMs : bucketMs;
    const fromGrid = new Map();
    const toGrid = new Map();
    const solar = new Map();
    const fromBattery = new Map();
    const toBattery = new Map();
    flowIds.fromGrid.forEach((id) =>
      this._mergeInto(fromGrid, this._bucketSeries(normalized[id] || [], mathBucketMs))
    );
    flowIds.toGrid.forEach((id) =>
      this._mergeInto(toGrid, this._bucketSeries(normalized[id] || [], mathBucketMs))
    );
    flowIds.solar.forEach((id) =>
      this._mergeInto(solar, this._bucketSeries(normalized[id] || [], mathBucketMs))
    );
    flowIds.fromBattery.forEach((id) =>
      this._mergeInto(fromBattery, this._bucketSeries(normalized[id] || [], mathBucketMs))
    );
    flowIds.toBattery.forEach((id) =>
      this._mergeInto(toBattery, this._bucketSeries(normalized[id] || [], mathBucketMs))
    );

    const usedTotalByMathBucket = new Map();
    const flowBuckets = new Set([
      ...fromGrid.keys(),
      ...toGrid.keys(),
      ...solar.keys(),
      ...fromBattery.keys(),
      ...toBattery.keys(),
    ]);
    flowBuckets.forEach((ts) => {
      const usedTotal =
        Math.max(fromGrid.get(ts) || 0, 0) +
        Math.max(solar.get(ts) || 0, 0) +
        Math.max(fromBattery.get(ts) || 0, 0) -
        Math.max(toGrid.get(ts) || 0, 0) -
        Math.max(toBattery.get(ts) || 0, 0);
      usedTotalByMathBucket.set(ts, usedTotal);
    });

    const deviceTotalsByMathBucket = new Map();
    deviceTotalsByTs.forEach((value, ts) => {
      const mathTs = this._bucketStart(ts, mathBucketMs);
      deviceTotalsByMathBucket.set(
        mathTs,
        (deviceTotalsByMathBucket.get(mathTs) || 0) + value
      );
    });

    const { totalConsumedByBucket, untrackedByBucket } = computeTotalAndUntrackedByBucket({
      usedTotalByMathBucket,
      deviceTotalsByMathBucket,
      bucketMs,
      flowBucketMs,
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
      const temperatureName = metrics?.temperature_override ? "Temperature (override)" : "Temperature";
      series.push({
        id: "adaptive-temperature-overlay",
        name: temperatureName,
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

      if (!series.some((entry) => Array.isArray(entry.data) && entry.data.length)) {
        const attempt = (this._rangeAttemptCounts?.[rangeKey] || 0) + 1;
        this._rangeAttemptCounts = {
          ...(this._rangeAttemptCounts || {}),
          [rangeKey]: attempt,
        };
        if (attempt < 2) {
          this._queueRetryForRange(rangeKey);
          return;
        }
        this._showCardError("No consumption data available for the selected range.");
        return;
      }

      this._rangeAttemptCounts = {
        ...(this._rangeAttemptCounts || {}),
        [rangeKey]: 0,
      };

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
      if (this._debugEnabled) {
        this._latestAdaptiveExportData = {
          generated_at: new Date().toISOString(),
          collection_key: this._getCollectionKey(),
          range: {
            start: bounds.start.toISOString(),
            end: bounds.end.toISOString(),
          },
          bucket_ms: bucketMs,
          period: {
            device: devicePeriod,
            flow_and_cost: flowPeriod,
            price: "hour",
            temperature: "hour",
          },
          ids: {
            device: deviceIds,
            flow: flowIds,
            overlay: overlayIds,
          },
          metadata: {
            energy: energyMeta,
            units: {
              energy: this._energyUnit,
              cost: this._costUnit,
              price: this._priceUnit,
              temperature: this._temperatureUnit,
            },
          },
          raw: {
            device: deviceRaw,
            flow_and_cost: flowRaw,
            price: priceRaw,
            temperature: temperatureRaw,
          },
          computed: {
            total_consumed_by_bucket: this._serializeMap(totalConsumedByBucket),
            device_totals_by_bucket: this._serializeMap(deviceTotalsByTs),
            untracked_by_bucket: this._serializeMap(untrackedByBucket),
          },
          series,
        };
      }
      this._initializeSeriesVisibility(series);
      this._logAdaptiveGraphDebug({
        updateMeta: this._lastUpdateMeta || {
          primaryTrigger: this._lastUpdateTrigger || "unspecified",
          finalTrigger: this._lastUpdateTrigger || "unspecified",
          triggerChain: [this._lastUpdateTrigger || "unspecified"],
          triggerContexts: {},
        },
        bounds,
        bucketMs,
        devicePeriod,
        flowPeriod,
        deviceIds,
        flowAndCostIds,
        flowIds,
        overlayIds,
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
      this._pendingRetryRangeKey = null;
      this._setLoadingState(false);
      this._lastRenderedUpdateSignature = updateSignature;
      this._applySeriesVisibility();
    } catch (err) {
      const message = err?.message || String(err);
      this._showCardError(`Failed to render adaptive graph: ${message}`);
    }
  }
}
