import assert from 'node:assert/strict';
import test from 'node:test';
import {eventIndexAt, formatValue, parseVCD, VCDParseError} from '../web/workspace/vcd.mjs';

const header = '$timescale 10 ps $end\n$scope module top $end\n$var wire 1 ! clock $end\n$var wire 8 # data [7:0] $end\n$upscope $end\n$enddefinitions $end\n';
const scalar = '$var wire 1 ! a $end $enddefinitions $end ';

test('parses real scalar/vector VCD with scopes, aliases, timescale and dump blocks', () => {
  const result = parseVCD(`$date Today $end
$version test fixture $end
$timescale 10 ps $end
$scope module top $end
$var wire 1 ! clock $end
$var wire 8 # data [7:0] $end
$scope module child $end
$var reg 8 # alias [7:0] $end
$upscope $end $upscope $end
$enddefinitions $end
$dumpvars 0! bx # $end
#5 1! b101 #
#10 0! b10xz #
#15 1! bz #
#20 $dumpoff x! bx # $end
#25 $dumpon 0! b00001111 # $end
#30`);
  assert.deepEqual(result.timescale, {value: 10, unit: 'ps', label: '10 ps', secondsPerTick: 1e-11});
  assert.equal(result.startTime, 0);
  assert.equal(result.endTime, 30);
  assert.equal(result.totalEvents, 12);
  assert.deepEqual(result.signals.map(signal => signal.name), ['top.clock', 'top.data [7:0]', 'top.child.alias [7:0]']);
  assert.equal(result.signals[1].events, result.signals[2].events);
  assert.deepEqual(result.signals[1].events, [
    {time: 0, value: 'xxxxxxxx'}, {time: 5, value: '00000101'},
    {time: 10, value: '000010xz'}, {time: 15, value: 'zzzzzzzz'},
    {time: 20, value: 'xxxxxxxx'}, {time: 25, value: '00001111'},
  ]);
});

test('supports arbitrary whitespace, compact timescales and escaped references', () => {
  const result = parseVCD('$timescale\t1ns\r\n$end\r\n$scope module top $end $var reg 2 a \\escaped.name [-1:-2] $end $upscope $end $enddefinitions $end #0 bZ a #10 b01 a');
  assert.equal(result.timescale.label, '1 ns');
  assert.equal(result.signals[0].name, 'top.\\escaped.name [-1:-2]');
  assert.deepEqual(result.signals[0].events, [{time: 0, value: 'zz'}, {time: 10, value: '01'}]);
});

test('last update at a timestamp wins and unchanged values are compacted', () => {
  const result = parseVCD(`${scalar}#0 0! 1! #1 1! #2 0! #3 0!`);
  assert.equal(result.totalEvents, 5);
  assert.deepEqual(result.signals[0].events, [{time: 0, value: '1'}, {time: 2, value: '0'}]);
  assert.equal(result.endTime, 3);
});

test('unknown bits remain explicit in every radix and large unsigned values are exact', () => {
  assert.equal(formatValue('00001010'), '0xa');
  assert.equal(formatValue('00001010', 'bin'), '00001010');
  assert.equal(formatValue('00001010', 'unsigned'), '10');
  assert.equal(formatValue('1'.repeat(64), 'unsigned'), '18446744073709551615');
  for (const value of ['10xz', 'xxxx', 'zzzz']) {
    for (const radix of ['hex', 'unsigned', 'bin']) assert.equal(formatValue(value, radix), value);
  }
  assert.equal(formatValue(undefined), '—');
});

test('cursor binary search handles events before, on and after timestamps', () => {
  const events = [{time: 3}, {time: 5}, {time: 20}];
  assert.equal(eventIndexAt([], 0), -1);
  assert.equal(eventIndexAt(events, 0), -1);
  assert.equal(eventIndexAt(events, 3), 0);
  assert.equal(eventIndexAt(events, 4), 0);
  assert.equal(eventIndexAt(events, 5), 1);
  assert.equal(eventIndexAt(events, 21), 2);
});

