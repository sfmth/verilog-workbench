// VCD parsing runs in a worker in the browser. These limits also bound malformed
// input, rather than relying on how many traces eventually become visible.
export const VCD_LIMITS = Object.freeze({
  maxBytes: 16 * 1024 * 1024,
  maxSignals: 2048,
  maxEvents: 250000,
  maxValueBits: 32 * 1024 * 1024,
  maxWidth: 4096,
  maxTokenLength: 8192,
  maxScopeDepth: 64,
  maxNameLength: 4096,
});

export class VCDParseError extends Error {
  constructor(message) {
    super(message);
    this.name = 'VCDParseError';
  }
}

export function parseVCD(text, options = {}) {
  const limits = {...VCD_LIMITS, ...options};
  for (const [key, value] of Object.entries(limits)) {
    if (!(key in VCD_LIMITS) || !Number.isSafeInteger(value) || value < 1) {
      throw new VCDParseError(`Invalid VCD limit: ${key}.`);
    }
  }
  const fail = message => { throw new VCDParseError(message); };
  if (typeof text !== 'string') fail('VCD input must be text.');
  if (text.length > limits.maxBytes || new TextEncoder().encode(text).byteLength > limits.maxBytes) {
    fail(`VCD exceeds the ${limits.maxBytes}-byte file limit.`);
  }
  let position = 0;
  const whitespace = code => code === 32 || (code >= 9 && code <= 13);
  function token() {
    while (position < text.length && whitespace(text.charCodeAt(position))) position++;
    if (position === text.length) return null;
    const start = position;
    while (position < text.length && !whitespace(text.charCodeAt(position))) {
      if (++position - start > limits.maxTokenLength) fail('VCD token is too long.');
    }
    return text.slice(start, position);
  }
  function directiveBody(keep = true) {
    const parts = [];
    for (let part; (part = token()) !== null;) {
      if (part === '$end') return parts;
      if (keep) {
        if (parts.length >= 8) fail('VCD directive has too many fields.');
        parts.push(part);
      }
    }
    fail('Unterminated VCD directive (missing $end).');
  }
  const scopes = [];
  const signals = [];
  const traces = new Map();
  let timescale = {value: 1, unit: 'tick', label: 'unspecified', secondsPerTick: null};
  let declaredTimescale = false;
  let definitionsEnded = false;
  let time = 0;
  let endTime = 0;
  let totalEvents = 0;
  let totalValueBits = 0;
  let dumpBlock = false;
  function record(code, value, kind) {
    const trace = traces.get(code);
    if (!trace) fail(`VCD value change uses undeclared identifier ${code}.`);
    if (++totalEvents > limits.maxEvents) fail(`VCD exceeds the ${limits.maxEvents}-event limit.`);
    if ((totalValueBits += trace.width) > limits.maxValueBits) fail('VCD expanded value storage limit exceeded.');
    if (kind === 'real') fail(`Real-valued VCD signal ${code} is not supported; use scalar or binary vectors.`);
    if (!/^[01xz]+$/i.test(value)) fail(`Invalid binary VCD value for ${code}.`);
    value = value.toLowerCase();
    if (value.length > trace.width) fail(`VCD value for ${code} exceeds its declared width.`);
    // VCD zero-extends short binary words, except unknown/high-impedance words.
    value = value.padStart(trace.width, /^[xz]/.test(value) ? value[0] : '0');
    const previous = trace.events.at(-1);
    if (previous?.time === time) previous.value = value;
    else if (previous?.value !== value) trace.events.push({time, value});
  }
  for (let item; (item = token()) !== null;) {
    if (item.startsWith('$')) {
      if (definitionsEnded && ['$dumpvars', '$dumpall', '$dumpon', '$dumpoff'].includes(item)) {
        if (dumpBlock) fail('Nested VCD dump directives are invalid.');
        dumpBlock = true;
        continue;
      }
      if (item === '$end' && definitionsEnded && dumpBlock) {
        dumpBlock = false;
        continue;
      }
      if (item === '$end') fail('Unexpected $end in VCD.');
      if (item === '$comment' || item === '$date' || item === '$version') {
        directiveBody(false);
        continue;
      }
      const body = directiveBody();
      if (definitionsEnded) fail(`Unsupported VCD directive: ${item}.`);
      if (item === '$scope') {
        if (body.length !== 2) fail('Invalid VCD scope declaration.');
        if (scopes.length >= limits.maxScopeDepth) fail('VCD scope nesting limit exceeded.');
        scopes.push(body[1]);
      } else if (item === '$upscope') {
        if (body.length || !scopes.length) fail('Unbalanced VCD scope declaration.');
        scopes.pop();
      } else if (item === '$timescale') {
        const match = /^(1|10|100)(s|ms|us|ns|ps|fs)$/.exec(body.join(''));
        if (!match || declaredTimescale) fail('Invalid or repeated VCD timescale.');
        const value = Number(match[1]);
        const unit = match[2];
        timescale = {value, unit, label: `${value} ${unit}`, secondsPerTick: value * ({s: 1, ms: 1e-3, us: 1e-6, ns: 1e-9, ps: 1e-12, fs: 1e-15})[unit]};
        declaredTimescale = true;
      } else if (item === '$var') {
        if (body.length < 4 || body.length > 5) fail('Invalid VCD variable declaration.');
        const [type, widthText, code, reference, range] = body;
        const width = Number(widthText);
        if (!/^\d+$/.test(widthText) || !Number.isSafeInteger(width) || width < 1 || width > limits.maxWidth) {
          fail(`VCD signal width must be between 1 and ${limits.maxWidth}.`);
        }
        if (type === 'real' || type === 'realtime' || type === 'string') {
          fail(`VCD ${type} signals are unsupported; use scalar or binary vectors.`);
        }
        if (!['event', 'integer', 'parameter', 'reg', 'supply0', 'supply1', 'time', 'tri', 'triand', 'trior', 'trireg', 'tri0', 'tri1', 'wand', 'wire', 'wor', 'logic', 'bit'].includes(type)) {
          fail(`Unsupported VCD variable type: ${type}.`);
        }
        if (!/^[\x21-\x7e]+$/.test(code) || (range && !/^\[-?\d+(?::-?\d+)?\]$/.test(range))) {
          fail('Invalid VCD identifier or signal range.');
        }
        if (signals.length >= limits.maxSignals) fail(`VCD exceeds the ${limits.maxSignals}-signal limit.`);
        let trace = traces.get(code);
        if (trace && trace.width !== width) fail(`VCD aliases for ${code} disagree on width.`);
        if (!trace) {
          trace = {width, events: []};
          traces.set(code, trace);
        }
        const name = [...scopes, reference + (range ? ` ${range}` : '')].join('.');
        if (name.length > limits.maxNameLength) fail('VCD signal name length limit exceeded.');
        signals.push({id: `signal-${signals.length}`, code, name, width, type, events: trace.events});
      } else if (item === '$enddefinitions') {
        if (body.length || scopes.length) fail('VCD definitions have unclosed scopes or invalid content.');
        definitionsEnded = true;
      } else {
        fail(`Unsupported VCD directive: ${item}.`);
      }
      continue;
    }
    if (!definitionsEnded) fail('VCD value changes appeared before $enddefinitions.');
    if (item.startsWith('#')) {
      if (!/^#\d+$/.test(item)) fail('Invalid VCD timestamp.');
      const next = Number(item.slice(1));
      if (!Number.isSafeInteger(next)) fail('VCD timestamp exceeds the safe integer range.');
      if (next < time) fail('VCD timestamps must not go backwards.');
      time = next;
      endTime = next;
    } else if (/^[01xz]/i.test(item)) {
      if (item.length < 2) fail('VCD scalar value is missing its identifier.');
      record(item.slice(1), item[0], 'binary');
    } else if (/^[br]/i.test(item)) {
      const code = token();
      if (!code) fail('VCD vector value is missing its identifier.');
      record(code, item.slice(1), item[0].toLowerCase() === 'r' ? 'real' : 'binary');
    } else {
      fail(`Invalid or unsupported VCD value change: ${item.slice(0, 40)}.`);
    }
  }
  if (!definitionsEnded) fail('VCD is missing $enddefinitions.');
  if (dumpBlock) fail('Unterminated VCD dump directive.');
  if (!signals.length) fail('VCD contains no supported signals.');
  return {timescale, signals, startTime: 0, endTime, totalEvents};
}

export function formatValue(value, radix = 'hex') {
  if (value == null) return '—';
  if (radix === 'bin') return value;
  if (radix === 'unsigned') return /[xz]/.test(value) ? value : BigInt(`0b${value}`).toString(10);
  if (!/[xz]/.test(value)) return `0x${BigInt(`0b${value}`).toString(16)}`;
  // Keep mixed unknown nibbles explicit: converting 10xz to a hex digit would
  // discard information, so use the binary spelling for that entire word.
  return value;
}

export function eventIndexAt(events, time) {
  let low = 0;
  let high = events.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (events[middle].time <= time) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}
