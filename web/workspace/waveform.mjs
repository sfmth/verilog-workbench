import {eventIndexAt, formatValue, VCD_LIMITS} from './vcd.mjs';

const MAX_SELECTED = 24;
const MAX_LIST = 100;
const MAX_RENDER_EVENTS = 4000;
const NS = 'http://www.w3.org/2000/svg';

function element(tag, text, parent, attributes = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  parent?.append(node);
  return node;
}

function svgElement(tag, parent, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  if (text !== undefined) node.textContent = text;
  parent.append(node);
  return node;
}

export function mountWaveform(container, text, {filename = 'Waveform.vcd'} = {}) {
  const root = element('section', undefined, null, {class: 'waveform-view', 'aria-label': 'VCD waveform viewer'});
  container.replaceChildren(root);
  element('h3', filename, root);
  const status = element('p', 'Parsing VCD in a background worker…', root, {role: 'status', 'aria-live': 'polite'});
  let worker;
  let disposed = false;
  const cleanup = () => {
    disposed = true;
    worker?.terminate();
    root.remove();
  };
  if (typeof text !== 'string' || text.length > VCD_LIMITS.maxBytes) {
    status.textContent = `Unable to open waveform: VCD must be text under ${VCD_LIMITS.maxBytes / 1024 / 1024} MiB.`;
    return cleanup;
  }
  try {
    worker = new Worker(new URL('./vcd-worker.mjs', import.meta.url), {type: 'module'});
    worker.onerror = () => {
      if (!disposed) status.textContent = 'Unable to load the VCD parser worker. Reload the page and try again.';
      worker.terminate();
    };
    worker.onmessage = ({data}) => {
      worker.terminate();
      if (disposed) return;
      if (!data?.ok) {
        status.textContent = `Unable to open waveform: ${data?.error || 'Invalid worker response.'}`;
        return;
      }
      const waveform = data.waveform;
      status.textContent = `${waveform.signals.length} signals · ${waveform.totalEvents.toLocaleString()} value changes · timescale ${waveform.timescale.label} per tick`;
      mountControls(root, waveform);
    };
    worker.postMessage({text});
  } catch (error) {
    status.textContent = `Unable to start waveform parser: ${error.message}`;
    worker?.terminate();
  }
  return cleanup;
}

