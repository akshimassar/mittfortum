const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

let editorState;

test.before(async () => {
  const modulePath = path.resolve(
    __dirname,
    "../../custom_components/fortum/frontend/strategy/editors/multipoint-strategy-editor-state.mjs"
  );
  editorState = await import(pathToFileURL(modulePath).href);
});

test("createMultipointEditorStateFromConfig captures points", () => {
  const state = editorState.createMultipointEditorStateFromConfig({
    type: "custom:fortum-energy-multipoint",
    debug: true,
    metering_points: [
      {
        number: "6094111",
        name: "Home",
        itemization: [{ stat: "sensor.sauna", name: "Sauna" }],
      },
    ],
  });

  assert.equal(state.debug, true);
  assert.equal(state.points.length, 1);
  assert.deepEqual(state.points[0], {
    number: "6094111",
    name: "Home",
    address: "",
    itemizationRows: [{ stat: "sensor.sauna", name: "Sauna" }],
  });
});

test("createMultipointEditorStateFromConfig seeds one empty point", () => {
  const state = editorState.createMultipointEditorStateFromConfig({
    type: "custom:fortum-energy-multipoint",
  });

  assert.equal(state.points.length, 1);
  assert.deepEqual(state.points[0], {
    number: "",
    name: "",
    address: "",
    itemizationRows: [],
  });
});

test("buildMultipointConfigFromEditorState preserves unknown keys", () => {
  const config = editorState.buildMultipointConfigFromEditorState({
    baseConfig: {
      type: "custom:fortum-energy-multipoint",
      collection_key: "energy_custom",
      itemization: [{ stat: "sensor.old" }],
    },
    debug: false,
    points: [
      {
        number: " 6094111 ",
        name: " Home ",
        address: " Test Street 1 ",
        itemizationRows: [{ stat: " sensor.sauna ", name: " Sauna " }],
      },
    ],
  });

  assert.deepEqual(config, {
    type: "custom:fortum-energy-multipoint",
    collection_key: "energy_custom",
    metering_points: [
      {
        number: "6094111",
        name: "Home",
        address: "Test Street 1",
        itemization: [{ stat: "sensor.sauna", name: "Sauna" }],
      },
    ],
  });
});

test("buildMultipointConfigFromEditorState allows empty itemization", () => {
  const config = editorState.buildMultipointConfigFromEditorState({
    baseConfig: { type: "custom:fortum-energy-multipoint" },
    debug: true,
    points: [
      {
        number: "6094111",
        name: "",
        address: "",
        itemizationRows: [{ stat: "   ", name: "ignored" }],
      },
    ],
  });

  assert.deepEqual(config, {
    type: "custom:fortum-energy-multipoint",
    debug: true,
    metering_points: [
      {
        number: "6094111",
        itemization: [],
      },
    ],
  });
});
