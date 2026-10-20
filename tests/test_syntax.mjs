import assert from 'node:assert/strict';
import test from 'node:test';
import {highlight, languageForPath, MAX_HIGHLIGHT_BYTES, MAX_HIGHLIGHT_LINES, MAX_HIGHLIGHT_TOKENS} from '../web/workspace/syntax.mjs';

const KINDS = new Set(['comment', 'string', 'keyword', 'number', 'meta', 'operator', 'builtin']);
function decorated(text, language, options) {
  const result = highlight(text, language, options);
  assert.equal(result.mode, 'highlighted', result.reason);
  let end = 0;
  let reconstructed = '';
  for (const item of result.tokens) {
    assert.ok(Number.isSafeInteger(item.from) && Number.isSafeInteger(item.to));
    assert.ok(item.from >= end && item.to > item.from && item.to <= text.length);
    assert.ok(KINDS.has(item.kind), item.kind);
    assert.ok(!(item.from > 0 && /[\uD800-\uDBFF]/.test(text[item.from - 1]) && /[\uDC00-\uDFFF]/.test(text[item.from])));
    assert.ok(!(item.to < text.length && /[\uD800-\uDBFF]/.test(text[item.to - 1]) && /[\uDC00-\uDFFF]/.test(text[item.to])));
    reconstructed += text.slice(end, item.from) + text.slice(item.from, item.to);
    end = item.to;
  }
  reconstructed += text.slice(end);
  assert.equal(reconstructed, text);
  return result.tokens.map(item => ({text: text.slice(item.from, item.to), kind: item.kind}));
}
function pairs(items, kind) { return items.filter(item => item.kind === kind).map(item => item.text); }
function plain(text, language, options, reason) {
  const result = highlight(text, language, options);
  assert.equal(result.mode, 'plain');
  assert.deepEqual(result.tokens, []);
  assert.match(result.reason, reason);
  return result;
}

test('maps supported source extensions without guessing directory names', () => {
  for (const path of ['main.v', 'rtl/MAIN.VH', 'a.sv', 'a.svh']) assert.equal(languageForPath(path), 'systemverilog');
  for (const path of ['design.vhd', 'dir/name.VHDL']) assert.equal(languageForPath(path), 'vhdl');
  for (const path of ['test.py', 'pkg/types.PYI']) assert.equal(languageForPath(path), 'python');
  for (const path of ['', '.v/file', 'pkg.py/name', 'x.py.old', 'README', null, {}]) assert.equal(languageForPath(path), 'plain');
});

test('SystemVerilog comments and strings mask token-like contents across lines', () => {
  const text = 'module m; // always 123 "quote"\n/* begin\nendmodule */\nstring s = "module \\"still\\" // string";\nendmodule\n';
  const items = decorated(text, 'systemverilog');
  assert.deepEqual(pairs(items, 'keyword'), ['module', 'string', 'endmodule']);
  assert.deepEqual(pairs(items, 'comment'), ['// always 123 "quote"', '/* begin\nendmodule */']);
  assert.deepEqual(pairs(items, 'string'), ['"module \\"still\\" // string"']);
  assert.deepEqual(pairs(items, 'number'), []);
});

test('SystemVerilog literals, directives, system tasks and escaped identifiers retain exact slices', () => {
  const text = "`include \"x.svh\"\n`define MASK 4'bx01z\nlocalparam A='0, B='1, C=16'shF_F, D='hff, E=3.25e-2, F=10ns;\n$display(\\module//fake  ); MODULE module_name;";
  const items = decorated(text, 'systemverilog');
  assert.deepEqual(pairs(items, 'meta'), ['`include', '`define']);
  assert.deepEqual(pairs(items, 'number'), ["4'bx01z", "'0", "'1", "16'shF_F", "'hff", '3.25e-2', '10ns']);
  assert.deepEqual(pairs(items, 'builtin'), ['$display']);
  assert.deepEqual(pairs(items, 'keyword'), ['localparam']);
  assert.deepEqual(pairs(items, 'comment'), []);
});

