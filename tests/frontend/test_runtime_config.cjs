const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

globalThis.HTMLElement = globalThis.HTMLElement || class {};

require(path.resolve(
  __dirname,
  "../../custom_components/fortum/frontend/strategy/runtime-config.js"
));

const hooks = globalThis.__fortumEnergyRuntimeConfig;

const toPriceId = (consumptionStatId) => {
  if (typeof consumptionStatId !== "string") {
    return null;
  }
  if (!/^[^:]*fortum:hourly_consumption_/.test(consumptionStatId)) {
    return null;
  }
  return consumptionStatId.replace("hourly_consumption_", "hourly_price_");
};

const toTemperatureId = (consumptionStatId) => {
  if (typeof consumptionStatId !== "string") {
    return null;
  }
  if (!/^[^:]*fortum:hourly_consumption_/.test(consumptionStatId)) {
    return null;
  }
  return consumptionStatId.replace("hourly_consumption_", "hourly_temperature_");
};

const toForecastId = (priceStatId) => {
  if (typeof priceStatId !== "string") {
    return null;
  }
  if (!priceStatId.includes("hourly_price_")) {
    return null;
  }
  return null;
};

const gridImportFlows = (source) => {
  if (Array.isArray(source?.flow_from) && source.flow_from.length) {
    return source.flow_from;
  }
  return [source];
};

const gridExportFlows = (source) => {
  if (Array.isArray(source?.flow_to) && source.flow_to.length) {
    return source.flow_to;
  }
  return [source];
};

const buildLegacyExpectedConfig = (prefs, info) => {
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

  (prefs?.energy_sources || []).forEach((source) => {
    if (source.type === "grid") {
      gridImportFlows(source).forEach((flow) => {
        if (!flow?.stat_energy_from) {
          return;
        }
        flowIds.fromGrid.push(flow.stat_energy_from);
        const costId = flow.stat_cost || info.cost_sensors[flow.stat_energy_from];
        if (costId) {
          overlayIds.importCost.push(costId);
        }
        const priceId = toPriceId(flow.stat_energy_from);
        if (priceId) {
          overlayIds.price.push(priceId);
          const forecastId = toForecastId(priceId);
          if (forecastId) {
            forecastIds.push(forecastId);
          }
        }
        const temperatureId = toTemperatureId(flow.stat_energy_from);
        if (temperatureId) {
          overlayIds.temperature.push(temperatureId);
        }
      });

      gridExportFlows(source).forEach((flow) => {
        if (!flow?.stat_energy_to) {
          return;
        }
        flowIds.toGrid.push(flow.stat_energy_to);
        const compensationId =
          flow.stat_compensation ||
          flow.stat_cost ||
          info.cost_sensors[flow.stat_energy_to];
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

test("runtime config hooks are exposed", () => {
  assert.ok(hooks);
  assert.equal(typeof hooks.deriveEnergyRuntimeConfig, "function");
});

test("deriveEnergyRuntimeConfig matches legacy prefs-only behavior", () => {
  const prefs = {
    energy_sources: [
      {
        type: "grid",
        flow_from: [
          {
            stat_energy_from: "fortum:hourly_consumption_111",
            stat_cost: "fortum:hourly_cost_111",
          },
          {
            stat_energy_from: "sensor.random_non_fortum_consumption",
          },
        ],
        flow_to: [
          {
            stat_energy_to: "sensor.export_grid_total",
            stat_compensation: "sensor.export_comp",
          },
        ],
      },
      {
        type: "solar",
        stat_energy_from: "sensor.solar_total",
      },
      {
        type: "battery",
        stat_energy_from: "sensor.battery_discharge",
        stat_energy_to: "sensor.battery_charge",
      },
    ],
  };

  const info = {
    cost_sensors: {
      "sensor.random_non_fortum_consumption": "sensor.random_non_fortum_cost",
      "sensor.export_grid_total": "sensor.export_cost_from_info",
    },
  };

  const expected = buildLegacyExpectedConfig(prefs, info);
  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs,
    info,
    overrides: undefined,
    strictOverride: false,
  });

  assert.equal(actual.source, "prefs");
  assert.deepEqual(actual.flowIds, expected.flowIds);
  assert.deepEqual(actual.overlayIds, expected.overlayIds);
  assert.deepEqual(actual.forecastIds, expected.forecastIds);
  assert.deepEqual(actual.issues, []);
});

test("strict override mode does not fallback to prefs", () => {
  const prefs = {
    energy_sources: [
      {
        type: "grid",
        stat_energy_from: "fortum:hourly_consumption_999",
      },
    ],
  };

  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs,
    info: { cost_sensors: {} },
    overrides: [],
    strictOverride: true,
  });

  assert.equal(actual.source, "override");
  assert.deepEqual(actual.flowIds.fromGrid, []);
  assert.deepEqual(actual.overlayIds.importCost, []);
  assert.deepEqual(actual.forecastIds, []);
  assert.ok(actual.issues.includes("override_provided_but_no_valid_energy_sources"));
});

test("strict override mode derives IDs from override only", () => {
  const prefs = {
    energy_sources: [
      {
        type: "grid",
        stat_energy_from: "fortum:hourly_consumption_999",
        stat_cost: "fortum:hourly_cost_999",
      },
      {
        type: "solar",
        stat_energy_from: "sensor.solar_should_be_ignored",
      },
    ],
  };

  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs,
    info: {
      cost_sensors: {
        "fortum:hourly_consumption_777": "fortum:hourly_cost_777",
      },
    },
    overrides: [
      {
        stat_energy_from: "fortum:hourly_consumption_777",
      },
    ],
    strictOverride: true,
  });

  assert.equal(actual.source, "override");
  assert.deepEqual(actual.flowIds.fromGrid, ["fortum:hourly_consumption_777"]);
  assert.deepEqual(actual.overlayIds.importCost, ["fortum:hourly_cost_777"]);
  assert.deepEqual(actual.overlayIds.price, ["fortum:hourly_price_777"]);
  assert.deepEqual(actual.overlayIds.temperature, ["fortum:hourly_temperature_777"]);
  assert.deepEqual(actual.forecastIds, []);
  assert.deepEqual(actual.issues, []);
});

