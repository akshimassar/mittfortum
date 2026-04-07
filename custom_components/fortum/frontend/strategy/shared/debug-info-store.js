import { listDiscoverableMeteringPoints } from "/fortum-energy-static/strategy/shared/metering-point-discovery.mjs";
import {
  REDACTION_TOKEN,
  sanitizeDiagnosticsPayload,
} from "/fortum-energy-static/strategy/shared/debug-export-redaction.mjs";

const STORE_KEY = "__fortumEnergyDashboardDebugStore";
const ADAPTIVE_HISTORY_LIMIT = 160;

const resolveHomeAssistantVersion = (hass) => {
  const version = hass?.config?.version;
  if (typeof version === "string" && version.trim().length) {
    return version.trim();
  }
  return "unknown";
};

const resolveIntegrationVersion = () => {
  const version = globalThis.__fortumEnergyIntegrationVersion;
  if (typeof version === "string" && version.trim().length) {
    return version.trim();
  }
  return "unknown";
};

const resolveBrowserDiagnostics = () => {
  if (typeof navigator === "undefined") {
    return {
      user_agent: "unknown",
      language: "unknown",
      platform: "unknown",
    };
  }

  const uaDataPlatform = navigator.userAgentData?.platform;
  return {
    user_agent: navigator.userAgent || "unknown",
    language: navigator.language || "unknown",
    platform:
      (typeof uaDataPlatform === "string" && uaDataPlatform) ||
      navigator.platform ||
      "unknown",
  };
};

const clonePayload = (value) => {
  if (typeof structuredClone === "function") {
    try {
      return structuredClone(value);
    } catch (_err) {
      // Fall back to JSON clone below.
    }
  }
  return JSON.parse(JSON.stringify(value));
};

const getStore = () => {
  if (!globalThis[STORE_KEY]) {
    globalThis[STORE_KEY] = {
      adaptiveHistory: [],
      latestAdaptive: null,
      latestFuturePrice: null,
      cardConfigs: {},
      sequence: 0,
    };
  }
  return globalThis[STORE_KEY];
};

export const setDashboardCardConfig = (cardId, config) => {
  if (typeof cardId !== "string" || !cardId.length) {
    return;
  }
  const store = getStore();
  store.cardConfigs[cardId] = clonePayload(config || {});
};

export const recordAdaptiveDebugInfo = (payload) => {
  if (!payload || typeof payload !== "object") {
    return;
  }
  const store = getStore();
  store.sequence += 1;
  const row = {
    sequence: store.sequence,
    recorded_at: new Date().toISOString(),
    ...clonePayload(payload),
  };
  store.latestAdaptive = row;
  store.adaptiveHistory.push(row);
  if (store.adaptiveHistory.length > ADAPTIVE_HISTORY_LIMIT) {
    store.adaptiveHistory.splice(0, store.adaptiveHistory.length - ADAPTIVE_HISTORY_LIMIT);
  }
};

export const setLatestFuturePriceDebugInfo = (payload) => {
  if (!payload || typeof payload !== "object") {
    return;
  }
  const store = getStore();
  store.sequence += 1;
  store.latestFuturePrice = {
    sequence: store.sequence,
    recorded_at: new Date().toISOString(),
    ...clonePayload(payload),
  };
};

export const getDiscoverableMeteringPoints = (hass) => {
  return listDiscoverableMeteringPoints(hass).map((point) => ({
    number: point.number,
    address: point.address,
    label: point.label,
    entity_ids: point.entityIds,
  }));
};

export const buildDashboardDebugExport = ({
  collectionKey,
  hass,
  adaptiveDebugInfo,
  adaptiveExportData,
}) => {
  const store = getStore();
  const rawPayload = {
    generated_at: new Date().toISOString(),
    format_version: 3,
    collection_key: collectionKey || "",
    redaction: {
      enabled: true,
      personal_placeholder_format: "[REDACTED <field> <n>]",
      token_placeholder: REDACTION_TOKEN,
    },
    environment: {
      home_assistant_version: resolveHomeAssistantVersion(hass),
      integration_version: resolveIntegrationVersion(),
      browser: resolveBrowserDiagnostics(),
    },
    dashboard_config: clonePayload(store.cardConfigs),
    discoverable_metering_points: getDiscoverableMeteringPoints(hass),
    adaptive_graph: {
      latest_debug: adaptiveDebugInfo || store.latestAdaptive,
      latest_export_data: adaptiveExportData ? clonePayload(adaptiveExportData) : null,
      history: clonePayload(store.adaptiveHistory),
    },
    future_price: {
      latest_debug: clonePayload(store.latestFuturePrice),
    },
  };
  return sanitizeDiagnosticsPayload(rawPayload);
};
