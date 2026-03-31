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
    fortum: { metering_point_number: " 6094111 " },
    itemization: [{ stat_consumption: "sensor.sauna", name: "Sauna" }],
  });

  assert.equal(cfg.fortum.metering_point_number, "6094111");
  assert.deepEqual(cfg.itemization, [{ stat_consumption: "sensor.sauna", name: "Sauna" }]);
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
