import { DEFAULT_COLLECTION_KEY, EMPTY_PREFS } from "/fortum-energy-static/strategy/shared/constants.js";

const isFortumConsumptionStatId = (statId) =>
  typeof statId === "string" && /^[^:]*fortum:hourly_consumption_/.test(statId);

export class FortumEnergyDevicesDetailOverlayCard extends HTMLElement {
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

      const importFlows = Array.isArray(source.flow_from) ? source.flow_from : [source];
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
          flow.stat_compensation || flow.stat_cost || info.cost_sensors[flow.stat_energy_to];
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
      (id) => id && !this._externalPriceMeta?.[id] && !this._externalPriceMetaInflight?.has(id)
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
      (id) => id && !this._externalPriceStats?.[id] && !this._externalPriceInflight?.has(id)
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

      const importFlows = Array.isArray(source.flow_from) ? source.flow_from : [source];

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

    detailCard._chartData = detailCard._chartData.filter(
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
    const detailCard = this._innerCard?.querySelector("hui-energy-devices-detail-graph-card");
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
                  `${this._escapeRegExp(label)}: <div style="direction:ltr; display: inline;">[^<]*?<\\/div>`
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
