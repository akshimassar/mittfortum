import { validateMultipointStrategyConfig } from "/fortum-energy-static/strategy/shared/config-validation.mjs";
import {
  buildMultipointConfigFromEditorState,
  createMultipointEditorStateFromConfig,
} from "/fortum-energy-static/strategy/editors/multipoint-strategy-editor-state.mjs";

const emitConfigChanged = (element, config) => {
  element.dispatchEvent(
    new CustomEvent("config-changed", {
      detail: { config },
      bubbles: true,
      composed: true,
    })
  );
};

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

export class FortumEnergyMultipointStrategyEditor extends HTMLElement {
  connectedCallback() {
    this._maybeEnsureStatisticPickerLoaded();
  }

  setConfig(config) {
    this._state = createMultipointEditorStateFromConfig(config);
    this._error = "";
    this._statisticPickerAvailable = Boolean(customElements.get("ha-statistic-picker"));

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._render();
    this._maybeEnsureStatisticPickerLoaded();
  }

  set hass(value) {
    this._hass = value;
    this._applyStatisticPickerProps();
    this._maybeEnsureStatisticPickerLoaded();
  }

  _render() {
    if (!this.shadowRoot || !this._state) {
      return;
    }

    const hasStatisticPicker =
      this._statisticPickerAvailable ?? Boolean(customElements.get("ha-statistic-picker"));

    const pointsHtml = this._state.points
      .map((point, pointIndex) => this._renderPoint(point, pointIndex, hasStatisticPicker))
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; color: var(--primary-text-color); }
        .wrapper { display: grid; gap: 16px; }
        .field { display: grid; gap: 6px; }
        .label { font-size: 14px; font-weight: 600; }
        .hint { font-size: 12px; color: var(--secondary-text-color); }
        .input {
          width: 100%; box-sizing: border-box; border: 1px solid var(--input-border-color, var(--divider-color));
          border-radius: 10px; min-height: 40px; background: var(--card-background-color);
          color: var(--primary-text-color); padding: 8px 10px;
        }
        .name-input { min-height: 52px; }
        .row { display: flex; align-items: center; gap: 10px; }
        .checkbox { width: 18px; height: 18px; }
        .points { display: grid; gap: 14px; }
        .point {
          border: 1px solid var(--divider-color); border-radius: 12px; padding: 12px;
          display: grid; gap: 12px;
        }
        .point-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
        .point-title { font-size: 14px; font-weight: 600; }
        .itemization { display: grid; gap: 10px; }
        .item-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
          gap: 8px;
          align-items: center;
        }
        .stat-picker { width: 100%; }
        .actions { display: flex; gap: 8px; flex-wrap: wrap; }
        button {
          border: 1px solid var(--divider-color); border-radius: 10px;
          background: transparent; color: var(--primary-text-color);
          min-height: 38px; padding: 0 12px; cursor: pointer;
        }
        .error {
          border-radius: 10px; border: 1px solid var(--error-color); color: var(--error-color);
          background: color-mix(in srgb, var(--error-color) 10%, transparent);
          padding: 10px; white-space: pre-wrap; font-size: 13px;
        }
        @media (max-width: 900px) {
          .item-row { grid-template-columns: minmax(0, 1fr); }
        }
      </style>
      <div class="wrapper">
        <div class="field">
          <div class="row">
            <input
              id="debug"
              class="checkbox"
              type="checkbox"
              data-field="debug"
              ${this._state.debug ? "checked" : ""}
            />
            <label for="debug">Enable debug logs for dashboard cards</label>
          </div>
        </div>

        <div class="points">${pointsHtml}</div>

        <div class="actions">
          <button type="button" data-action="add-point">Add metering point</button>
        </div>

        ${this._error ? `<div class="error">${escapeHtml(this._error)}</div>` : ""}
      </div>
    `;

    this._bindEvents();
  }

  _renderPoint(point, pointIndex, hasStatisticPicker) {
    const meteringPointOptions = this._getMeteringPointOptions();
    const meteringPointValue = point.number || "";
    const hasCurrentOption = meteringPointOptions.some((opt) => opt.number === meteringPointValue);
    const currentOption =
      meteringPointValue && !hasCurrentOption
        ? { number: meteringPointValue, label: `${meteringPointValue} (not currently discovered)` }
        : null;

    const rowsHtml = (point.itemizationRows || [])
      .map(
        (row, rowIndex) => `
        <div class="item-row" data-point-index="${pointIndex}" data-row-index="${rowIndex}">
          ${
            hasStatisticPicker
              ? `<ha-statistic-picker
                  data-field="row_stat"
                  data-point-index="${pointIndex}"
                  data-row-index="${rowIndex}"
                  class="stat-picker"
                  hide-clear-icon
                ></ha-statistic-picker>`
              : `<input
                  data-field="row_stat"
                  data-point-index="${pointIndex}"
                  data-row-index="${rowIndex}"
                  class="input"
                  type="text"
                  placeholder="statistic id"
                  value="${escapeHtml(row?.stat || "")}"
                />`
          }
          <input
            data-field="row_name"
            data-point-index="${pointIndex}"
            data-row-index="${rowIndex}"
            class="input name-input"
            type="text"
            placeholder="Name (optional)"
            value="${escapeHtml(row?.name || "")}"
          />
          <button type="button" data-action="remove-row" data-point-index="${pointIndex}" data-row-index="${rowIndex}">Remove</button>
        </div>`
      )
      .join("");

    return `
      <section class="point" data-point-index="${pointIndex}">
        <div class="point-header">
          <div class="point-title">Metering point ${pointIndex + 1}</div>
          <button type="button" data-action="remove-point" data-point-index="${pointIndex}">Remove point</button>
        </div>

        <div class="field">
          <label class="label" for="point-number-${pointIndex}">Metering point number</label>
          <select id="point-number-${pointIndex}" class="input" data-field="point_number" data-point-index="${pointIndex}">
            <option value="">Select metering point</option>
            ${
              currentOption
                ? `<option value="${escapeHtml(currentOption.number)}" selected>${escapeHtml(currentOption.label)}</option>`
                : ""
            }
            ${meteringPointOptions
              .map(
                (option) => `<option value="${escapeHtml(option.number)}" ${
                  option.number === meteringPointValue ? "selected" : ""
                }>${escapeHtml(option.label)}</option>`
              )
              .join("")}
          </select>
        </div>

        <div class="field">
          <label class="label" for="point-name-${pointIndex}">Display name</label>
          <input id="point-name-${pointIndex}" class="input" data-field="point_name" data-point-index="${pointIndex}" type="text" placeholder="Name (optional)" value="${escapeHtml(point?.name || "")}" />
        </div>

        <div class="field">
          <div class="label">Itemization</div>
          ${
            hasStatisticPicker
              ? ""
              : `<div class="hint">Statistic picker is unavailable here. Enter statistic IDs manually.</div>`
          }
          <div class="itemization">
            ${rowsHtml}
            <div class="actions">
              <button type="button" data-action="add-row" data-point-index="${pointIndex}">Add itemization row</button>
            </div>
          </div>
        </div>
      </section>
    `;
  }

  _bindEvents() {
    if (!this.shadowRoot) {
      return;
    }

    this.shadowRoot.querySelectorAll("[data-field]").forEach((field) => {
      field.addEventListener("change", (event) => this._handleFieldChange(event));
    });

    this.shadowRoot.querySelectorAll("button[data-action]").forEach((button) => {
      button.addEventListener("click", (event) => this._handleAction(event));
    });

    this._applyStatisticPickerProps();
  }

  _getMeteringPointOptions() {
    const states = this._hass?.states;
    if (!states || typeof states !== "object") {
      return [];
    }
    const byNumber = new Map();
    Object.entries(states).forEach(([entityId, stateObj]) => {
      if (!entityId.startsWith("sensor.metering_point_")) {
        return;
      }
      const numberRaw = stateObj?.attributes?.metering_point_no;
      const number = typeof numberRaw === "string" ? numberRaw.trim() : "";
      if (!number) {
        return;
      }
      const addressRaw = stateObj?.attributes?.address;
      const address = typeof addressRaw === "string" ? addressRaw.trim() : "";
      const existing = byNumber.get(number);
      if (!existing || (!existing.address && address)) {
        byNumber.set(number, {
          number,
          address,
          label: address ? `${address} (${number})` : number,
        });
      }
    });

    return Array.from(byNumber.values()).sort((left, right) =>
      left.label.localeCompare(right.label)
    );
  }

  _buildExcludeStatistics(pointIndex, currentRowIndex) {
    const rows = this._state?.points?.[pointIndex]?.itemizationRows || [];
    return rows
      .map((row, rowIndex) =>
        rowIndex === currentRowIndex || typeof row?.stat !== "string" ? "" : row.stat.trim()
      )
      .filter(Boolean);
  }

  _applyStatisticPickerProps() {
    if (!this.shadowRoot || !this._state) {
      return;
    }

    this.shadowRoot.querySelectorAll("ha-statistic-picker[data-field='row_stat']").forEach((picker) => {
      picker.allowCustomEntity = true;
      picker.statisticTypes = "sum";
      picker.includeUnitClass = ["energy"];
      if (!picker.dataset.suppressMissingEntityItem) {
        picker.dataset.suppressMissingEntityItem = "1";
        try {
          if (typeof picker._getAdditionalItems === "function") {
            picker._getAdditionalItems = () => [];
          }
        } catch (_err) {
          // Keep picker functional if internals change.
        }
      }
      if (this._hass) {
        picker.hass = this._hass;
      }
      const pointIndex = Number(picker.dataset.pointIndex);
      const rowIndex = Number(picker.dataset.rowIndex);
      const row = this._state?.points?.[pointIndex]?.itemizationRows?.[rowIndex];
      picker.value = row?.stat || "";
      picker.excludeStatistics = this._buildExcludeStatistics(pointIndex, rowIndex);
      if (!picker.dataset.boundValueChanged) {
        picker.dataset.boundValueChanged = "1";
        picker.addEventListener("value-changed", (event) => this._handleStatisticPickerChange(event));
      }
      if (typeof picker.requestUpdate === "function") {
        picker.requestUpdate();
      }
    });
  }

  _handleStatisticPickerChange(event) {
    const target = event.currentTarget;
    const pointIndex = Number(target?.dataset?.pointIndex);
    const rowIndex = Number(target?.dataset?.rowIndex);
    if (!Number.isInteger(pointIndex) || !Number.isInteger(rowIndex)) {
      return;
    }
    const row = this._state?.points?.[pointIndex]?.itemizationRows?.[rowIndex];
    if (!row) {
      return;
    }
    row.stat = typeof event?.detail?.value === "string" ? event.detail.value : "";
    this._validateAndEmit();
  }

  _handleFieldChange(event) {
    if (!this._state) {
      return;
    }
    const target = event.currentTarget;
    const field = target?.dataset?.field;

    if (field === "debug") {
      this._state.debug = target.checked;
      this._validateAndEmit();
      return;
    }

    const pointIndex = Number(target?.dataset?.pointIndex);
    if (!Number.isInteger(pointIndex) || !this._state.points[pointIndex]) {
      return;
    }
    const point = this._state.points[pointIndex];

    if (field === "point_number") {
      point.number = target.value;
      const options = this._getMeteringPointOptions();
      const match = options.find((option) => option.number === point.number);
      if (match && !point.name) {
        point.address = match.address || "";
      }
      this._validateAndEmit();
      return;
    }
    if (field === "point_name") {
      point.name = target.value;
      this._validateAndEmit();
      return;
    }
    if (field === "row_stat" || field === "row_name") {
      const rowIndex = Number(target?.dataset?.rowIndex);
      if (!Number.isInteger(rowIndex) || !point.itemizationRows[rowIndex]) {
        return;
      }
      point.itemizationRows[rowIndex] = {
        ...point.itemizationRows[rowIndex],
        [field === "row_stat" ? "stat" : "name"]: target.value,
      };
      this._validateAndEmit();
    }
  }

  _handleAction(event) {
    if (!this._state) {
      return;
    }
    const target = event.currentTarget;
    const action = target?.dataset?.action;

    if (action === "add-point") {
      this._state.points = this._state.points.concat({
        number: "",
        name: "",
        address: "",
        itemizationRows: [],
      });
      this._validateAndEmit();
      return;
    }

    const pointIndex = Number(target?.dataset?.pointIndex);
    if (!Number.isInteger(pointIndex) || !this._state.points[pointIndex]) {
      return;
    }

    if (action === "remove-point") {
      this._state.points = this._state.points.filter((_, index) => index !== pointIndex);
      if (this._state.points.length === 0) {
        this._state.points = [{ number: "", name: "", address: "", itemizationRows: [] }];
      }
      this._validateAndEmit();
      return;
    }

    if (action === "add-row") {
      this._state.points[pointIndex].itemizationRows = this._state.points[pointIndex].itemizationRows
        .concat({ stat: "", name: "" });
      this._validateAndEmit();
      return;
    }

    if (action === "remove-row") {
      const rowIndex = Number(target?.dataset?.rowIndex);
      if (!Number.isInteger(rowIndex)) {
        return;
      }
      this._state.points[pointIndex].itemizationRows = this._state.points[pointIndex].itemizationRows
        .filter((_, index) => index !== rowIndex);
      this._validateAndEmit();
    }
  }

  _validateAndEmit() {
    try {
      const config = buildMultipointConfigFromEditorState(this._state);
      const validated = validateMultipointStrategyConfig(config);
      this._error = "";
      emitConfigChanged(this, validated);
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
    }
    this._render();
  }

  _maybeEnsureStatisticPickerLoaded() {
    if (this._statisticPickerAvailable || customElements.get("ha-statistic-picker")) {
      this._statisticPickerAvailable = true;
      return;
    }
    if (this._ensureStatisticPickerPromise || !this._hass || !this.shadowRoot || !this.isConnected) {
      return;
    }
    this._ensureStatisticPickerPromise = this._ensureStatisticPickerLoaded().finally(() => {
      this._ensureStatisticPickerPromise = undefined;
    });
  }

  async _ensureStatisticPickerLoaded() {
    if (!customElements.get("ha-selector")) {
      return;
    }
    const probe = document.createElement("ha-selector");
    probe.hass = this._hass;
    probe.selector = { statistic: {} };
    probe.style.display = "none";
    this.shadowRoot.appendChild(probe);
    try {
      await Promise.race([
        customElements.whenDefined("ha-statistic-picker"),
        new Promise((resolve) => window.setTimeout(resolve, 1200)),
      ]);
    } finally {
      probe.remove();
      this._statisticPickerAvailable = Boolean(customElements.get("ha-statistic-picker"));
      this._render();
    }
  }
}

if (typeof customElements !== "undefined") {
  const tag = "fortum-energy-multipoint-strategy-editor";
  if (!customElements.get(tag)) {
    customElements.define(tag, FortumEnergyMultipointStrategyEditor);
  }
}