function mountControls(root, waveform) {
  const {signals, endTime, timescale} = waveform;
  const selected = new Set(signals.slice(0, 6).map(signal => signal.id));
  const extent = Math.max(1, endTime);
  let start = 0;
  let end = extent;
  let cursor = 0;
  let radix = 'hex';
  let renderQueued = false;
  const controls = element('div', undefined, root, {class: 'waveform-controls'});
  const radixLabel = element('label', 'Value radix ', controls);
  const radixInput = element('select', undefined, radixLabel, {'aria-label': 'Waveform value radix'});
  for (const [value, label] of [['hex', 'Hexadecimal'], ['bin', 'Binary'], ['unsigned', 'Unsigned decimal']]) {
    element('option', label, radixInput, {value});
  }
  function button(label, action) {
    const result = element('button', label, controls, {type: 'button'});
    result.addEventListener('click', action);
    return result;
  }
  function zoom(factor) {
    const width = Math.min(extent, Math.max(1, (end - start) * factor));
    const center = cursor >= start && cursor <= end ? cursor : (start + end) / 2;
    start = Math.max(0, Math.min(extent - width, center - width / 2));
    end = start + width;
    render();
  }
  const zoomIn = button('Zoom in', () => zoom(.5));
  const zoomOut = button('Zoom out', () => zoom(2));
  const earlier = button('Earlier', () => pan(-1));
  const later = button('Later', () => pan(1));
  button('Fit trace', () => { start = 0; end = extent; render(); });
  function pan(direction) {
    const width = end - start;
    start = Math.max(0, Math.min(extent - width, start + direction * width / 2));
    end = start + width;
    render();
  }
  const cursorLabel = element('label', 'Cursor (ticks) ', root);
  const cursorInput = element('input', undefined, cursorLabel, {class: 'waveform-cursor', type: 'range', min: '0', max: String(endTime), step: '1', value: '0', 'aria-label': 'Waveform cursor in ticks'});
  const cursorValue = element('output', '0', cursorLabel);
  const layout = element('div', undefined, root, {class: 'waveform-layout'});
  const picker = element('div', undefined, layout, {class: 'waveform-signals'});
  const filterLabel = element('label', 'Find signals ', picker);
  const filter = element('input', undefined, filterLabel, {class: 'waveform-filter', type: 'search', placeholder: 'Scope or signal name', 'aria-label': 'Filter waveform signals'});
  const selectionStatus = element('p', undefined, picker);
  const list = element('div', undefined, picker, {class: 'waveform-signal-list'});
  const display = element('div', undefined, layout, {class: 'waveform-display'});
  const viewStatus = element('p', undefined, display);
  const chart = element('div', undefined, display);
  element('p', 'Use the signal checkboxes to choose up to 24 traces. Click the plot or use the cursor slider; zoom follows the cursor. Times are shown in VCD ticks.', root);
  function renderSignals(focusId) {
    const needle = filter.value.toLowerCase();
    const matches = signals.filter(signal => signal.name.toLowerCase().includes(needle));
    list.replaceChildren();
    selectionStatus.textContent = `${selected.size}/${MAX_SELECTED} selected · ${matches.length} matches${matches.length > MAX_LIST ? ' (showing first 100; narrow the filter)' : ''}`;
    let focusTarget;
    for (const signal of matches.slice(0, MAX_LIST)) {
      const label = element('label', undefined, list, {class: 'waveform-signal-option'});
      const checkbox = element('input', undefined, label, {type: 'checkbox'});
      checkbox.checked = selected.has(signal.id);
      checkbox.disabled = selected.size >= MAX_SELECTED && !checkbox.checked;
      if (signal.id === focusId) focusTarget = checkbox;
      element('span', `${signal.name} (${signal.width} bit${signal.width === 1 ? '' : 's'})`, label);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && selected.size < MAX_SELECTED) selected.add(signal.id);
        else selected.delete(signal.id);
        renderSignals(signal.id);
        render();
      });
    }
    focusTarget?.focus({preventScroll: true});
  }
  const shortValue = value => {
    const formatted = formatValue(value, radix);
    return formatted.length > 32 ? `${formatted.slice(0, 29)}…` : formatted;
  };
  function render() {
    if (renderQueued) return;
    renderQueued = true;
    // Slider and keyboard input coalesce to one bounded paint per animation frame.
    requestAnimationFrame(() => {
      renderQueued = false;
      if (!root.isConnected) return;
      zoomIn.disabled = end - start <= 1;
      zoomOut.disabled = end - start >= extent;
      earlier.disabled = start <= 0;
      later.disabled = end >= extent;
      cursorValue.textContent = timescale.secondsPerTick === null
        ? `${cursor} ticks (timescale unspecified)`
        : `${cursor} ticks (${cursor * timescale.value} ${timescale.unit})`;
      cursorInput.setAttribute('aria-valuetext', cursorValue.textContent);
      const shown = signals.filter(signal => selected.has(signal.id));
      const maxChanges = Math.min(600, Math.floor(MAX_RENDER_EVENTS / Math.max(1, shown.length)));
      const width = 1000;
      const plotLeft = 290;
      const plotRight = 985;
      const plotWidth = plotRight - plotLeft;
      const rowHeight = 42;
      const height = 48 + Math.max(1, shown.length) * rowHeight;
      const x = time => plotLeft + Math.max(0, Math.min(1, (time - start) / (end - start))) * plotWidth;
      chart.replaceChildren();
      const svg = svgElement('svg', chart, {class: 'waveform-svg', viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': `Waveforms from ${start} to ${end} ticks; cursor at ${cursor} ticks`});
      svgElement('title', svg, {}, 'Waveform plot. Use the labelled cursor slider for keyboard access.');
      for (let index = 0; index <= 4; index++) {
        const time = start + (end - start) * index / 4;
        const position = x(time);
        svgElement('line', svg, {x1: position, x2: position, y1: 26, y2: height, stroke: '#334257'});
        svgElement('text', svg, {x: position, y: 18, fill: '#cbd9eb', 'text-anchor': index === 4 ? 'end' : index === 0 ? 'start' : 'middle'}, Number(time.toPrecision(6)).toString());
      }
      let dense = 0;
      for (const [index, signal] of shown.entries()) {
        const top = 36 + index * rowHeight;
        const middle = top + 17;
        const signalGroup = svgElement('g', svg, {});
        svgElement('title', signalGroup, {}, signal.name);
        svgElement('text', signalGroup, {x: 8, y: top + 12, fill: '#e6eef9'}, signal.name.length > 36 ? `${signal.name.slice(0, 33)}…` : signal.name);
        const current = signal.events[eventIndexAt(signal.events, cursor)]?.value;
        svgElement('text', signalGroup, {x: 8, y: top + 29, fill: '#9eb7d4'}, shortValue(current));
        svgElement('line', signalGroup, {x1: 0, x2: width, y1: top + rowHeight - 4, y2: top + rowHeight - 4, stroke: '#243248'});
        const first = eventIndexAt(signal.events, start);
        const last = eventIndexAt(signal.events, end);
        if (last - first > maxChanges) {
          dense++;
          svgElement('text', signalGroup, {x: plotLeft + 8, y: middle + 4, fill: '#f1c56a'}, `${(last - first).toLocaleString()} changes in view — zoom in to resolve`);
          continue;
        }
        let from = start;
        let value = signal.events[first]?.value;
        const segments = [];
        for (let event = first + 1; event <= last; event++) {
          const next = signal.events[event];
          segments.push({from, to: next.time, value});
          from = next.time;
          value = next.value;
        }
        segments.push({from, to: end, value});
        let previousLevel;
        for (const segment of segments) {
          const left = x(segment.from);
          const right = x(segment.to);
          if (right <= left) continue;
          const unknown = segment.value == null || /[xz]/.test(segment.value);
          const color = unknown ? '#f1c56a' : '#79dfb5';
          if (signal.width === 1) {
            const level = segment.value === '1' ? top + 5 : segment.value === '0' ? top + 29 : middle;
            svgElement('path', signalGroup, {d: `M${left} ${previousLevel ?? level}V${level}H${right}`, fill: 'none', stroke: color, 'stroke-width': 1.5});
            previousLevel = level;
            if (unknown && right - left > 18) svgElement('text', signalGroup, {x: left + 4, y: middle - 4, fill: color}, segment.value || '—');
          } else {
            svgElement('path', signalGroup, {d: `M${left} ${middle}L${Math.min(right, left + 3)} ${top + 5}H${Math.max(left, right - 3)}L${right} ${middle}L${Math.max(left, right - 3)} ${top + 29}H${Math.min(right, left + 3)}Z`, fill: 'none', stroke: color});
            const label = shortValue(segment.value);
            if (right - left > label.length * 8 + 10) svgElement('text', signalGroup, {x: left + 6, y: middle + 4, fill: color}, label);
          }
        }
      }
      if (!shown.length) svgElement('text', svg, {x: plotLeft + 8, y: 60, fill: '#cbd9eb'}, 'Choose signals to display.');
      if (cursor >= start && cursor <= end) svgElement('line', svg, {x1: x(cursor), x2: x(cursor), y1: 26, y2: height, stroke: '#ff8d9a', 'stroke-width': 2});
      viewStatus.textContent = `View: ${Number(start.toPrecision(6))}–${Number(end.toPrecision(6))} ticks${dense ? ` · ${dense} dense trace${dense === 1 ? '' : 's'} need a closer zoom` : ''}`;
      svg.addEventListener('click', event => {
        const bounds = svg.getBoundingClientRect();
        const point = (event.clientX - bounds.left) * width / bounds.width;
        if (point < plotLeft || point > plotRight) return;
        cursor = Math.max(0, Math.min(endTime, Math.round(start + (point - plotLeft) / plotWidth * (end - start))));
        cursorInput.value = String(cursor);
        render();
      });
    });
  }
  radixInput.addEventListener('change', () => { radix = radixInput.value; render(); });
  filter.addEventListener('input', renderSignals);
  cursorInput.addEventListener('input', () => { cursor = Number(cursorInput.value); render(); });
  renderSignals();
  render();
}