test('unclosed SystemVerilog edit states are bounded and resume after a single-line string', () => {
  assert.deepEqual(pairs(decorated('/* unfinished\nmodule x;', 'systemverilog'), 'comment'), ['/* unfinished\nmodule x;']);
  const items = decorated('"unfinished\nmodule x;\n"joined\\\r\nline"', 'systemverilog');
  assert.deepEqual(pairs(items, 'string'), ['"unfinished', '"joined\\\r\nline"']);
  assert.deepEqual(pairs(items, 'keyword'), ['module']);
});

test('VHDL keywords are case insensitive and comments/doubled strings remain opaque', () => {
  const text = 'EnTiTy name is -- signal 12 "hidden"\r\nconstant report_text : string := "say ""ENTITY"" -- literal";\nEND ENTITY;\n';
  const items = decorated(text, 'vhdl');
  assert.deepEqual(pairs(items, 'keyword'), ['EnTiTy', 'is', 'constant', 'END', 'ENTITY']);
  assert.deepEqual(pairs(items, 'comment'), ['-- signal 12 "hidden"']);
  assert.deepEqual(pairs(items, 'string'), ['"say ""ENTITY"" -- literal"']);
  assert.deepEqual(pairs(items, 'number'), []);
});

test('VHDL character literals differ from attributes and qualification apostrophes', () => {
  const text = "if clk'event and clk = '1' then c := std_logic'('0'); d := std_logic '('1'); a := ' '; b := '('; q := '''; end if;";
  const items = decorated(text, 'vhdl');
  assert.deepEqual(pairs(items, 'meta'), ["'event"]);
  assert.deepEqual(pairs(items, 'string'), ["'1'", "'0'", "'1'", "' '", "'('", "'''"]);
  assert.deepEqual(pairs(items, 'keyword'), ['if', 'and', 'then', 'end', 'if']);
});

test('VHDL based numbers and bit strings preserve lengths, case and unknown bits', () => {
  const text = '16#ff_ff# 2#10.01#E+2 1_000.5e-3 X"AB_CD" B"10XZ" 8UX"FF" \\ENTITY--not-a-comment\\ \\name\\\\--still-not-comment\\ entity_name';
  const items = decorated(text, 'vhdl');
  assert.deepEqual(pairs(items, 'number'), ['16#ff_ff#', '2#10.01#E+2', '1_000.5e-3', 'X"AB_CD"', 'B"10XZ"', '8UX"FF"']);
  assert.deepEqual(pairs(items, 'keyword'), []);
  assert.deepEqual(pairs(items, 'comment'), []);
});

test('VHDL unclosed strings stop at the edit line and unclosed extended names stay plain', () => {
  const items = decorated('"unfinished\nSIGNAL s : bit;\n\\entity\nend entity;', 'vhdl');
  assert.deepEqual(pairs(items, 'string'), ['"unfinished']);
  assert.deepEqual(pairs(items, 'keyword'), ['SIGNAL', 'end', 'entity']);
});

test('Python prefixes, triple strings and comments are opaque lexical regions', () => {
  const text = `@pkg.decorator\nasync def run():\n    value = r"# not comment"\n    blob = b'abc'\n    docs = '''def hidden():\n# literal\n'''\n    return f"hello {value!r}" # actual comment\n`;
  const items = decorated(text, 'python');
  assert.deepEqual(pairs(items, 'meta'), ['@pkg.decorator']);
  assert.deepEqual(pairs(items, 'keyword'), ['async', 'def', 'return']);
  assert.deepEqual(pairs(items, 'string'), ['r"# not comment"', "b'abc'", "'''def hidden():\n# literal\n'''", 'f"hello {value!r}"']);
  assert.deepEqual(pairs(items, 'comment'), ['# actual comment']);
});

