import { FortumEnergySingleDashboardStrategy } from "/fortum-energy-static/strategy/strategies/single-strategy.js";
import { validateMultipointStrategyConfig } from "/fortum-energy-static/strategy/shared/config-validation.mjs";

const normalizeMeteringPointNumber = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(Math.trunc(value));
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  return null;
};

export class FortumEnergyMultipointDashboardStrategy extends FortumEnergySingleDashboardStrategy {
  static async generate(config, hass) {
    const validatedConfig = validateMultipointStrategyConfig(config || {});
    const meteringPoints = validatedConfig.metering_points;
    const firstPoint = meteringPoints[0];

    if (!firstPoint) {
      return super.generate(validatedConfig, hass);
    }

    const normalizedNumber = normalizeMeteringPointNumber(firstPoint.number);
    const singleConfig = {
      ...validatedConfig,
      fortum: {
        ...(validatedConfig?.fortum || {}),
      },
    };

    if (normalizedNumber && !singleConfig.fortum.metering_point_number) {
      singleConfig.fortum.metering_point_number = normalizedNumber;
    }
    singleConfig.itemization = firstPoint.itemization;
    singleConfig.electricity_title = firstPoint.name || firstPoint.address || firstPoint.number;

    return super.generate(singleConfig, hass);
  }
}
