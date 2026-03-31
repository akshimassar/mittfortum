const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

let validation;

test.before(async () => {
  const modulePath = path.resolve(
    __dirname,
    "../../custom_components/fortum/frontend/strategy/shared/config-validation.mjs"
  );
  validation = await import(pathToFileURL(modulePath).href);
});

test("validates single config with metering_point_number", () => {
  const cfg = validation.validateSingleStrategyConfig({
    debug: true,
    fortum: { metering_point_number: " 6094111 " },
    itemization: [{ stat: "sensor.sauna", name: "Sauna" }],
  });

  assert.equal(cfg.debug, true);
  assert.equal(cfg.fortum.metering_point_number, "6094111");
  assert.deepEqual(cfg.itemization, [{ stat: "sensor.sauna", name: "Sauna" }]);
});

test("rejects non-boolean debug value", () => {
  assert.throws(
    () => validation.validateSingleStrategyConfig({ debug: "yes" }),
    /strategy\.debug must be a boolean/i
  );
});

test("validates multipoint config with optional name", () => {
  const cfg = validation.validateMultipointStrategyConfig({
    metering_points: [
      {
        number: "6094111",
        address: "Street 1, City",
        itemization: [],
      },
    ],
  });

  assert.deepEqual(cfg.metering_points, [
    {
      number: "6094111",
      address: "Street 1, City",
      itemization: [],
    },
  ]);
});

test("rejects multipoint config without itemization", () => {
  assert.throws(
    () =>
      validation.validateMultipointStrategyConfig({
        metering_points: [{ number: "6094111" }],
      }),
    /itemization must be a list/i
  );
});

test("rejects legacy stat_consumption key in strategy itemization", () => {
  assert.throws(
    () =>
      validation.validateSingleStrategyConfig({
        itemization: [{ stat_consumption: "sensor.sauna" }],
      }),
    /strategy\.itemization\[0\]\.stat/i
  );
});
