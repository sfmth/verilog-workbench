// Lexical decoration only: offsets refer to unchanged UTF-16 source text.
// This does not parse HDL/Python, diagnose errors, or interpret f-string fields.
export const MAX_HIGHLIGHT_BYTES = 262144;
export const MAX_HIGHLIGHT_LINES = 10000;
export const MAX_HIGHLIGHT_TOKENS = 8000;

const LANGUAGES = new Set(['systemverilog', 'vhdl', 'python']);
const SV_KEYWORDS = new Set(`accept_on alias always always_comb always_ff always_latch and assert assign assume automatic before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex casez cell chandle checker class clocking cmos config const constraint context continue cover covergroup coverpoint cross deassign default defparam design disable dist do edge else end endcase endchecker endclass endclocking endconfig endfunction endgenerate endgroup endinterface endmodule endpackage endprimitive endprogram endproperty endsequence endspecify endtable endtask enum event eventually expect export extends extern final first_match for force foreach forever fork forkjoin function generate genvar global highz0 highz1 if iff ifnone ignore_bins illegal_bins implements implies import incdir include initial inout input inside instance int integer interconnect interface intersect join join_any join_none large let liblist library local localparam logic longint macromodule matches medium modport module nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or output package packed parameter pmos posedge primitive priority program property protected pull0 pull1 pulldown pullup pulsestyle_ondetect pulsestyle_onevent pure rand randc randcase randsequence rcmos real realtime ref reg reject_on release repeat restrict return rnmos rpmos rtran rtranif0 rtranif1 s_always s_eventually s_nexttime s_until s_until_with scalared sequence shortint shortreal showcancelled signed small soft solve specify specparam static string strong strong0 strong1 struct super supply0 supply1 sync_accept_on sync_reject_on table tagged task this throughout time timeprecision timeunit tran tranif0 tranif1 tri tri0 tri1 triand trior trireg type typedef union unique unique0 unsigned until until_with untyped use uwire var vectored virtual void wait wait_order wand weak weak0 weak1 while wildcard wire with within wor xnor xor`.split(/\s+/));
const VHDL_KEYWORDS = new Set(`abs access after alias all and architecture array assert assume assume_guarantee attribute begin block body buffer bus case component configuration constant context cover default disconnect downto else elsif end entity exit fairness file for force function generate generic group guarded if impure in inertial inout is label library linkage literal loop map mod nand new next nor not null of on open or others out package parameter port postponed procedure process property protected pure range record register reject release rem report restrict restrict_guarantee return rol ror select sequence severity shared signal sla sll sra srl strong subtype then to transport type unaffected units until use variable vmode vprop vunit wait when while with xnor xor`.split(/\s+/));
const PY_KEYWORDS = new Set(`False None True and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try type while with yield`.split(/\s+/));
const PY_BUILTINS = new Set(`abs aiter all anext any ascii bin bool breakpoint bytearray bytes callable chr classmethod compile complex delattr dict dir divmod enumerate eval exec filter float format frozenset getattr globals hasattr hash help hex id input int isinstance issubclass iter len list locals map max memoryview min next object oct open ord pow print property range repr reversed round set setattr slice sorted staticmethod str sum super tuple type vars zip __import__ ArithmeticError AssertionError AttributeError BaseException BufferError EOFError Exception FileNotFoundError ImportError IndexError KeyError LookupError ModuleNotFoundError NameError NotImplementedError OSError OverflowError PermissionError RuntimeError StopAsyncIteration StopIteration SyntaxError SystemError SystemExit TimeoutError TypeError UnicodeError ValueError Warning ZeroDivisionError`.split(/\s+/));
const PY_PREFIXES = new Set(['r', 'u', 'b', 'f', 'fr', 'rf', 'br', 'rb', 't', 'tr', 'rt']);

