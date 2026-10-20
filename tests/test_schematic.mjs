import assert from 'node:assert/strict';
import test from 'node:test';
import {mountSchematic} from '../web/workspace/schematic.mjs';

class Element extends EventTarget {
  constructor(document, tag) {
    super(); this.ownerDocument = document; this.tagName = tag; this.children = [];
    this.attributes = {}; this.scrollTop = this.scrollLeft = 0;
    const names = new Set();
    this.classList = {add: name => names.add(name), remove: name => names.delete(name), contains: name => names.has(name)};
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Artifact content must remain outside the document HTML context.'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  setPointerCapture(id) { this.capturedPointer = id; }
  focus() { this.ownerDocument.activeElement = this; }
}
function fixture(filename = 'build/design.svg') {
  const document = {createElement(tag) { return new Element(document, tag); }};
  const container = document.createElement('div');
  const dispose = mountSchematic(container, 'blob:controlled-artifact', filename);
  const [title, controls, status, viewport] = container.children;
  return {document, container, title, controls, status, viewport, image: viewport.children[0], zoom: controls.children[0].children[0], dispose};
}
const pointer = (target, type, values = {}) => target.dispatchEvent(Object.assign(new Event(type, {cancelable: true}), {button: 0, pointerId: 7, clientX: 10, clientY: 20, ...values}));

test('schematic uses an image context and literal artifact labels with accessible controls', () => {
  const filename = 'build/<svg onload="alert(1)">.svg';
  const f = fixture(filename);
  assert.equal(f.title.textContent, filename);
  assert.equal(f.image.tagName, 'img');
  assert.equal(f.image.src, 'blob:controlled-artifact');
  assert.equal(f.image.alt, 'Schematic artifact ' + filename);
  assert.equal(f.image.draggable, false);
  assert.equal(f.viewport.tabIndex, 0);
  assert.equal(f.viewport.attributes.role, 'region');
  assert.equal(f.viewport.attributes['aria-label'], 'Schematic viewport');
  assert.equal(f.zoom.attributes['aria-label'], 'Schematic zoom');
  assert.deepEqual(f.zoom.children.map(option => option.value), ['50', '75', '100', '150', '200', '300']);
  f.dispose();
});

test('zoom, fit, drag, cancellation and pan buttons operate without replacing the artifact', () => {
  const f = fixture();
  f.zoom.value = '300'; f.zoom.dispatchEvent(new Event('change'));
  assert.equal(f.image.className, 'schematic-image zoom-300');
  f.viewport.scrollLeft = 200; f.viewport.scrollTop = 100;
  assert.equal(pointer(f.viewport, 'pointerdown'), false);
  assert.equal(f.document.activeElement, f.viewport);
  assert.equal(f.viewport.capturedPointer, 7);
  assert.equal(f.viewport.classList.contains('panning'), true);
  pointer(f.viewport, 'pointerdown', {pointerId: 8, isPrimary: false});
  pointer(f.viewport, 'pointerup', {pointerId: 8});
  assert.equal(f.viewport.capturedPointer, 7);
  assert.equal(f.viewport.classList.contains('panning'), true);
  pointer(f.viewport, 'pointermove', {clientX: -20, clientY: -30});
  assert.deepEqual([f.viewport.scrollLeft, f.viewport.scrollTop], [230, 150]);
  pointer(f.viewport, 'pointermove', {pointerId: 8, clientX: -100});
  assert.equal(f.viewport.scrollLeft, 230);
  pointer(f.viewport, 'pointercancel');
  assert.equal(f.viewport.classList.contains('panning'), false);
  pointer(f.viewport, 'pointermove', {clientX: -100});
  assert.equal(f.viewport.scrollLeft, 230);
  const panRight = f.controls.children.find(child => child.textContent === 'Pan right');
  panRight.dispatchEvent(new Event('click'));
  assert.equal(f.viewport.scrollLeft, 350);
  f.controls.children.find(child => child.textContent === 'Fit schematic').dispatchEvent(new Event('click'));
  assert.deepEqual([f.viewport.scrollLeft, f.viewport.scrollTop], [0, 0]);
  assert.equal(f.zoom.value, '100');
  assert.equal(f.image.className, 'schematic-image zoom-100');
  assert.equal(f.image.src, 'blob:controlled-artifact');
  f.dispose();
});

test('image errors give a download fallback and disposal removes the preview and drag/image listeners', () => {
  const f = fixture();
  f.image.dispatchEvent(new Event('error'));
  assert.match(f.status.textContent, /could not be displayed.*Download/);
  pointer(f.viewport, 'pointerdown', {button: 2});
  assert.equal(f.viewport.capturedPointer, undefined);
  const originalStatus = f.status.textContent;
  f.dispose();
  assert.deepEqual(f.container.children, []);
  f.zoom.value = '200'; f.zoom.dispatchEvent(new Event('change'));
  assert.equal(f.image.className, 'schematic-image zoom-100');
  f.status.textContent = 'after disposal'; f.image.dispatchEvent(new Event('error'));
  assert.equal(f.status.textContent, 'after disposal');
  assert.notEqual(f.status.textContent, originalStatus);
  pointer(f.viewport, 'pointerdown'); pointer(f.viewport, 'pointermove', {clientX: -100});
  assert.deepEqual([f.viewport.scrollLeft, f.viewport.scrollTop], [0, 0]);
});
