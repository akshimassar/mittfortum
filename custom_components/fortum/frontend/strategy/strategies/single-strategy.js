import { DEFAULT_COLLECTION_KEY } from "/fortum-energy-static/strategy/shared/constants.js";
import { fetchEnergyPrefs } from "/fortum-energy-static/strategy/shared/energy-prefs.js";
import { localize } from "/fortum-energy-static/strategy/shared/formatters.js";
import { validateSingleStrategyConfig } from "/fortum-energy-static/strategy/shared/config-validation.mjs";
import { resolveSingleStrategyMetrics } from "/fortum-energy-static/strategy/shared/single-resolution.mjs";

const buildSettingsView = (hass) => ({
  title: localize(hass, "ui.panel.config.energy.caption", "Settings"),
  path: "settings",
  icon: "mdi:cog",
  cards: [
    {
      type: "custom:fortum-energy-settings-redirect-card",
    },
  ],
});

const buildElectricityViewConfig = (
  collectionKey,
  hass,
  debug = false,
  resolvedMetrics,
  viewTitle
) => {
  const view = {
    title:
      typeof viewTitle === "string" && viewTitle.trim()
        ? viewTitle.trim()
        : localize(hass, "ui.panel.energy.title.electricity", "Electricity"),
    path: "electricity",
    type: "sections",
    sections: [],
  };

  const mainCards = [];

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  mainCards.push({
    title: localize(
      hass,
      "ui.panel.energy.cards.energy_date_selection_title",
      "Time range"
    ),
    type: "energy-date-selection",
    collection_key: collectionKey,
    disable_compare: true,
    opening_direction: "right",
    vertical_opening_direction: "down",
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:fortum-energy-quick-ranges-card",
    collection_key: collectionKey,
    debug,
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  mainCards.push({
    type: "custom:fortum-energy-devices-adaptive-graph-card",
    collection_key: collectionKey,
    debug,
    resolved_metrics: resolvedMetrics,
    grid_options: { columns: 36 },
  });

  mainCards.push({
    title: "Price of Tomorrow",
    type: "custom:fortum-energy-future-price-card",
    collection_key: collectionKey,
    debug,
    resolved_metrics: resolvedMetrics,
    grid_options: { columns: 36 },
  });

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
  });

  view.sections.push({
    type: "grid",
    column_span: 3,
    cards: mainCards,
  });

  return view;
};

export class FortumEnergySingleDashboardStrategy extends HTMLElement {
  static async generate(config, hass) {
    try {
      const validatedConfig = validateSingleStrategyConfig(config || {});
      const collectionKey =
        validatedConfig.collection_key ||
        validatedConfig.collectionKey ||
        DEFAULT_COLLECTION_KEY;
      const debug = validatedConfig.debug === true;
      const prefs = await fetchEnergyPrefs(hass);
      const hasYamlMeteringPoint =
        typeof validatedConfig?.fortum?.metering_point_number === "string" &&
        validatedConfig.fortum.metering_point_number.trim().length > 0;

      let statisticIds = [];
      try {
        statisticIds = await hass.callWS({ type: "recorder/list_statistic_ids" });
      } catch (err) {
        if (!hasYamlMeteringPoint) {
          throw err;
        }
      }

      const { metrics: resolvedMetrics } = resolveSingleStrategyMetrics({
        config: validatedConfig,
        prefs,
        statisticIds,
      });

      return {
        views: [
          buildElectricityViewConfig(
            collectionKey,
            hass,
            debug,
            resolvedMetrics,
            validatedConfig.electricity_title
          ),
          buildSettingsView(hass),
        ],
      };
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
                content: `Error loading fortum-energy strategy:\n> ${message}`,
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

export class FortumEnergyDashboardStrategy extends FortumEnergySingleDashboardStrategy {}
