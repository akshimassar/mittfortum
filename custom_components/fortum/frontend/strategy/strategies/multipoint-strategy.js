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

const toStatisticIdSet = (rawItems) =>
  new Set(
    (Array.isArray(rawItems) ? rawItems : [])
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        return item?.statistic_id;
      })
      .filter((value) => typeof value === "string" && value.length)
  );

const resolvePointForecast = (hass, meteringPointNumber, statisticIds) => {
  const entityId = `sensor.metering_point_${meteringPointNumber}`;
  const sensorState = hass?.states?.[entityId];
  if (!sensorState) {
    return {
      forecastIds: [],
      forecastError: `Metering point sensor ${entityId} is missing.`,
    };
  }

  const priceArea = sensorState?.attributes?.price_area;
  if (typeof priceArea !== "string" || !priceArea.trim()) {
    return {
      forecastIds: [],
      forecastError: `Sensor ${entityId} has no attribute price_area.`,
    };
  }

  const statisticId = `fortum:price_forecast_${priceArea.trim().toLowerCase()}`;
  if (!statisticIds.has(statisticId)) {
    return {
      forecastIds: [],
      forecastError: `Price statistic ${statisticId} has no values.`,
    };
  }

  return {
    forecastIds: [statisticId],
    forecastError: null,
  };
};

const applyForecastConfigToView = (view, forecastIds, forecastError) => {
  if (!view || !Array.isArray(view.sections)) {
    return view;
  }

  view.sections.forEach((section) => {
    if (!Array.isArray(section?.cards)) {
      return;
    }
    section.cards.forEach((card) => {
      if (card?.type !== "custom:fortum-energy-future-price-card") {
        return;
      }
      const resolvedMetrics = card.resolved_metrics || {};
      card.resolved_metrics = {
        ...resolvedMetrics,
        price_forecast: forecastIds,
        future_price_error: forecastError,
      };
    });
  });

  return view;
};

export class FortumEnergyMultipointDashboardStrategy extends FortumEnergySingleDashboardStrategy {
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
    let settingsView = null;

    for (let index = 0; index < meteringPoints.length; index += 1) {
      const point = meteringPoints[index];
      const normalizedNumber = normalizeMeteringPointNumber(point.number);
      const singleConfig = {
        ...validatedConfig,
        fortum: {
          ...(validatedConfig?.fortum || {}),
        },
      };

      if (normalizedNumber) {
        singleConfig.fortum.metering_point_number = normalizedNumber;
      }
      singleConfig.itemization = point.itemization;
      singleConfig.electricity_title = point.name || point.address || point.number;

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
        normalizedNumber || String(point.number),
        statisticIdSet
      );

      const pointView = {
        ...selectedView,
        title: singleConfig.electricity_title,
        path: `electricity-${index + 1}`,
      };
      views.push(applyForecastConfigToView(pointView, forecastIds, forecastError));

      if (!settingsView) {
        settingsView = generatedViews.find((view) => view?.path === "settings") || null;
      }
    }

    if (settingsView) {
      views.push(settingsView);
    }

    return { views };
  }
}
