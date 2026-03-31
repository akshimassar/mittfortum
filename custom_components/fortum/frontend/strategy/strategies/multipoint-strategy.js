import { FortumEnergySingleDashboardStrategy } from "/fortum-energy-static/strategy/strategies/single-strategy.js";

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
    const meteringPoints = Array.isArray(config?.metering_points)
      ? config.metering_points
      : [];
    const firstPoint = meteringPoints.find((point) => point && typeof point === "object");

    if (!firstPoint) {
      return super.generate(config, hass);
    }

    const normalizedNumber = normalizeMeteringPointNumber(firstPoint.number);
    const singleConfig = {
      ...config,
      fortum: {
        ...(config?.fortum || {}),
      },
    };

    if (normalizedNumber && !singleConfig.fortum.metering_point_number) {
      singleConfig.fortum.metering_point_number = normalizedNumber;
    }
    if (!("itemization" in singleConfig) && Array.isArray(firstPoint.itemization)) {
      singleConfig.itemization = firstPoint.itemization;
    }
    if (typeof firstPoint.name === "string" && firstPoint.name.trim()) {
      singleConfig.electricity_title = firstPoint.name.trim();
    }

    return super.generate(singleConfig, hass);
  }
}
