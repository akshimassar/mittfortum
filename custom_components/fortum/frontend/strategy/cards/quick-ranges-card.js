import { DEFAULT_COLLECTION_KEY } from "/fortum-energy-static/strategy/shared/constants.js";
import { ensureFortumEnergyRangePersistence } from "/fortum-energy-static/strategy/shared/range-persistence.js";

export class FortumEnergyQuickRangesCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._render();
  }

  set hass(hass) {
    const languageChanged = this._hass?.locale?.language !== hass?.locale?.language;
    this._hass = hass;
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    ensureFortumEnergyRangePersistence(hass, collectionKey);
    if (!this._rendered || languageChanged) {
      this._render();
    }
  }

  getCardSize() {
    return 1;
  }

  _setDefaultRange(range) {
    const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
    const collection = this._hass?.connection?.[`_${collectionKey}`];

    const getCenterDate = () => {
      const start = collection?.start instanceof Date ? collection.start : null;
      const end = collection?.end instanceof Date ? collection.end : null;
      if (!start || !end) {
        return new Date();
      }
      return new Date((start.getTime() + end.getTime()) / 2);
    };

    const center = getCenterDate();
    center.setHours(12, 0, 0, 0);

    const createCenteredRange = (days) => {
      const half = Math.floor((days - 1) / 2);
      const start = new Date(center);
      start.setDate(center.getDate() - half);
      start.setHours(0, 0, 0, 0);

      const end = new Date(start);
      end.setDate(start.getDate() + days - 1);
      end.setHours(23, 59, 59, 999);

      return { start, end };
    };

    let start;
    let end;
    if (range === "month") {
      ({ start, end } = createCenteredRange(31));
    } else if (range === "week") {
      ({ start, end } = createCenteredRange(7));
    } else {
      ({ start, end } = createCenteredRange(1));
    }

    if (collection && collection.setPeriod && collection.refresh) {
      collection.setPeriod(start, end);
      collection.refresh();
      return;
    }

    window.location.reload();
  }

  _render() {
    if (!this.shadowRoot || !this._hass) {
      return;
    }

    const dayLabel = "Day";
    const weekLabel = "Week";
    const monthLabel = "Month";

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color));
          border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color);
          box-sizing: border-box;
          height: 100%;
          display: flex;
          align-items: center;
        }
        .row {
          display: flex;
          gap: 8px;
          padding: 8px 12px;
          align-items: center;
          width: 100%;
        }
        ha-button {
          flex: 1;
          --ha-button-theme-color: currentColor;
        }
        ha-button.export {
          flex: 0 0 auto;
          min-width: 92px;
        }
      </style>
      <div class="card">
        <div class="row">
          <ha-button appearance="filled" size="small" data-range="day">${dayLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="week">${weekLabel}</ha-button>
          <ha-button appearance="filled" size="small" data-range="month">${monthLabel}</ha-button>
          ${
            this._config?.debug === true
              ? '<ha-button class="export" appearance="outlined" size="small" data-range="export">Export Debug</ha-button>'
              : ""
          }
        </div>
      </div>
    `;

    if (!this._boundClick) {
      this._boundClick = (ev) => {
        const target = ev.target;
        if (!(target instanceof Element)) {
          return;
        }
        const button = target.closest("ha-button");
        if (!button) {
          return;
        }
        const range = button.getAttribute("data-range");
        if (range) {
          if (range === "export") {
            const collectionKey = this._config?.collection_key || DEFAULT_COLLECTION_KEY;
            window.dispatchEvent(
              new CustomEvent("fortum-energy:export-adaptive-snapshot", {
                detail: {
                  collectionKey,
                  download: true,
                },
              })
            );
            return;
          }
          this._setDefaultRange(range);
        }
      };
      this.shadowRoot.addEventListener("click", this._boundClick);
    }

    this._rendered = true;
  }
}