test('Python numeric forms, builtins, borders and Unicode identifiers avoid partial keyword matches', () => {
  const text = 'definite = print(len(πreturn))\nreturn_value = True\nx = 0xFF + 0b10_01 + 0o77 + .5e-2j + 1_000.25 + 12j\na@b\n';
  const items = decorated(text, 'python');
  assert.deepEqual(pairs(items, 'keyword'), ['True']);
  assert.deepEqual(pairs(items, 'builtin'), ['print', 'len']);
  assert.deepEqual(pairs(items, 'number'), ['0xFF', '0b10_01', '0o77', '.5e-2j', '1_000.25', '12j']);
  assert.deepEqual(pairs(items, 'meta'), []);
});

test('Python f-strings remain one opaque token with escaped braces and quoted fields', () => {
  const text = 'f"{{literal}} {mapping["key"]} { {"a": 1} }"\nf\'value {"word"}\'';
  const items = decorated(text, 'python');
  assert.deepEqual(pairs(items, 'string'), ['f"{{literal}} {mapping["key"]} { {"a": 1} }"', 'f\'value {"word"}\'']);
  assert.deepEqual(pairs(items, 'number'), []);
});

test('Python unfinished single/triple strings and escaped CRLF remain safe during typing', () => {
  const items = decorated('"unfinished\ndef x(): pass\ns = "a\\\r\nb"\n"""unfinished\nreturn 3', 'python');
  assert.deepEqual(pairs(items, 'string'), ['"unfinished', '"a\\\r\nb"', '"""unfinished\nreturn 3']);
  assert.deepEqual(pairs(items, 'keyword'), ['def', 'pass']);
});

test('UTF-16 offsets, BOM, CRLF, emoji and literal HTML reconstruct original source exactly', () => {
  const text = '\ufeff# 🚀 <script>alert("x")</script>\r\ndef 𐐀name():\r\n\treturn "é😀<&>"\r\n';
  const result = highlight(text, 'python');
  assert.equal(result.line_count, 4);
  decorated(text, 'python');
  const keyword = result.tokens.find(item => item.kind === 'keyword');
  assert.equal(keyword.from, text.indexOf('def'));
  assert.equal(text.slice(keyword.from, keyword.to), 'def');
  assert.deepEqual(pairs(decorated(text, 'python'), 'string'), ['"é😀<&>"']);
  assert.ok(!Object.hasOwn(result, 'html'));
  assert.ok(!Object.hasOwn(result, 'text'));
});

test('line counts include empty sources, trailing blanks, CRLF and standalone CR', () => {
  for (const [text, count] of [['', 1], ['x', 1], ['x\n', 2], ['\n\n', 3], ['x\r\ny\r', 3], ['\r\n', 2]]) {
    assert.equal(highlight(text, 'python').line_count, count);
  }
});

test('byte bounds count UTF-8, including astral and lone-surrogate replacement encodings', () => {
  assert.equal(MAX_HIGHLIGHT_BYTES, 262144);
  assert.equal(highlight('é', 'python', {maxBytes: 2}).mode, 'highlighted');
  plain('é', 'python', {maxBytes: 1}, /byte limit/);
  assert.equal(highlight('😀', 'python', {maxBytes: 4}).mode, 'highlighted');
  plain('😀', 'python', {maxBytes: 3}, /byte limit/);
  assert.equal(highlight('\ud800', 'python', {maxBytes: 3}).mode, 'highlighted');
  plain('\ud800', 'python', {maxBytes: 2}, /byte limit/);
  assert.equal(highlight('x'.repeat(MAX_HIGHLIGHT_BYTES), 'python').mode, 'highlighted');
  plain('x'.repeat(MAX_HIGHLIGHT_BYTES + 1), 'python', undefined, /byte limit/);
  plain('x'.repeat(MAX_HIGHLIGHT_BYTES + 1), 'python', {maxBytes: MAX_HIGHLIGHT_BYTES * 2}, /byte limit/);
});

