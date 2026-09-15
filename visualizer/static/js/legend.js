/* The legend over the map and the result table in the Weights tab.

   Both are pure readouts of the current settings, so they live together and
   away from the map code. model.js calls setStyleChangeHook with render() so
   a restyle refreshes them without model.js importing this file, which would
   make a cycle. */

import { $, LADDER, LEVELS, RAMP, state } from "./core.js";
import { delayAt, setStyleChangeHook } from "./model.js";

export function renderLegend() {
  const items = state.colourBy === "delay"
    ? [["1.0x", RAMP[0]], ["1.4x", RAMP[1]], ["2.2x", RAMP[2]], ["3.5x+", RAMP[3]]]
    : state.colourBy === "speed"
      ? [["45+ km/h", RAMP[0]], ["25", RAMP[1]], ["12", RAMP[2]], ["5", RAMP[3]]]
      : [["free", RAMP[0]], ["slow", RAMP[1]], ["heavy", RAMP[2]], ["jam", RAMP[3]]];
  $("legendItems").innerHTML = items
    .map(([t, c]) => `<div><i style="background:${c}"></i>${t}</div>`).join("");
}

export function renderCalc() {
  $("calcBody").innerHTML = LADDER.map((base, i) => {
    const { weight, delay } = delayAt(base);
    return `<tr><td><i style="background:${RAMP[i]}"></i>${LEVELS[i]}</td>` +
      `<td>${weight.toFixed(0)}</td><td>${(weight / 100).toFixed(2)}</td>` +
      `<td>${delay.toFixed(2)}x</td><td>${(50 / delay).toFixed(0)} km/h</td></tr>`;
  }).join("");
}

export function renderReadouts() {
  renderLegend();
  renderCalc();
}

setStyleChangeHook(renderReadouts);
