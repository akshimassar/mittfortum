export class FortumEnergySpacerCard extends HTMLElement {
  setConfig(_config) {}

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { rows: 1, columns: 4 };
  }

  connectedCallback() {
    this.style.display = "block";
    this.style.height = "100%";
    this.style.pointerEvents = "none";
  }
}
