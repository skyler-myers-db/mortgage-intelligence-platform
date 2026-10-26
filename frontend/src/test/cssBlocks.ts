/**
 * A minimal CSS statement reader for source guards (cssLayers.test.ts): the
 * top-level statements of a sheet, each a `prelude { body }` block or a
 * `prelude;` statement, with comments removed and strings respected. It
 * does not validate CSS; it only finds where blocks start and end.
 */

export interface CssStatement {
  /** Text before `{` (a block) or before `;` (a statement), trimmed. */
  prelude: string;
  /** The block's inner text, or undefined for a `;` statement. */
  body?: string;
}

/** The text with every comment replaced by a space (strings kept). */
export function stripCssComments(css: string): string {
  let out = '';
  let quote = '';
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (quote) {
      out += ch;
      if (ch === '\\') {
        out += css[i + 1] ?? '';
        i += 1;
      } else if (ch === quote) quote = '';
    } else if (ch === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2);
      i = end === -1 ? css.length : end + 1;
      out += ' ';
    } else {
      if (ch === '"' || ch === "'") quote = ch;
      out += ch;
    }
  }
  return out;
}

/** Top-level statements of comment-free CSS text, in source order. */
export function cssStatements(text: string): CssStatement[] {
  const statements: CssStatement[] = [];
  let start = 0;
  let quote = '';
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === '\\') i += 1;
      else if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === ';') {
      statements.push({ prelude: text.slice(start, i).trim() });
      start = i + 1;
    } else if (ch === '{') {
      let depth = 1;
      let j = i + 1;
      let inner = '';
      for (; j < text.length && depth > 0; j += 1) {
        const c = text[j];
        if (inner) {
          if (c === '\\') j += 1;
          else if (c === inner) inner = '';
        } else if (c === '"' || c === "'") inner = c;
        else if (c === '{') depth += 1;
        else if (c === '}') depth -= 1;
      }
      if (depth !== 0) throw new Error(`unbalanced block after: ${text.slice(start, i).trim().slice(0, 80)}`);
      statements.push({ prelude: text.slice(start, i).trim(), body: text.slice(i + 1, j - 1) });
      start = j;
      i = j - 1;
    } else if (ch === '}') {
      throw new Error(`stray } at offset ${i}`);
    }
  }
  if (text.slice(start).trim()) throw new Error(`unterminated statement: ${text.slice(start).trim().slice(0, 80)}`);
  return statements.filter((statement) => statement.prelude !== '' || statement.body !== undefined);
}

/** Every statement at any depth, depth-first, with the chain of enclosing preludes. */
export function walkCss(text: string, parents: string[] = []): Array<CssStatement & { parents: string[] }> {
  const out: Array<CssStatement & { parents: string[] }> = [];
  for (const statement of cssStatements(text)) {
    out.push({ ...statement, parents });
    if (statement.body !== undefined && statement.prelude.startsWith('@')) {
      out.push(...walkCss(statement.body, [...parents, statement.prelude]));
    }
  }
  return out;
}
