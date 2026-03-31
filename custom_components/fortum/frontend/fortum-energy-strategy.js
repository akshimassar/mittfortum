import { FortumEnergyCustomLegendCard } from "/fortum-energy-static/strategy/cards/custom-legend-card.js";
import { FortumEnergyDevicesDetailOverlayCard } from "/fortum-energy-static/strategy/cards/devices-detail-overlay-card.js";
import { FortumEnergyDevicesAdaptiveGraphCard } from "/fortum-energy-static/strategy/cards/devices-adaptive-graph-card.js";
import { FortumEnergyFuturePriceCard } from "/fortum-energy-static/strategy/cards/future-price-card.js";
import { FortumEnergySettingsRedirectCard } from "/fortum-energy-static/strategy/cards/settings-redirect-card.js";
import { FortumEnergyQuickRangesCard } from "/fortum-energy-static/strategy/cards/quick-ranges-card.js";
import { FortumEnergySpacerCard } from "/fortum-energy-static/strategy/cards/spacer-card.js";
import { DEFAULT_COLLECTION_KEY } from "/fortum-energy-static/strategy/shared/constants.js";
import { fetchEnergyPrefs } from "/fortum-energy-static/strategy/shared/energy-prefs.js";
import { deriveEnergyRuntimeConfig, normalizeEnergySourceOverrides } from "/fortum-energy-static/strategy/runtime-config.mjs";
import { localize } from "/fortum-energy-static/strategy/shared/formatters.js";

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
  prefs,
  collectionKey,
  hass,
  debug = false,
  energySources = []
) => {
  const view = {
    title: localize(hass, "ui.panel.energy.title.electricity", "Electricity"),
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
    grid_options: { columns: 12 },
  });

  mainCards.push({
    type: "custom:fortum-energy-spacer-card",
    grid_options: { columns: 6 },
  });

  if (prefs.device_consumption.length) {
    mainCards.push({
      type: "custom:fortum-energy-devices-adaptive-graph-card",
      collection_key: collectionKey,
      debug,
      energy_sources: energySources,
      grid_options: { columns: 36 },
    });

    mainCards.push({
      title: "Price of Tomorrow",
      type: "custom:fortum-energy-future-price-card",
      collection_key: collectionKey,
      debug,
      energy_sources: energySources,
      grid_options: { columns: 36 },
    });
  }

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

class FortumEnergyDashboardStrategy extends HTMLElement {
  static async generate(config, hass) {
    try {
      const collectionKey =
        config.collection_key || config.collectionKey || DEFAULT_COLLECTION_KEY;
      const debug = config.debug === true;
      const energySources = normalizeEnergySourceOverrides(config.energy_sources);
      const prefs = await fetchEnergyPrefs(hass);

      if (!hasAnyEnergyPrefs(prefs)) {
        return { views: [buildSetupView(), buildSettingsView(hass)] };
      }

      return {
        views: [
          buildElectricityViewConfig(prefs, collectionKey, hass, debug, energySources),
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

const registerIfNeeded = (tag, klass) => {
  if (typeof customElements === "undefined") {
    return;
  }
  if (!customElements.get(tag)) {
    customElements.define(tag, klass);
  }
};

if (typeof process !== "undefined" && process?.versions?.node) {
  globalThis.__fortumEnergyStrategyTestHooks = {
    normalizeEnergySourceOverrides,
    deriveEnergyRuntimeConfig,
  };
}

registerIfNeeded(
  "fortum-energy-custom-legend-card",
  FortumEnergyCustomLegendCard
);
registerIfNeeded("fortum-energy-spacer-card", FortumEnergySpacerCard);
registerIfNeeded("fortum-energy-quick-ranges-card", FortumEnergyQuickRangesCard);
registerIfNeeded(
  "fortum-energy-devices-detail-overlay-card",
  FortumEnergyDevicesDetailOverlayCard
);
registerIfNeeded(
  "fortum-energy-devices-adaptive-graph-card",
  FortumEnergyDevicesAdaptiveGraphCard
);
registerIfNeeded("fortum-energy-future-price-card", FortumEnergyFuturePriceCard);
registerIfNeeded("fortum-energy-settings-redirect-card", FortumEnergySettingsRedirectCard);
try {
  registerIfNeeded("ll-strategy-dashboard-fortum-energy", FortumEnergyDashboardStrategy);
} catch (err) {
  console.error("[fortum-energy] strategy registration failed", err);
}
