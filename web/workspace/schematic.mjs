/** Pan/zoom a native SVG/PNG in an image context; source SVG is never executed. */
export function mountSchematic(container, url, filename) {
  const document = container.ownerDocument;
  const make = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  };
  const title = make('h3', filename), controls = make('div', undefined, 'actions schematic-controls');
  const viewport = make('div', undefined, 'schematic-viewport');
  viewport.tabIndex = 0; viewport.setAttribute('role', 'region'); viewport.setAttribute('aria-label', 'Schematic viewport');
  const image = make('img', undefined, 'schematic-image zoom-100');
  image.src = url; image.alt = 'Schematic artifact ' + filename; image.draggable = false;
  const label = make('label', 'Zoom'), zoom = make('select'); zoom.id = 'schematic-zoom';
  zoom.setAttribute('aria-label', 'Schematic zoom');
  for (const scale of [50, 75, 100, 150, 200, 300]) {
    const option = make('option', scale === 100 ? 'Fit width · 100%' : scale + '%'); option.value = String(scale); zoom.append(option);
  }
  zoom.value = '100'; label.append(zoom); controls.append(label);
  function button(label, action) {
    const value = make('button', label); value.type = 'button'; value.addEventListener('click', action); controls.append(value); return value;
  }
  const fit = () => { zoom.value = '100'; image.className = 'schematic-image zoom-100'; viewport.scrollTop = viewport.scrollLeft = 0; };
  button('Fit schematic', fit);
  for (const [label, x, y] of [['Pan left', -120, 0], ['Pan right', 120, 0], ['Pan up', 0, -120], ['Pan down', 0, 120]]) button(label, () => { viewport.scrollLeft += x; viewport.scrollTop += y; });
  const status = make('p', 'Drag the drawing or use the pan buttons and scrollbars.', 'muted');
  status.setAttribute('role', 'status');
  viewport.append(image); container.replaceChildren(title, controls, status, viewport);
  let drag;
  const change = () => { image.className = `schematic-image zoom-${zoom.value}`; };
  const down = event => {
    if (event.button !== 0 || event.isPrimary === false || drag) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
    viewport.focus({preventScroll: true}); viewport.setPointerCapture(event.pointerId); viewport.classList.add('panning'); event.preventDefault();
  };
  const move = event => {
    if (!drag || event.pointerId !== drag.id) return;
    viewport.scrollLeft = drag.left + drag.x - event.clientX;
    viewport.scrollTop = drag.top + drag.y - event.clientY;
  };
  const up = event => { if (drag && event.pointerId !== undefined && event.pointerId !== drag.id) return; drag = undefined; viewport.classList.remove('panning'); };
  const error = () => { status.textContent = 'The schematic image could not be displayed. Download the artifact to inspect it.'; };
  zoom.addEventListener('change', change); image.addEventListener('error', error);
  viewport.addEventListener('pointerdown', down); viewport.addEventListener('pointermove', move);
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) viewport.addEventListener(type, up);
  return () => {
    zoom.removeEventListener('change', change); image.removeEventListener('error', error);
    viewport.removeEventListener('pointerdown', down); viewport.removeEventListener('pointermove', move);
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) viewport.removeEventListener(type, up);
    drag = undefined; container.replaceChildren();
  };
}