test("override with non-fortum stat does not derive price/forecast", () => {
  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs: { energy_sources: [] },
    info: { cost_sensors: {} },
    overrides: [
      {
        stat_energy_from: "sensor.some_other_consumption_stat",
      },
    ],
    strictOverride: true,
  });

  assert.equal(actual.source, "override");
  assert.deepEqual(actual.flowIds.fromGrid, ["sensor.some_other_consumption_stat"]);
  assert.deepEqual(actual.overlayIds.price, []);
  assert.deepEqual(actual.overlayIds.temperature, []);
  assert.deepEqual(actual.forecastIds, []);
});

test("deduplicates repeated IDs across prefs flows", () => {
  const prefs = {
    energy_sources: [
      {
        type: "grid",
        flow_from: [
          {
            stat_energy_from: "fortum:hourly_consumption_123",
            stat_cost: "fortum:hourly_cost_123",
          },
          {
            stat_energy_from: "fortum:hourly_consumption_123",
            stat_cost: "fortum:hourly_cost_123",
          },
        ],
      },
    ],
  };

  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs,
    info: { cost_sensors: {} },
    overrides: undefined,
    strictOverride: false,
  });

  assert.deepEqual(actual.flowIds.fromGrid, ["fortum:hourly_consumption_123"]);
  assert.deepEqual(actual.overlayIds.importCost, ["fortum:hourly_cost_123"]);
  assert.deepEqual(actual.overlayIds.price, ["fortum:hourly_price_123"]);
  assert.deepEqual(actual.overlayIds.temperature, ["fortum:hourly_temperature_123"]);
  assert.deepEqual(actual.forecastIds, []);
});

test("export compensation resolves in expected precedence", () => {
  const prefs = {
    energy_sources: [
      {
        type: "grid",
        flow_to: [
          {
            stat_energy_to: "sensor.to_grid_a",
            stat_compensation: "sensor.comp_a",
            stat_cost: "sensor.cost_a",
          },
          {
            stat_energy_to: "sensor.to_grid_b",
            stat_cost: "sensor.cost_b",
          },
          {
            stat_energy_to: "sensor.to_grid_c",
          },
        ],
      },
    ],
  };

  const actual = hooks.deriveEnergyRuntimeConfig({
    prefs,
    info: {
      cost_sensors: {
        "sensor.to_grid_c": "sensor.info_cost_c",
      },
    },
    overrides: undefined,
    strictOverride: false,
  });

  assert.deepEqual(actual.overlayIds.exportCompensation, [
    "sensor.comp_a",
    "sensor.cost_b",
    "sensor.info_cost_c",
  ]);
});
