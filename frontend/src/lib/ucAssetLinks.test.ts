import { describe, expect, it } from 'vitest';
import { SOURCE_LINE_RE, catalogExplorerUrl, normalizeWorkspaceHost, parseUcAsset } from './ucAssetLinks';

const HOST = 'https://dbc-test.cloud.databricks.com';

describe('parseUcAsset', () => {
  it('splits a 3-part dotted UC name', () => {
    expect(parseUcAsset('mip.gold.borrower_360')).toEqual({
      catalog: 'mip',
      schema: 'gold',
      table: 'borrower_360',
    });
  });

  it('tolerates surrounding whitespace', () => {
    expect(parseUcAsset('  mip.gold.lead_population ')?.table).toBe('lead_population');
  });

  it.each([
    ['two-part name', 'gold.borrower_360'],
    ['four-part name', 'a.b.c.d'],
    ['wildcard', 'mip.gold.*'],
    ['prose', 'the gold table'],
    ['empty', ''],
    ['nullish', null],
  ])('returns null for %s', (_label, value) => {
    expect(parseUcAsset(value as string | null)).toBeNull();
  });
});

describe('normalizeWorkspaceHost', () => {
  it('accepts a bare workspace hostname and adds https', () => {
    expect(normalizeWorkspaceHost('dbc-test.cloud.databricks.com')).toBe(HOST);
  });

  it('accepts a full https origin and strips a trailing slash', () => {
    expect(normalizeWorkspaceHost(`${HOST}/`)).toBe(HOST);
  });

  it('accepts azure and gcp workspace suffixes', () => {
    expect(normalizeWorkspaceHost('adb-123.4.azuredatabricks.net')).toBe(
      'https://adb-123.4.azuredatabricks.net',
    );
    expect(normalizeWorkspaceHost('x.gcp.databricks.com')).toBe('https://x.gcp.databricks.com');
  });

  it.each([
    ['http scheme', 'http://dbc-test.cloud.databricks.com'],
    ['non-databricks host', 'https://evil.example.com'],
    ['embedded credentials', 'https://user:pw@dbc-test.cloud.databricks.com'],
    ['path segment', 'https://dbc-test.cloud.databricks.com/redirect'],
    ['query string', 'https://dbc-test.cloud.databricks.com?next=x'],
    ['whitespace', 'dbc test.cloud.databricks.com'],
    ['empty', ''],
    ['nullish', null],
  ])('rejects %s', (_label, value) => {
    expect(normalizeWorkspaceHost(value as string | null)).toBeNull();
  });
});

describe('catalogExplorerUrl', () => {
  it('builds the Catalog Explorer path from a dotted asset', () => {
    expect(catalogExplorerUrl(HOST, 'mip.gold.borrower_360')).toBe(
      `${HOST}/explore/data/mip/gold/borrower_360`,
    );
  });

  it('returns null when the workspace host is unknown — callers degrade to plain text', () => {
    expect(catalogExplorerUrl(null, 'mip.gold.borrower_360')).toBeNull();
    expect(catalogExplorerUrl(undefined, 'mip.gold.borrower_360')).toBeNull();
  });

  it('returns null for a non-UC asset string', () => {
    expect(catalogExplorerUrl(HOST, 'lakebase.approvals')).toBeNull();
  });
});

describe('SOURCE_LINE_RE', () => {
  it('captures the label and the asset from a trailing source disclosure', () => {
    const re = new RegExp(SOURCE_LINE_RE.source, SOURCE_LINE_RE.flags);
    const match = re.exec('12,480 borrowers. Source: mip.gold.borrower_360.');
    expect(match?.[1]).toBe('Source: ');
    expect(match?.[2]).toBe('mip.gold.borrower_360');
  });

  it('does not swallow the sentence-ending period into the asset', () => {
    const re = new RegExp(SOURCE_LINE_RE.source, SOURCE_LINE_RE.flags);
    const match = re.exec('Source: mip.gold.lead_population.');
    expect(match?.[2]).toBe('mip.gold.lead_population');
  });

  it('ignores a Source line whose value is not a 3-part UC name', () => {
    const re = new RegExp(SOURCE_LINE_RE.source, SOURCE_LINE_RE.flags);
    expect(re.exec('Source: internal notes')).toBeNull();
  });
});
