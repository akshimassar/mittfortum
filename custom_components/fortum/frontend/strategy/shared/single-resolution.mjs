const CONSUMPTION_ID_PATTERN = /^fortum:hourly_consumption_[a-z0-9_]+$/i;
const FORECAST_ID_PATTERN = /^fortum:price_forecast_[a-z0-9_]+$/i;

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

const toStatisticIdList = (rawItems) =>
  Array.from(
    new Set(
      (Array.isArray(rawItems) ? rawItems : [])
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          return item?.statistic_id;
        })
        .filter((value) => typeof value === "string" && value.length)
    )
  );

const sanitizeMeteringPointSuffix = (meteringPointNo) => {
  const suffix = String(meteringPointNo || "")
    .trim()
    .toLowerCase()
    .replace(/[^0-9a-z_]/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!suffix) {
    throw new Error("Invalid fortum.metering_point_number value.");
  }
  return suffix;
};

export const buildFortumStatisticId = (metric, meteringPointNo) =>
  `fortum:hourly_${metric}_${sanitizeMeteringPointSuffix(meteringPointNo)}`;

export const deriveFortumRelatedStatisticIds = (consumptionId) => {
  if (typeof consumptionId !== "string" || !CONSUMPTION_ID_PATTERN.test(consumptionId)) {
    throw new Error(`Invalid Fortum consumption statistic id: ${consumptionId || "<empty>"}`);
  }
  return {
    consumption: consumptionId,
    cost: consumptionId.replace("hourly_consumption_", "hourly_cost_"),
    price: consumptionId.replace("hourly_consumption_", "hourly_price_"),
    temperature: consumptionId.replace("hourly_consumption_", "hourly_temperature_"),
  };
};

export const normalizeItemization = (raw) =>
  (Array.isArray(raw) ? raw : [])
    .map((entry) => {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      const stat = typeof entry.stat === "string" ? entry.stat.trim() : "";
      if (!stat) {
        return null;
      }
      const name = typeof entry.name === "string" ? entry.name.trim() : "";
      return {
        stat,
        ...(name ? { name } : {}),
      };
    })
    .filter(Boolean);

const normalizePrefsItemization = (raw) =>
  (Array.isArray(raw) ? raw : [])
    .map((entry) => {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      const stat =
        typeof entry.stat_consumption === "string" ? entry.stat_consumption.trim() : "";
      if (!stat) {
        return null;
      }
      const name = typeof entry.name === "string" ? entry.name.trim() : "";
      return {
        stat,
        ...(name ? { name } : {}),
      };
    })
    .filter(Boolean);

const discoverFortumConsumptionIds = (statisticIds) =>
  toStatisticIdList(statisticIds).filter((id) => CONSUMPTION_ID_PATTERN.test(id)).sort();

const discoverFortumForecastIds = (statisticIds) =>
  toStatisticIdList(statisticIds).filter((id) => FORECAST_ID_PATTERN.test(id)).sort();

export const resolveSingleStrategyMetrics = ({ config, prefs, statisticIds }) => {
  const fortumConfig = config?.fortum;
  const yamlMeteringPointNo =
    typeof fortumConfig?.metering_point_number === "string"
      ? fortumConfig.metering_point_number.trim()
      : "";

  let source = "auto";
  let baseIds;
  if (yamlMeteringPointNo) {
    source = "yaml";
    baseIds = {
      consumption: buildFortumStatisticId("consumption", yamlMeteringPointNo),
      cost: buildFortumStatisticId("cost", yamlMeteringPointNo),
      price: buildFortumStatisticId("price", yamlMeteringPointNo),
      temperature: buildFortumStatisticId("temperature", yamlMeteringPointNo),
    };
  } else {
    const autoConsumptionIds = discoverFortumConsumptionIds(statisticIds);
    if (!autoConsumptionIds.length) {
      throw new Error(
        "No Fortum metering point statistics found. Set strategy.fortum.metering_point_number."
      );
    }
    if (autoConsumptionIds.length > 1) {
      throw new Error(
        `Single strategy found multiple Fortum metering points: ${autoConsumptionIds.join(", "
        )}. Set strategy.fortum.metering_point_number.`
      );
    }
    baseIds = deriveFortumRelatedStatisticIds(autoConsumptionIds[0]);
  }

  let itemizations;
  if (hasOwn(config, "itemization")) {
    if (!Array.isArray(config.itemization)) {
      throw new Error("strategy.itemization must be a list when provided.");
    }
    itemizations = normalizeItemization(config.itemization);
  } else {
    itemizations = normalizePrefsItemization(prefs?.device_consumption);
  }

  return {
    source,
    metrics: {
      consumption: [baseIds.consumption],
      cost: [baseIds.cost],
      price: [baseIds.price],
      temperature: [baseIds.temperature],
      itemizations,
      price_forecast: discoverFortumForecastIds(statisticIds),
    },
  };
};
