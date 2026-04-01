import { FortumEnergySingleDashboardStrategy } from "/fortum-energy-static/strategy/strategies/single-strategy.js";
import { validateMultipointStrategyConfig } from "/fortum-energy-static/strategy/shared/config-validation.mjs";
import {
  applyForecastConfigToView,
  buildSingleConfigsFromMultipoint,
  resolvePointForecast,
  toStatisticIdSet,
} from "/fortum-energy-static/strategy/shared/multipoint-runtime.mjs";

export class FortumEnergyMultipointDashboardStrategy extends FortumEnergySingleDashboardStrategy {
  static async getConfigElement() {
    await import("/fortum-energy-static/strategy/editors/multipoint-strategy-editor.js");
    return document.createElement("fortum-energy-multipoint-strategy-editor");
  }

  static async generate(config, hass) {
    const validatedConfig = validateMultipointStrategyConfig(config || {});
    const meteringPoints = validatedConfig.metering_points;

    let statisticIds = [];
    try {
      statisticIds = await hass.callWS({ type: "recorder/list_statistic_ids" });
    } catch (_err) {
      statisticIds = [];
    }
    const statisticIdSet = toStatisticIdSet(statisticIds);

    const views = [];

    const pointConfigs = buildSingleConfigsFromMultipoint(validatedConfig);
    for (let index = 0; index < meteringPoints.length; index += 1) {
      const point = meteringPoints[index];
      const singleConfig = pointConfigs[index];
      const pointNumber = singleConfig?.fortum?.metering_point_number || String(point.number);

      const generated = await super.generate(singleConfig, hass);
      const generatedViews = Array.isArray(generated?.views) ? generated.views : [];
      const electricityView = generatedViews.find((view) => view?.path === "electricity");
      const fallbackView = generatedViews[0];
      const selectedView = electricityView || fallbackView;

      if (!selectedView) {
        continue;
      }

      const { forecastIds, forecastError } = resolvePointForecast(
        hass,
        pointNumber,
        statisticIdSet
      );

      const pointView = {
        ...selectedView,
        title: singleConfig.electricity_title,
        path: `electricity-${index + 1}`,
      };
      views.push(applyForecastConfigToView(pointView, forecastIds, forecastError));
    }

    return { views };
  }
}
