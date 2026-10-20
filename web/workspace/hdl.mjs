const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_$]*$/;
const COMMANDS = new Set(['list', 'doctor', 'test', 'wave', 'lint', 'synth', 'formal', 'fpga']);

function identifier(value, label) {
  if (!IDENTIFIER.test(value) || value.length > 128) throw new Error(`${label} must be an ordinary HDL identifier.`);
  return value;
}
function lines(value, label, maximum = 32) {
  const result = String(value || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (result.length > maximum || result.some(line => line.length > 512 || /[\x00-\x1f\x7f]/.test(line))) throw new Error(`${label}: use at most ${maximum} lines of 512 characters.`);
  return result;
}
function unsigned(value, label, maximum) {
  const number = Number(value);
  if (!/^\d+$/.test(String(value)) || !Number.isSafeInteger(number) || number > maximum) throw new Error(`${label} must be an integer from 0 to ${maximum.toLocaleString('en-US')}.`);
  return number;
}
function choice(value, choices, label) {
  if (!choices.includes(value)) throw new Error(`Invalid ${label}.`);
  return value;
}

/** Build only the typed native workflows exposed by the companion. */
export function buildHdlJob(state, program = false) {
  const command = state.command;
  if (!COMMANDS.has(command)) throw new Error('Choose a supported native workflow.');
  const names = String(state.module || '').trim().split(/[\s,]+/).filter(Boolean).map(value => identifier(value, 'Top/module'));
  if (names.length > 32) throw new Error('Choose at most 32 modules.');
  const options = {};
  if (['synth', 'fpga'].includes(command)) {
    if (names.length !== 1) throw new Error('Choose exactly one top/module for this action.');
    options.module = names[0];
  } else if (['test', 'wave', 'lint'].includes(command) && names.length) options.modules = names;
  if (command === 'wave' && ['list', 'load'].includes(state.wave_mode)) {
    if (state.wave_mode === 'list') options.list_saved = true;
    else {
      if (!String(state.wave_tag || '').trim()) throw new Error('Choose a saved waveform tag.');
      if (names.length > 1) throw new Error('Opening a saved waveform accepts at most one module filter.');
      options.load = String(state.wave_tag).trim();
    }
    return {command, options, timeout: 300};
  }
  if (['test', 'wave'].includes(command)) {
    options.test_language = choice(state.test_language || 'auto', ['auto', 'cocotb', 'verilog', 'vhdl'], 'test language');
    options.waves = command === 'wave' || !!state.waves;
    options.wave_format = choice(state.wave_format || 'vcd', ['vcd', 'fst'], 'waveform format');
    for (const key of ['test', 'test_top', 'testcase']) {
      const value = String(state[key] || '').trim();
      if (key === 'testcase' && value && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(value)) throw new Error('Cocotb function must be a Python function name.');
      if (value) options[key] = key === 'test' ? value : identifier(value, key === 'testcase' ? 'Cocotb function' : 'Testbench top');
    }
    if (options.test && names.length > 1) throw new Error('Choose one design when selecting an explicit test file.');
    if (options.test_top && (options.test_language === 'cocotb' || /\.py$/i.test(options.test || ''))) throw new Error('Testbench top applies to HDL testbenches. Clear it for Cocotb tests.');
    if (state.seed !== '' && state.seed !== undefined) options.seed = unsigned(state.seed, 'Seed', 4294967295);
    if ((options.testcase || options.seed !== undefined) && ['verilog', 'vhdl'].includes(options.test_language)) throw new Error('A test function or seed requires a Cocotb test.');
    if (state.max_array_words !== '' && state.max_array_words !== undefined) options.max_array_words = unsigned(state.max_array_words, 'Array words', 1000000);
    if (state.gate_level) options.gate_level = true;
    if (state.keep_going && command === 'test') options.keep_going = true;
    const plusargs = lines(state.plusargs, 'Simulator plusargs');
    if (plusargs.length) options.plusargs = plusargs;
    if (command === 'wave') {
      const tag = String(state.wave_tag || '').trim();
      if (tag) options.tag = tag;
      if (state.replace_tag) {
        if (!tag) throw new Error('Enter a tag before replacing a saved waveform.');
        options.replace_tag = true;
      }
    }
  }
  if (['test', 'wave', 'lint', 'synth', 'fpga'].includes(command)) {
    const defines = lines(state.defines, 'Defines'), includes = lines(state.includes, 'Include folders');
    if (defines.length) options.defines = defines;
    if (includes.length) options.includes = includes;
  }
  if (command === 'lint') {
    options.linters = [choice(state.linter || 'all', ['all', 'iverilog', 'verilator', 'yosys', 'verible', 'ghdl'], 'linter')];
    if (!names.length) options.all = true;
    if (state.keep_going) options.keep_going = true;
  }
  if (command === 'synth') {
    options.format = choice(state.synth_format || 'svg', ['svg', 'png', 'json', 'dot'], 'synthesis format');
    options.schematic = state.schematic ?? ['svg', 'png'].includes(options.format);
    if (state.full) options.full = true;
    if (state.flatten) options.flatten = true;
  }
  if (command === 'formal' && String(state.formal_config || '').trim()) options.config = String(state.formal_config).trim();
  if (command === 'fpga') {
    options.board = choice(state.board, ['tangnano9k', 'icebreaker'], 'board');
    options.stage = program ? 'flash' : choice(state.stage || 'pack', ['synth', 'pnr', 'pack'], 'FPGA stage');
    options.program = !!program;
    if (String(state.constraints || '').trim()) options.constraints = String(state.constraints).trim();
  }
  return {command, options, timeout: 300};
}

export function selectedTest(design, test) {
  return {module: design.name, test: test.path, test_top: test.top || '', test_language: test.kind, testcase: '', seed: ''};
}

/** The catalog exposes unit dependencies, not elaborated instance names. */
export function designHierarchy(discovery, name, {maxNodes = 256, maxDepth = 24} = {}) {
  const units = new Map(['modules', 'interfaces', 'primitives', 'packages'].flatMap(key => discovery?.[key] || []).map(unit => [unit.name, unit]));
  let remaining = Math.max(1, Math.min(256, maxNodes));
  function visit(current, ancestors, depth) {
    if (remaining-- <= 0) return null;
    const unit = units.get(current);
    const cyclic = ancestors.has(current);
    const entry = {name: current, files: unit?.files || [], problems: unit?.problems || [], children: [], ...(cyclic ? {cycle: true} : {}), ...(!unit ? {unresolved: true} : {})};
    if (cyclic || !unit) return entry;
    const dependencies = unit.dependencies || [];
    if (depth >= maxDepth) { if (dependencies.length) entry.truncated = true; return entry; }
    const next = new Set(ancestors); next.add(current);
    for (const child of dependencies) {
      const value = visit(child, next, depth + 1);
      if (!value) { entry.truncated = true; break; }
      entry.children.push(value);
    }
    return entry;
  }
  return visit(name, new Set(), 0);
}

export function runSummary(job) {
  const text = `${job.stdout || ''}\n${job.stderr || ''}`.replace(/\x1b\[[0-9;]*m/g, '');
  const notes = [];
  if (job.status === 'passed' && /\bGATE PASS\b/.test(text) && !/\bGATE (?:FAIL|SKIPPED)\b/.test(text)) notes.push('RTL and synthesized gate tests passed.');
  if (/\bGATE SKIPPED\b/.test(text)) notes.push('Gate test skipped; inspect Output for the reason.');
  if (/\bGATE FAIL\b/.test(text)) notes.push('Synthesized gate test failed.');
  if (/starter|smoke test/i.test(text)) notes.push('Generated starter tests are smoke tests; add assertions for your design.');
  if (job.command === 'formal' && job.status === 'passed' && /DONE\s*\(PASS|Status:\s*PASSED|\bPASS\b.*\bengine_/i.test(text)) notes.push('Formal proof passed.');
  if (job.command === 'formal' && /DONE\s*\(FAIL|Status:\s*FAILED/i.test(text)) notes.push('Formal property failed. Open the generated counterexample VCD in Artifacts.');
  return notes.join(' ');
}