test('line and token bounds fall back with no partial decoration', () => {
  assert.equal(MAX_HIGHLIGHT_LINES, 10000);
  assert.equal(MAX_HIGHLIGHT_TOKENS, 8000);
  assert.equal(highlight('\n'.repeat(MAX_HIGHLIGHT_LINES - 1), 'python').mode, 'highlighted');
  const tooManyLines = plain('\n'.repeat(MAX_HIGHLIGHT_LINES), 'python', undefined, /line limit/);
  assert.equal(tooManyLines.line_count, MAX_HIGHLIGHT_LINES + 1);
  plain('x\ny', 'python', {maxLines: 1}, /line limit/);
  assert.equal(highlight('+'.repeat(MAX_HIGHLIGHT_TOKENS), 'python').mode, 'highlighted');
  plain('+'.repeat(MAX_HIGHLIGHT_TOKENS + 1), 'python', undefined, /token limit/);
  plain('def a', 'python', {maxTokens: 0}, /token limit/);
  plain('def return', 'python', {maxTokens: 1}, /token limit/);
  assert.equal(highlight('identifier', 'python', {maxTokens: 0}).mode, 'highlighted');
});

test('unknown languages and invalid source inputs return explicit bounded plain results', () => {
  const result = plain('hello\n', 'html', undefined, /file type/);
  assert.equal(result.language, 'plain');
  assert.equal(result.line_count, 2);
  assert.deepEqual(highlight({text: 'private'}, 'python'), {mode: 'plain', language: 'plain', reason: 'Syntax decoration requires source text.', line_count: 0, tokens: []});
  assert.equal(highlight('def', 'python', null).mode, 'highlighted');
});

test('long near-matches and unfinished delimiters finish within the source/token bounds', () => {
  for (const [text, language] of [
    ['9'.repeat(240000) + 'e+', 'python'],
    ['9'.repeat(240000) + "'Q", 'systemverilog'],
    ['9'.repeat(240000) + '#', 'vhdl'],
    ['f"{' + '{'.repeat(100000), 'python'],
    ['/*' + '*'.repeat(240000), 'systemverilog'],
  ]) decorated(text, language);
});

test('worker validates requests and emits only a bounded source-free result schema', async () => {
  const messages = [];
  const previous = globalThis.self;
  globalThis.self = {postMessage: message => messages.push(message)};
  try {
    await import('../web/workspace/syntax-worker.mjs');
    self.onmessage({data: {id: 12, text: 'def x(): return "<script>"', path: 'main.py', extra: 'secret'}});
    assert.equal(messages.at(-1).id, 12);
    assert.equal(messages.at(-1).language, 'python');
    assert.equal(messages.at(-1).mode, 'highlighted');
    assert.deepEqual(Object.keys(messages.at(-1)).sort(), ['id', 'language', 'line_count', 'mode', 'tokens']);
    for (const id of [-1, Infinity, {}, 'a'.repeat(129), '<script>']) {
      self.onmessage({data: {id, text: 'private', path: 'main.py'}});
      assert.equal(messages.at(-1).id, null);
      assert.equal(messages.at(-1).mode, 'plain');
      assert.deepEqual(messages.at(-1).tokens, []);
      assert.ok(!JSON.stringify(messages.at(-1)).includes('private'));
    }
    self.onmessage({data: {id: 'request_1', text: {}, path: 'main.py'}});
    assert.equal(messages.at(-1).id, 'request_1');
    assert.equal(messages.at(-1).mode, 'plain');
    self.onmessage({data: {id: 'request_2', text: 'def x', path: 'x'.repeat(4097)}});
    assert.equal(messages.at(-1).mode, 'plain');
    self.onmessage({data: {id: 0, text: 'x'.repeat(MAX_HIGHLIGHT_BYTES + 1), path: 'main.py'}});
    assert.match(messages.at(-1).reason, /byte limit/);
  } finally {
    if (previous === undefined) delete globalThis.self;
    else globalThis.self = previous;
  }
});
