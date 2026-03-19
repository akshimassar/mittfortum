const DEFAULT_COLLECTION_KEY = "energy_my_energy_dashboard";

const EMPTY_PREFS = {
  energy_sources: [],
  device_consumption: [],
  device_consumption_water: [],
};

const localize = (hass, key, fallback) => hass.localize?.(key) || fallback;

const fetchEnergyPrefs = async (hass) => {
  try {
    const prefs = await hass.callWS({ type: "energy/get_prefs" });
    return prefs || EMPTY_PREFS;
  } catch (err) {
    if (err && err.code === "not_found") {
      return EMPTY_PREFS;
    }
    throw err;
  }
};

const hasAnyEnergyPrefs = (prefs) =>
  prefs &&
  (prefs.device_consumption.length > 0 || prefs.energy_sources.length > 0);

const buildSetupView = () => ({
  title: "Setup",
  path: "setup",
  cards: [
    {
      type: "markdown",
      content:
        "No Energy preferences found yet. Open **Settings -> Dashboards -> Energy** and complete setup first.",
    },
  ],
});

const buildElectricityViewConfig = (prefs, collectionKey, hass) => {
  const view = {
    title: localize(hass, "ui.panel.energy.title.electricity", "Electricity"),
    path: "electricity",
    type: "sections",
    sections: [],
  };

  const hasGrid = prefs.energy_sources.some(
    (source) =>
      source.type === "grid" &&
      (!!source.stat_energy_from || !!source.stat_energy_to)
  );
  const hasSolar = prefs.energy_sources.some((source) => source.type === "solar");
  const hasBattery = prefs.energy_sources.some(
    (source) => source.type === "battery"
  );

  const mainCards = [];

  mainCards.push({
    type: "energy-compare",
    collection_key: collectionKey,
    grid_options: { columns: 36 },
  });

  if (hasGrid || hasBattery) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_usage_graph_title",
        "Energy usage"
      ),
      type: "energy-usage-graph",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  if (hasSolar) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_solar_graph_title",
        "Solar production"
      ),
      type: "energy-solar-graph",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  if (hasGrid || hasSolar || hasBattery) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_sources_table_title",
        "Energy sources"
      ),
      type: "energy-sources-table",
      collection_key: collectionKey,
      types: ["grid", "solar", "battery"],
      grid_options: { columns: 36 },
    });
  }

  if (prefs.device_consumption.length) {
    mainCards.push({
      title: localize(
        hass,
        "ui.panel.energy.cards.energy_devices_detail_graph_title",
        "Individual devices"
      ),
      type: "energy-devices-detail-graph",
      collection_key: collectionKey,
      grid_options: { columns: 36 },
    });
  }

  mainCards.push({
    title: localize(
      hass,
      "ui.panel.energy.cards.energy_date_selection_title",
      "Time range"
    ),
    type: "energy-date-selection",
    collection_key: collectionKey,
    opening_direction: "right",
    vertical_opening_direction: "up",
    grid_options: { columns: 36 },
  });

  view.sections.push({
    type: "grid",
    column_span: 3,
    cards: mainCards,
  });

  return view;
};

class MyEnergyDashboardStrategy {
  static async generate(config, hass) {
    try {
      const collectionKey =
        config.collection_key || config.collectionKey || DEFAULT_COLLECTION_KEY;
      const prefs = await fetchEnergyPrefs(hass);

      if (!hasAnyEnergyPrefs(prefs)) {
        return { views: [buildSetupView()] };
      }

      return { views: [buildElectricityViewConfig(prefs, collectionKey, hass)] };
    } catch (err) {
      const message = err && err.message ? err.message : String(err);
      return {
        views: [
          {
            title: "Error",
            path: "error",
            cards: [
              {
                type: "markdown",
                content: `Error loading my-energy strategy:\n> ${message}`,
              },
            ],
          },
        ],
      };
    }
  }

  static async generateDashboard(args) {
    return this.generate(args.strategy || {}, args.hass);
  }
}

const registerIfNeeded = (tag, klass) => {
  if (!customElements.get(tag)) {
    customElements.define(tag, klass);
  }
};

registerIfNeeded("ll-strategy-dashboard-my-energy", MyEnergyDashboardStrategy);
registerIfNeeded("ll-strategy-my-energy", MyEnergyDashboardStrategy);