const SV_NUMBER = /(?:[0-9][0-9_]*[ \t]*)?'[sS]?[bBoOdDhH][ \t]*[0-9a-fA-F_xXzZ?]+|'[01xXzZ]|(?:[0-9][0-9_]*\.[0-9_]+|[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?(?:step|ms|us|ns|ps|fs|s)?/y;
const VHDL_NUMBER = /[0-9][0-9_]*#[0-9a-fA-F_]+(?:\.[0-9a-fA-F_]+)?#(?:[eE][+-]?[0-9][0-9_]*)?|[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?/y;
const VHDL_BIT_STRING = /(?:[0-9][0-9_]*)?[uUsS]?[bBoOxXdD]"/y;
const PY_NUMBER = /(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?)[jJ]?/y;
const OPERATOR = /[+\-*/%=<>!&|^~?:;,.()[\]{}#@]/;
const ASCII_START = /[A-Za-z_$]/;
const ASCII_PART = /[A-Za-z0-9_$]/;
const PY_START = /[\p{ID_Start}_]/u;
const PY_PART = /[\p{ID_Continue}_]/u;

export function languageForPath(path) {
  if (typeof path !== 'string') return 'plain';
  const extension = /\.([^.\/\\]+)$/.exec(path)?.[1].toLowerCase();
  if (['v', 'vh', 'sv', 'svh'].includes(extension)) return 'systemverilog';
  if (['vhd', 'vhdl'].includes(extension)) return 'vhdl';
  if (['py', 'pyi'].includes(extension)) return 'python';
  return 'plain';
}

function limit(value, maximum) {
  return Number.isSafeInteger(value) && value >= 0 ? Math.min(value, maximum) : maximum;
}

function measure(text, maxBytes) {
  let bytes = 0;
  let lineCount = 1;
  // No split/TextEncoder allocation proportional to an oversized source buffer.
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code === 10 || (code === 13 && text.charCodeAt(i + 1) !== 10)) lineCount++;
    if (bytes > maxBytes) continue;
    if (code <= 0x7f) bytes++;
    else if (code <= 0x7ff) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) {
      bytes += 4;
      i++;
    } else bytes += 3; // Includes the UTF-8 replacement encoding for lone surrogates.
  }
  return {bytes, lineCount};
}

function matchAt(pattern, text, offset) {
  pattern.lastIndex = offset;
  return pattern.exec(text)?.[0];
}

function endOfLine(text, from) {
  let i = from;
  while (i < text.length && text[i] !== '\n' && text[i] !== '\r') i++;
  return i;
}

function doubleQuoted(text, from, doubled = false) {
  let i = from + 1;
  while (i < text.length) {
    if (text[i] === '"') {
      if (doubled && text[i + 1] === '"') { i += 2; continue; }
      return i + 1;
    }
    if (!doubled && text[i] === '\\') {
      i += text[i + 1] === '\r' && text[i + 2] === '\n' ? 3 : 2;
    } else if (text[i] === '\n' || text[i] === '\r') return i;
    else i++;
  }
  return text.length;
}

function pythonQuoted(text, quoteAt, formatted = false) {
  const quote = text[quoteAt];
  const triple = text.slice(quoteAt, quoteAt + 3) === quote.repeat(3);
  const width = triple ? 3 : 1;
  let i = quoteAt + width;
  let braces = 0;
  while (i < text.length) {
    if (text[i] === '\\') {
      i += text[i + 1] === '\r' && text[i + 2] === '\n' ? 3 : 2;
      continue;
    }
    if (formatted && braces > 0) {
      // Replacement fields remain part of one opaque string token. Skip quoted
      // expressions, including matching outer quotes, without evaluating them.
      if (text[i] === '"' || text[i] === "'") { i = pythonQuoted(text, i); continue; }
      if (text[i] === '#') { i = endOfLine(text, i); continue; }
      if (text[i] === '{') braces++;
      if (text[i] === '}') braces--;
      i++;
      continue;
    }
    if (text[i] === quote && (!triple || text.slice(i, i + 3) === quote.repeat(3))) return i + width;
    if (!triple && (text[i] === '\n' || text[i] === '\r')) return i;
    if (formatted && text[i] === '{') {
      if (text[i + 1] === '{') i++;
      else braces++;
    } else if (formatted && text[i] === '}' && text[i + 1] === '}') i++;
    i++;
  }
  return text.length;
}

