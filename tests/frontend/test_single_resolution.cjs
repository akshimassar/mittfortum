const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

let hooks;

test.before(async () => {
  const modulePath = path.resolve(
    __dirname,
    "../../custom_components/fortum/frontend/strategy/shared/single-resolution.mjs"
  );
  hooks = await import(pathToFileURL(modulePath).href);
});

test("uses YAML metering point and explicit empty itemization", () => {
  const result = hooks.resolveSingleStrategyMetrics({
    config: {
      fortum: { metering_point_number: "MP-12/34" },
      itemization: [],
    },
    prefs: {
      device_consumption: [{ name: "Should", stat_consumption: "sensor.unused" }],
    },
    statisticIds: ["fortum:price_forecast_fi"],
  });

  assert.equal(result.source, "yaml");
  assert.deepEqual(result.metrics.consumption, ["fortum:hourly_consumption_mp_12_34"]);
  assert.deepEqual(result.metrics.cost, ["fortum:hourly_cost_mp_12_34"]);
  assert.deepEqual(result.metrics.price, ["fortum:hourly_price_mp_12_34"]);
  assert.deepEqual(result.metrics.temperature, ["fortum:hourly_temperature_mp_12_34"]);
  assert.deepEqual(result.metrics.itemizations, []);
  assert.deepEqual(result.metrics.price_forecast, ["fortum:price_forecast_fi"]);
});

test("falls back to auto Fortum source and prefs itemization", () => {
  const result = hooks.resolveSingleStrategyMetrics({
    config: {},
    prefs: {
      device_consumption: [
        { name: "Sauna", stat_consumption: "sensor.sauna" },
        { name: "", stat_consumption: "sensor.boiler" },
      ],
    },
    statisticIds: [
      { statistic_id: "fortum:hourly_consumption_6094111" },
      { statistic_id: "fortum:price_forecast_fi" },
      { statistic_id: "fortum:price_forecast_se1" },
    ],
  });

  assert.equal(result.source, "auto");
  assert.deepEqual(result.metrics.consumption, ["fortum:hourly_consumption_6094111"]);
  assert.deepEqual(result.metrics.cost, ["fortum:hourly_cost_6094111"]);
  assert.deepEqual(result.metrics.price, ["fortum:hourly_price_6094111"]);
  assert.deepEqual(result.metrics.temperature, ["fortum:hourly_temperature_6094111"]);
  assert.deepEqual(result.metrics.itemizations, [
    { name: "Sauna", stat_consumption: "sensor.sauna" },
    { stat_consumption: "sensor.boiler" },
  ]);
  assert.deepEqual(result.metrics.price_forecast, [
    "fortum:price_forecast_fi",
    "fortum:price_forecast_se1",
  ]);
});

test("throws when auto discovery finds multiple metering points", () => {
  assert.throws(
    () =>
      hooks.resolveSingleStrategyMetrics({
        config: {},
        prefs: { device_consumption: [] },
        statisticIds: [
          "fortum:hourly_consumption_111",
          "fortum:hourly_consumption_222",
        ],
      }),
    /multiple Fortum metering points/i
  );
});

test("throws when itemization is not a list", () => {
  assert.throws(
    () =>
      hooks.resolveSingleStrategyMetrics({
        config: { itemization: { stat_consumption: "sensor.bad" } },
        prefs: {},
        statisticIds: ["fortum:hourly_consumption_123"],
      }),
    /strategy\.itemization must be a list/i
  );
});
