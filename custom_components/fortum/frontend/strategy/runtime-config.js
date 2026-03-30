const EMPTY_PREFS = {
  energy_sources: [],
  device_consumption: [],
  device_consumption_water: [],
};

const isFortumConsumptionStatId = (statId) =>
  typeof statId === "string" && /^[^:]*fortum:hourly_consumption_/.test(statId);

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
  } else {
    (safePrefs.energy_sources || []).forEach((source) => {
      if (source.type === "grid") {
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
  }

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

const runtimeConfigApi = {
  EMPTY_PREFS,
  normalizeEnergySourceOverrides,
  deriveEnergyRuntimeConfig,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = runtimeConfigApi;
}

if (typeof globalThis !== "undefined") {
  globalThis.__fortumEnergyRuntimeConfig = runtimeConfigApi;
}
