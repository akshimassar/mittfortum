import { FortumEnergyCustomLegendCard } from "/fortum-energy-static/strategy/cards/custom-legend-card.js";
import { FortumEnergyDevicesDetailOverlayCard } from "/fortum-energy-static/strategy/cards/devices-detail-overlay-card.js";
import { FortumEnergyDevicesAdaptiveGraphCard } from "/fortum-energy-static/strategy/cards/devices-adaptive-graph-card.js";
import { FortumEnergyFuturePriceCard } from "/fortum-energy-static/strategy/cards/future-price-card.js";
import { FortumEnergyQuickRangesCard } from "/fortum-energy-static/strategy/cards/quick-ranges-card.js";
import { FortumEnergyMultipointStrategyEditor } from "/fortum-energy-static/strategy/editors/multipoint-strategy-editor.js";
import { FortumEnergySingleStrategyEditor } from "/fortum-energy-static/strategy/editors/single-strategy-editor.js";
import { FortumEnergySpacerCard } from "/fortum-energy-static/strategy/cards/spacer-card.js";
import {
  FortumEnergyDashboardStrategy,
  FortumEnergySingleDashboardStrategy,
} from "/fortum-energy-static/strategy/strategies/single-strategy.js";
import { FortumEnergyMultipointDashboardStrategy } from "/fortum-energy-static/strategy/strategies/multipoint-strategy.js";
import { deriveEnergyRuntimeConfig, normalizeEnergySourceOverrides } from "/fortum-energy-static/strategy/runtime-config.mjs";

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
registerIfNeeded("fortum-energy-single-strategy-editor", FortumEnergySingleStrategyEditor);
registerIfNeeded("fortum-energy-multipoint-strategy-editor", FortumEnergyMultipointStrategyEditor);
try {
  registerIfNeeded(
    "ll-strategy-dashboard-fortum-energy-single",
    FortumEnergySingleDashboardStrategy
  );
  registerIfNeeded(
    "ll-strategy-dashboard-fortum-energy-multipoint",
    FortumEnergyMultipointDashboardStrategy
  );
  registerIfNeeded("ll-strategy-dashboard-fortum-energy", FortumEnergyDashboardStrategy);
} catch (err) {
  console.error("[fortum-energy] strategy registration failed", err);
}