test('enforces byte, declaration, event, expanded value, width, token, scope and name limits', () => {
  assert.throws(() => parseVCD(scalar, {maxBytes: 2}), /byte file limit/);
  assert.throws(() => parseVCD('$comment é $end ' + scalar, {maxBytes: ('$comment é $end ' + scalar).length}), /byte file limit/);
  assert.throws(() => parseVCD(header, {maxSignals: 1}), /signal limit/);
  assert.throws(() => parseVCD(`${scalar}0! #1 1!`, {maxEvents: 1}), /event limit/);
  assert.throws(() => parseVCD(`${header}b1 #`, {maxValueBits: 4}), /value storage limit/);
  assert.throws(() => parseVCD(header, {maxWidth: 4}), /signal width/);
  assert.throws(() => parseVCD(`${scalar}$comment ${'a'.repeat(20)} $end`, {maxTokenLength: 15}), /token is too long/);
  assert.throws(() => parseVCD('$scope module top $end $scope module child $end', {maxScopeDepth: 1}), /nesting limit/);
  assert.throws(() => parseVCD('$var wire 1 ! abcdef $end $enddefinitions $end', {maxNameLength: 3}), /name length limit/);
  assert.throws(() => parseVCD(scalar, {maxEvents: -1}), /Invalid VCD limit/);
  assert.throws(() => parseVCD(scalar, {unknownLimit: 1}), /Invalid VCD limit/);
});

test('counts repeated values against the event bound rather than trusting compacted storage', () => {
  assert.throws(() => parseVCD(`${scalar}0! 0! 0!`, {maxEvents: 2}), /event limit/);
});

test('rejects malformed, unsupported and precision-losing traces with clear failures', () => {
  for (const [text, message] of [
    ['$var wire 1 ! a $end', /missing \$enddefinitions/],
    ['$scope module top', /missing \$end/],
    ['$upscope $end', /Unbalanced/],
    ['$scope module top $end $enddefinitions $end', /unclosed scopes/],
    ['$var wire 0 ! a $end', /signal width/],
    ['$var wire 1 ! a $end $var wire 2 ! alias $end', /disagree on width/],
    ['$timescale 3 ns $end', /timescale/],
    ['$timescale 1 ns $end $timescale 10 ns $end', /timescale/],
    ['$var real 64 ! a $end', /real signals are unsupported/],
    ['$var garbage 1 ! a $end', /variable type/],
    ['$enddefinitions $end', /no supported signals/],
    [`${scalar}#2 0! #1 1!`, /backwards/],
    [`${scalar}#9007199254740992 0!`, /safe integer/],
    [`${scalar}#NaN`, /timestamp/],
    [`${scalar}0?`, /undeclared identifier/],
    [`${scalar}b101 !`, /declared width/],
    [`${scalar}b1q !`, /binary VCD value/],
    [`${scalar}b1`, /missing its identifier/],
    [`${scalar}$dumpvars 0!`, /Unterminated VCD dump/],
    [`${scalar}$dumpvars $dumpvars`, /Nested VCD dump/],
    [`${scalar}$end`, /Unexpected \$end/],
    [`${scalar}r1.2 !`, /Real-valued/],
    [`${scalar}$unsupported $end`, /Unsupported VCD directive/],
    ['$var wire 1 ! a lots of extra fields in this declaration $end', /too many fields/],
  ]) assert.throws(() => parseVCD(text), error => error instanceof VCDParseError && message.test(error.message), text);
});

test('a timestamp without value changes still defines the visible duration', () => {
  const result = parseVCD(`${scalar}#10`);
  assert.deepEqual(result.signals[0].events, []);
  assert.equal(result.endTime, 10);
  assert.equal(result.timescale.secondsPerTick, null);
  assert.equal(result.timescale.label, 'unspecified');
});
