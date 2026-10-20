import assert from 'node:assert/strict';
import test from 'node:test';
import {buildHdlJob, selectedTest, runSummary, designHierarchy} from '../web/workspace/hdl.mjs';

test('selected native test preserves its explicit file/top and clears incompatible Cocotb filters', () => {
  const selected = selectedTest({name: 'dut'}, {path: 'test/one.vhd', top: 'tb_dut', kind: 'vhdl'});
  const payload = buildHdlJob({command: 'test', ...selected, waves: true});
  assert.deepEqual(payload.options, {modules: ['dut'], test_language: 'vhdl', test: 'test/one.vhd', test_top: 'tb_dut', waves: true, wave_format: 'vcd'});
});
test('simulation forwards function, repeatable seed, includes/defines, arrays, plusargs and real gate check', () => {
  const options = buildHdlJob({command: 'test', module: 'one', test_language: 'cocotb', test: 'test/test_dut.py', testcase: 'truth_table', seed: '0', waves: true, gate_level: true, max_array_words: '0', keep_going: true, defines: 'ENABLE\nWIDTH=8\n', includes: 'src/include µ\n', plusargs: '+CHECK=1'}).options;
  assert.deepEqual(options.modules, ['one']);
  assert.deepEqual(buildHdlJob({command: 'test', module: 'one, two'}).options.modules, ['one', 'two']);
  assert.equal(options.testcase, 'truth_table'); assert.equal(options.seed, 0);
  assert.equal(options.gate_level, true); assert.equal(options.max_array_words, 0);
  assert.deepEqual(options.defines, ['ENABLE', 'WIDTH=8']); assert.deepEqual(options.includes, ['src/include µ']);
  assert.deepEqual(options.plusargs, ['+CHECK=1']); assert.equal(options.keep_going, true);
});
test('saved waveform management excludes stale simulation options', () => {
  const stale = {command: 'wave', module: 'dut', test: 'test/old.py', seed: 'bad', defines: 'old', gate_level: true};
  assert.deepEqual(buildHdlJob({...stale, wave_mode: 'list'}).options, {modules: ['dut'], list_saved: true});
  assert.deepEqual(buildHdlJob({...stale, wave_mode: 'load', wave_tag: 'known-good'}).options, {modules: ['dut'], load: 'known-good'});
  const archived = buildHdlJob({command: 'wave', module: 'dut', wave_tag: 'good', replace_tag: true, waves: false}).options;
  assert.equal(archived.waves, true); assert.equal(archived.tag, 'good'); assert.equal(archived.replace_tag, true);
});
test('simulation engine selection is bounded and does not leak into saved-wave management', () => {
  for (const command of ['test', 'wave']) for (const simulator of ['icarus', 'verilator', 'ghdl']) {
    assert.equal(buildHdlJob({command, simulator}).options.simulator, simulator);
  }
  assert.equal(buildHdlJob({command: 'test', simulator: 'auto'}).options.simulator, undefined);
  assert.throws(() => buildHdlJob({command: 'test', simulator: 'shell'}));
  assert.deepEqual(buildHdlJob({command: 'wave', wave_mode: 'list', simulator: 'invalid'}).options, {list_saved: true});
});
test('project testbench discovery excludes stale HDL controls and simulator overrides', () => {
  const request = buildHdlJob({command: 'flow', flow: '  ', flow_simulator: 'invalid-stale-selection', module: '--help', seed: 'bad', test: 'old.py', gate_level: true, stage: 'flash'});
  assert.deepEqual(request, {command: 'flow', options: {list: true}, timeout: 300});
});
test('named project testbenches forward only the declared name, simulator and bounded timeout', () => {
  assert.deepEqual(buildHdlJob({command: 'flow', flow: '  picorv32-upstream-regression ', flow_simulator: 'default', flow_timeout: '7200', module: 'stale', defines: 'OLD=1'}), {
    command: 'flow', options: {flow: 'picorv32-upstream-regression'}, timeout: 7200,
  });
  for (const simulator of ['icarus', 'verilator', 'ghdl', 'questa', 'modelsim', 'vcs', 'xcelium', 'riviera', 'activehdl']) {
    assert.deepEqual(buildHdlJob({command: 'flow', flow: 'original-bench', flow_simulator: simulator, flow_timeout: '1'}), {
      command: 'flow', options: {flow: 'original-bench', simulator}, timeout: 1,
    });
  }
  assert.equal(buildHdlJob({command: 'flow', flow: 'a'.repeat(96)}).options.flow.length, 96);
});
test('invalid project flow names, simulators and timeouts cannot reach the companion', () => {
  for (const flow of ['--help', 'Uppercase', '_private', 'folder/bench', 'a:b', 'a b', 'a\nb', 'a'.repeat(97)]) {
    assert.throws(() => buildHdlJob({command: 'flow', flow}), Error, flow);
  }
  assert.throws(() => buildHdlJob({command: 'flow', flow: 'original', flow_simulator: 'shell'}));
  for (const flow_timeout of ['0', '-1', '7201', '1.5', '1e3', 'Infinity', 'bad']) {
    assert.throws(() => buildHdlJob({command: 'flow', flow: 'original', flow_timeout}), Error, flow_timeout);
  }
});
test('synthesis and FPGA expose actual hierarchy and compile settings, with flash explicit', () => {
  const synth = buildHdlJob({command: 'synth', module: 'dut', synth_format: 'json', full: true, flatten: true, defines: 'MODE=1', includes: 'src'}).options;
  assert.equal(synth.schematic, false); assert.equal(synth.full, true); assert.equal(synth.flatten, true);
  assert.deepEqual(synth.defines, ['MODE=1']);
  assert.deepEqual(buildHdlJob({command: 'fpga', module: 'dut', board: 'icebreaker', stage: 'pack', constraints: 'src/io.pcf'}).options, {module: 'dut', board: 'icebreaker', stage: 'pack', program: false, constraints: 'src/io.pcf'});
  assert.equal(buildHdlJob({command: 'fpga', module: 'dut', board: 'tangnano9k'}, true).options.stage, 'flash');
});
test('invalid HDL requests fail before saving or submitting native jobs', () => {
  for (const state of [
    {command: 'shell'}, {command: 'test', module: '--help'}, {command: 'test', module: 'a '.repeat(33)},
    {command: 'synth', module: 'a,b'}, {command: 'synth'}, {command: 'test', seed: '-1'},
    {command: 'test', seed: '1.2'}, {command: 'test', seed: '4294967296'},
    {command: 'test', test_language: 'vhdl', testcase: 'foo'}, {command: 'test', max_array_words: '1000001'},
    {command: 'test', test_language: 'cocotb', testcase: 'not$a_function'},
    {command: 'test', module: 'one,two', test: 'test/one.py'}, {command: 'test', test_language: 'cocotb', test_top: 'tb'},
    {command: 'test', defines: 'A\n'.repeat(33)}, {command: 'wave', wave_mode: 'load'},
    {command: 'wave', replace_tag: true}, {command: 'fpga', module: 'a', board: 'other'},
    {command: 'fpga', module: 'a', board: 'icebreaker', stage: 'flash'},
  ]) assert.throws(() => buildHdlJob(state), Error, JSON.stringify(state));
});
test('gate skips and actual formal failures are visible without treating command completion as proof', () => {
  assert.match(runSummary({command: 'test', stdout: 'RTL PASS\nGATE SKIPPED (yosys not installed)'}), /skipped/);
  assert.match(runSummary({command: 'test', status: 'passed', stdout: 'RTL PASS\nGATE PASS'}), /synthesized gate tests passed/);
  assert.doesNotMatch(runSummary({command: 'test', status: 'failed', stdout: 'GATE PASS\nGATE FAIL'}), /tests passed/);
  assert.doesNotMatch(runSummary({command: 'test', status: 'passed', stdout: 'GATE PASS\nGATE SKIPPED'}), /tests passed/);
  assert.doesNotMatch(runSummary({command: 'formal', status: 'failed', stdout: 'DONE (PASS)\nDONE (FAIL)'}), /proof passed/);
  assert.match(runSummary({command: 'formal', stdout: 'SBY [bad] DONE (FAIL, rc=2)'}), /counterexample/);
  assert.equal(runSummary({command: 'formal', stdout: 'tool crashed'}), '');
});

test('dependency hierarchy includes supporting units, marks unresolved/cyclic edges and bounds expansion', () => {
  const discovery = {modules: [{name: 'top', files: ['src/top.sv'], dependencies: ['child', 'pkg', 'external']}, {name: 'child', dependencies: ['top']}], packages: [{name: 'pkg', files: ['src/pkg.sv']}]};
  const tree = designHierarchy(discovery, 'top');
  assert.equal(tree.children[0].children[0].cycle, true);
  assert.deepEqual(tree.children[1].files, ['src/pkg.sv']);
  assert.equal(tree.children[2].unresolved, true);
  const bounded = designHierarchy(discovery, 'top', {maxNodes: 2});
  assert.equal(bounded.truncated, true); assert.equal(bounded.children.length, 1);
  const shallow = designHierarchy(discovery, 'top', {maxDepth: 0});
  assert.equal(shallow.truncated, true); assert.deepEqual(shallow.children, []);
});
