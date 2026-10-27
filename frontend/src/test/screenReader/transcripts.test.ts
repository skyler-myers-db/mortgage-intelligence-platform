// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the committed transcripts under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

declare const process: { cwd(): string };

/**
 * The committed virtual screen-reader transcripts (virtualReader.ts) stay in
 * step with the suites that write them. Vitest compares a file snapshot only
 * when a suite asks for it and never reports one no suite asks for, so a
 * renamed or deleted walk would leave a stale golden that
 * docs/accessibility/VPAT.md could still cite. Every `expectTranscript('<name>')`
 * has exactly one `transcripts/<name>.txt` and every transcript has its
 * suite; each is a real walk (named steps, at least one spoken phrase).
 */

const DIR = join(process.cwd(), 'src', 'test', 'screenReader');

function suiteNames(): string[] {
  const names: string[] = [];
  for (const file of (readdirSync(DIR) as string[]).filter((entry) => entry.endsWith('.sr.test.tsx'))) {
    const text = readFileSync(join(DIR, file), 'utf8') as string;
    const found = [...text.matchAll(/expectTranscript\('([a-z-]+)'/g)].map((match) => match[1]);
    expect(found, `${file} records exactly one transcript`).toHaveLength(1);
    names.push(...found);
  }
  return names.sort();
}

function goldenNames(): string[] {
  return (readdirSync(join(DIR, 'transcripts')) as string[])
    .filter((entry) => entry.endsWith('.txt'))
    .map((entry) => entry.slice(0, -'.txt'.length))
    .sort();
}

describe('virtual screen-reader transcripts', () => {
  it('has exactly one golden per suite and one suite per golden', () => {
    const suites = suiteNames();
    expect(new Set(suites).size, 'transcript names are unique').toBe(suites.length);
    expect(goldenNames()).toEqual(suites);
  });

  it('records named steps with spoken phrases in every golden', () => {
    for (const name of goldenNames()) {
      const text = readFileSync(join(DIR, 'transcripts', `${name}.txt`), 'utf8') as string;
      expect(text.startsWith('## '), `${name}.txt opens with a step header`).toBe(true);
      expect(text.endsWith('\n'), `${name}.txt ends with a newline`).toBe(true);
      const phrases = text.split('\n').filter((line) => line !== '' && !line.startsWith('## '));
      expect(phrases.length, `${name}.txt holds spoken phrases`).toBeGreaterThan(0);
    }
  });
});