function pythonIdentifierEnd(text, from) {
  let i = from;
  while (i < text.length) {
    const point = text.codePointAt(i);
    const char = String.fromCodePoint(point);
    if (!(i === from ? PY_START : PY_PART).test(char)) break;
    i += point > 0xffff ? 2 : 1;
  }
  return i;
}

function asciiIdentifierEnd(text, from) {
  let i = from;
  while (i < text.length && ASCII_PART.test(text[i])) i++;
  return i;
}

function decoratorAt(text, at) {
  for (let i = at - 1; i >= 0 && text[i] !== '\n' && text[i] !== '\r'; i--) {
    if (text[i] !== ' ' && text[i] !== '\t' && text[i] !== '\f') return false;
  }
  return true;
}

export function highlight(text, language, options = {}) {
  if (typeof text !== 'string') return {mode: 'plain', language: 'plain', reason: 'Syntax decoration requires source text.', line_count: 0, tokens: []};
  const maxBytes = limit(options?.maxBytes, MAX_HIGHLIGHT_BYTES);
  const maxLines = limit(options?.maxLines, MAX_HIGHLIGHT_LINES);
  const maxTokens = limit(options?.maxTokens, MAX_HIGHLIGHT_TOKENS);
  const {bytes, lineCount} = measure(text, maxBytes);
  const plain = reason => ({mode: 'plain', language: LANGUAGES.has(language) ? language : 'plain', reason, line_count: lineCount, tokens: []});
  if (bytes > maxBytes) return plain(`Syntax decoration byte limit exceeded (${maxBytes} UTF-8 bytes).`);
  if (lineCount > maxLines) return plain(`Syntax decoration line limit exceeded (${maxLines} lines).`);
  if (!LANGUAGES.has(language)) return plain('Syntax decoration is unavailable for this file type.');

  const tokens = [];
  let exceeded = false;
  function token(from, to, kind) {
    if (tokens.length === maxTokens) { exceeded = true; return; }
    tokens.push({from, to, kind});
  }
  let i = 0;
  let vhdlPrevious = null;
  while (i < text.length && !exceeded) {
    const start = i;
    const char = text[i];
    if (language === 'systemverilog') {
      if (char === '/' && text[i + 1] === '/') {
        i = endOfLine(text, i + 2); token(start, i, 'comment'); continue;
      }
      if (char === '/' && text[i + 1] === '*') {
        const end = text.indexOf('*/', i + 2);
        i = end === -1 ? text.length : end + 2; token(start, i, 'comment'); continue;
      }
      if (char === '"') { i = doubleQuoted(text, i); token(start, i, 'string'); continue; }
      if (char === '\\') {
        i++;
        while (i < text.length && !/\s/.test(text[i])) i++;
        continue; // Escaped identifiers can contain keyword/comment characters.
      }
      if (char === '`') {
        i = asciiIdentifierEnd(text, i + 1);
        if (i > start + 1) token(start, i, 'meta');
        continue;
      }
      if (/[0-9']/.test(char)) {
        const number = matchAt(SV_NUMBER, text, i);
        if (number) { i += number.length; token(start, i, 'number'); continue; }
      }
      if (ASCII_START.test(char)) {
        i = asciiIdentifierEnd(text, i);
        const word = text.slice(start, i);
        if (SV_KEYWORDS.has(word)) token(start, i, 'keyword');
        else if (word.startsWith('$')) token(start, i, 'builtin');
        continue;
      }
    } else if (language === 'vhdl') {
      if (char === '-' && text[i + 1] === '-') {
        i = endOfLine(text, i + 2); token(start, i, 'comment'); continue;
      }
      const bitPrefix = /[0-9uUsSbBoOxXdD]/.test(char) && matchAt(VHDL_BIT_STRING, text, i);
      if (bitPrefix) {
        i = doubleQuoted(text, i + bitPrefix.length - 1, true);
        token(start, i, 'number'); vhdlPrevious = 'literal'; continue;
      }
      if (char === '"') { i = doubleQuoted(text, i, true); token(start, i, 'string'); vhdlPrevious = 'literal'; continue; }
      if (char === "'") {
        const point = text.codePointAt(i + 1);
        const width = point > 0xffff ? 2 : 1;
        if (vhdlPrevious !== 'identifier' && vhdlPrevious !== 'character' && point !== undefined && text[i + 1] !== '\n' && text[i + 1] !== '\r' && text[i + 1 + width] === "'") {
          i += width + 2; token(start, i, 'string'); vhdlPrevious = 'character'; continue;
        }
        i = asciiIdentifierEnd(text, i + 1);
        if (i > start + 1) token(start, i, 'meta');
        vhdlPrevious = i > start + 1 ? 'identifier' : 'tick';
        continue;
      }
      if (char === '\\') {
        i++;
        while (i < text.length && text[i] !== '\n' && text[i] !== '\r') {
          if (text[i] === '\\') {
            if (text[i + 1] === '\\') { i += 2; continue; }
            i++;
            break;
          }
          i++;
        }
        vhdlPrevious = 'identifier';
        continue;
      }
      if (/[0-9]/.test(char)) {
        const number = matchAt(VHDL_NUMBER, text, i);
        if (number) { i += number.length; token(start, i, 'number'); vhdlPrevious = 'literal'; continue; }
      }
      if (ASCII_START.test(char)) {
        i = asciiIdentifierEnd(text, i);
        const keyword = VHDL_KEYWORDS.has(text.slice(start, i).toLowerCase());
        if (keyword) token(start, i, 'keyword');
        vhdlPrevious = keyword ? 'keyword' : 'identifier';
        continue;
      }
    } else {
      if (char === '#') { i = endOfLine(text, i + 1); token(start, i, 'comment'); continue; }
      if (char === '"' || char === "'") { i = pythonQuoted(text, i); token(start, i, 'string'); continue; }
      if (char === '@' && decoratorAt(text, i) && pythonIdentifierEnd(text, i + 1) > i + 1) {
        i = pythonIdentifierEnd(text, i + 1);
        while (text[i] === '.' && pythonIdentifierEnd(text, i + 1) > i + 1) i = pythonIdentifierEnd(text, i + 1);
        token(start, i, 'meta'); continue;
      }
      if (/[0-9]/.test(char) || (char === '.' && /[0-9]/.test(text[i + 1] ?? ''))) {
        const number = matchAt(PY_NUMBER, text, i);
        if (number) { i += number.length; token(start, i, 'number'); continue; }
      }
      const identifierEnd = pythonIdentifierEnd(text, i);
      if (identifierEnd > i) {
        i = identifierEnd;
        const word = text.slice(start, i);
        if (PY_PREFIXES.has(word.toLowerCase()) && (text[i] === '"' || text[i] === "'")) {
          i = pythonQuoted(text, i, /[ft]/i.test(word)); token(start, i, 'string'); continue;
        }
        if (PY_KEYWORDS.has(word)) token(start, i, 'keyword');
        else if (PY_BUILTINS.has(word)) token(start, i, 'builtin');
        continue;
      }
    }
    if (OPERATOR.test(char)) token(start, start + 1, 'operator');
    if (language === 'vhdl' && !/\s/.test(char)) vhdlPrevious = 'other';
    // Skip a full code point so even undecorated Unicode is never split.
    i += text.codePointAt(i) > 0xffff ? 2 : 1;
  }
  if (exceeded) return plain(`Syntax decoration token limit exceeded (${maxTokens} tokens).`);
  return {mode: 'highlighted', language, line_count: lineCount, tokens};
}
