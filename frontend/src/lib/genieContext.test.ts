/**
 * @vitest-environment happy-dom
 *
 * Genie page-context templates (audit 2026-09-21 `genie-04`, phase 1).
 *
 * The contract under test: every template is written in REVIEWED vocabulary
 * -- words the shipped starter set (`genie/sample_questions.md`) and the
 * glossary already use -- and the only interpolated values are enum values a
 * registry the app already carries (segment names, federal state names by
 * USPS code). Place names are a documented guard false-positive source, so
 * free text must never reach a template; the lead template in particular
 * must never carry the masked borrower id or a contact field.
 *
 * The sentences live in `genieContextTemplates.json`; the Python twin
 * (`tests/unit/test_genie_context_templates.py`) runs every rendering through
 * the real deterministic prompt guards.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; the
// starter set is read from the repo under Vitest only (see test/designCss.ts).
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

declare const process: { cwd(): string };
import statesTopology from 'us-atlas/states-albers-10m.json';
import { FIPS_TO_USCODE } from '../components/mortgage/USChoroplethMap.utils';
import {
  GENIE_STARTERS,
  US_STATE_NAME_BY_CODE,
  genieContextFromQueueFilters,
  genieKpiPrompt,
  genieLeadPrompt,
  genieSegmentPrompt,
  genieStartersForRoute,
  genieStatePrompt,
  stateNameForCode,
} from './genieContext';
import templates from './genieContextTemplates.json';
import { glossaryEntries } from './mortgageGlossary';
import { ROUTE_META } from './routeMeta';
import { SEGMENT_DEFINITIONS } from './segmentMetadata';
import { USPS_STATE_CODES } from './uspsStates';

function tokens(text: string): string[] {
  return text.toLowerCase().match(/[a-z0-9]+(?:['’-][a-z0-9]+)*/g) ?? [];
}

/** The 30 bold prompts (plus the related state-planning prompt) of the starter set. */
function starterCorpus(): string[] {
  // Vitest runs from `frontend/`; the starter set lives at the repo root.
  const markdown: string = readFileSync(join(process.cwd(), '..', 'genie', 'sample_questions.md'), 'utf8');
  return [...markdown.matchAll(/\*\*(.+?)\*\*/g)].map((match) => match[1]);
}

const CORPUS_TOKENS = new Set<string>([
  ...starterCorpus().flatMap(tokens),
  ...glossaryEntries.flatMap((entry) =>
    [entry.term, ...entry.aliases, entry.short, entry.appContext, entry.proof].flatMap(tokens),
  ),
]);
const SEGMENT_NAME_TOKENS = new Set(SEGMENT_DEFINITIONS.flatMap((segment) => tokens(segment.name)));
const STATE_NAME_TOKENS = new Set(Object.values(US_STATE_NAME_BY_CODE).flatMap(tokens));

function unreviewedWords(prompt: string): string[] {
  return tokens(prompt).filter(
    (word) => !CORPUS_TOKENS.has(word) && !SEGMENT_NAME_TOKENS.has(word) && !STATE_NAME_TOKENS.has(word),
  );
}

const STATE_CODES = [...USPS_STATE_CODES];
const SEGMENT_CODES: string[] = SEGMENT_DEFINITIONS.map((segment) => segment.code);
const TEMPLATED_SEGMENT_CODES = SEGMENT_CODES.filter(
  (code) => !Object.prototype.hasOwnProperty.call(templates.segmentsWithoutTemplates, code),
);

describe('reviewed vocabulary', () => {
  it('the corpus is real: it carries the starter set and glossary words', () => {
    expect(starterCorpus().length).toBeGreaterThanOrEqual(30);
    for (const word of ['in-the-money', 'cotality', 'coverage', 'equity', 'heloc', 'segment']) {
      expect(CORPUS_TOKENS.has(word), word).toBe(true);
    }
    // A word the product never uses is NOT in the corpus, so the check bites.
    expect(CORPUS_TOKENS.has('zyrplax')).toBe(false);
    expect(unreviewedWords('borrowers with zyrplax')).toEqual(['zyrplax']);
  });

  it('every per-route starter is a verbatim starter-set prompt', () => {
    const starters = new Set(starterCorpus());
    const routesWithStarters: string[] = [];
    for (const meta of ROUTE_META) {
      const prompts = genieStartersForRoute(meta.pattern.replace(':id', 'B-0123456789ABC').replace(':assetKey', 'k'));
      if (prompts.length > 0) routesWithStarters.push(meta.pattern);
      for (const prompt of prompts) expect(starters.has(prompt), prompt).toBe(true);
    }
    for (const pattern of ['/', '/portfolio-builder', '/segment-intelligence', '/lead-queue', '/analytics']) {
      expect(routesWithStarters).toContain(pattern);
    }
    // The deep-dive route keeps the server's own list.
    expect(genieStartersForRoute('/ask-genie')).toEqual([]);
    expect(genieStartersForRoute('/this-route-does-not-exist')).toEqual([]);
  });

  it('every route key is a real route pattern and every starter key resolves', () => {
    const patterns = new Set(ROUTE_META.map((meta) => meta.pattern));
    for (const [pattern, keys] of Object.entries(templates.routeStarters)) {
      expect(patterns.has(pattern), pattern).toBe(true);
      for (const key of keys) expect(GENIE_STARTERS[key], `${pattern}: ${key}`).toBeTruthy();
    }
  });

  it('per-route starters differ between pages (they replace the identical global set)', () => {
    expect(genieStartersForRoute('/')).not.toEqual(genieStartersForRoute('/lead-queue'));
    expect(genieStartersForRoute('/segment-intelligence')).not.toEqual(genieStartersForRoute('/analytics'));
    for (const prompt of Object.values(GENIE_STARTERS)) expect(unreviewedWords(prompt)).toEqual([]);
  });

  it('every KPI prompt is reviewed vocabulary and unknown labels get none', () => {
    for (const label of [
      'Addressable population',
      'Addressable Borrowers',
      'Marketable population',
      'In the Money',
      'Refi economics screen',
      'Refi Economics',
      'Opportunity score 75+',
      'Primary offer paths',
      'Primary Offer Paths',
      'Avg. borrower score',
      'Approved Outreach',
    ]) {
      const prompt = genieKpiPrompt(label);
      expect(prompt, label).not.toBeNull();
      expect(unreviewedWords(prompt ?? ''), `${label}: ${prompt}`).toEqual([]);
    }
    expect(genieKpiPrompt('Some free-text label')).toBeNull();
    expect(genieKpiPrompt('')).toBeNull();
  });

  it('segment prompts interpolate only registered segment names', () => {
    for (const code of TEMPLATED_SEGMENT_CODES) {
      const prompt = genieSegmentPrompt(code);
      expect(prompt, code).not.toBeNull();
      expect(unreviewedWords(prompt ?? ''), prompt ?? code).toEqual([]);
    }
    expect(genieSegmentPrompt('made_up')).toBeNull();
    expect(genieSegmentPrompt('')).toBeNull();
  });

  it('a segment the guards refuse outright gets no prompt, alone or on a lead', () => {
    // Registered codes only: an exclusion for a code the registry does not
    // carry would be dead config.
    for (const code of Object.keys(templates.segmentsWithoutTemplates)) {
      expect(SEGMENT_CODES, code).toContain(code);
      expect(genieSegmentPrompt(code), code).toBeNull();
    }
    expect(genieSegmentPrompt('permit_activity')).toBeNull();
    // A lead whose first segment has no templates falls through to the next.
    expect(genieLeadPrompt({ segmentCodes: ['permit_activity', 'equity'], stateCode: 'TX' })).toBe(
      'How many borrowers in Texas are in the home equity candidate segment, and what is the average rate spread?',
    );
    expect(genieLeadPrompt({ segmentCodes: ['permit_activity'], stateCode: 'TX' })).toBe(genieStatePrompt('TX'));
  });

  it('state prompts interpolate only the federal name for a USPS code', () => {
    for (const code of STATE_CODES) {
      const prompt = genieStatePrompt(code);
      expect(prompt, code).not.toBeNull();
      expect(prompt).toContain(US_STATE_NAME_BY_CODE[code]);
      expect(unreviewedWords(prompt ?? ''), prompt ?? code).toEqual([]);
    }
    expect(genieStatePrompt('tx')).toBe(genieStatePrompt('TX'));
    expect(genieStatePrompt('ZZ')).toBeNull();
    expect(genieStatePrompt('Texas')).toBeNull();
    expect(genieStatePrompt(null)).toBeNull();
    expect(stateNameForCode('PR')).toBeNull();
  });

  it('lead prompts carry segment and state only: never an id or a contact field', () => {
    const borrowerId = 'B-0123456789ABC';
    for (const stateCode of STATE_CODES) {
      for (const segmentCode of TEMPLATED_SEGMENT_CODES) {
        const prompt = genieLeadPrompt({ segmentCodes: [segmentCode], stateCode });
        expect(prompt, `${stateCode}/${segmentCode}`).not.toBeNull();
        expect(unreviewedWords(prompt ?? ''), prompt ?? '').toEqual([]);
        expect(prompt).not.toContain(borrowerId);
        expect(prompt).not.toMatch(/B-[0-9A-Z]{13}/);
      }
    }
    // The first REGISTERED segment names the prompt; unknown codes are skipped.
    expect(genieLeadPrompt({ segmentCodes: ['made_up', 'itm'], stateCode: 'IL' })).toBe(
      'How many in-the-money borrowers are in Illinois, and what is the average rate spread?',
    );
    // No registered segment: the state template stands in.
    expect(genieLeadPrompt({ segmentCodes: [], stateCode: 'IL' })).toBe(genieStatePrompt('IL'));
    // No state the registry vouches for: nothing to ask.
    expect(genieLeadPrompt({ segmentCodes: ['itm'], stateCode: '' })).toBeNull();
    expect(genieLeadPrompt({ segmentCodes: ['itm'], stateCode: 'Springfield' })).toBeNull();
  });
});

describe('state registry parity', () => {
  it('names every USPS code and matches the map registry (us-atlas) name for name', () => {
    expect(Object.keys(US_STATE_NAME_BY_CODE).sort()).toEqual(STATE_CODES.sort());
    const topology = statesTopology as unknown as {
      objects: { states: { geometries: Array<{ id: string; properties: { name: string } }> } };
    };
    const atlasNames = new Map<string, string>();
    for (const geometry of topology.objects.states.geometries) {
      const code = FIPS_TO_USCODE[geometry.id];
      if (code) atlasNames.set(code.toUpperCase(), geometry.properties.name);
    }
    expect(atlasNames.size).toBe(STATE_CODES.length);
    for (const [code, name] of atlasNames) expect(US_STATE_NAME_BY_CODE[code], code).toBe(name);
  });
});

describe('genieContextFromQueueFilters (phase 2, a closed grammar)', () => {
  const build = (query: string) => genieContextFromQueueFilters(new URLSearchParams(query));

  it.each([
    ['an all-mode intersection', 'segment_codes=itm,equity&segment_mode=all', { route: 'lead-queue', segment_codes: ['itm', 'equity'], segment_mode: 'all' }],
    ['an any-mode pair in a state', 'states=OK&segment_codes=listed,permit', { route: 'lead-queue', segment_codes: ['listed', 'permit'], segment_mode: 'any', state: 'OK' }],
    ['a county', 'county=17031', { route: 'lead-queue', county_fips: '17031' }],
    ['a county list of one, with its state and a segment', 'state=il&counties=17031&segment=ITM', { route: 'lead-queue', segment_codes: ['itm'], segment_mode: 'any', state: 'IL', county_fips: '17031' }],
    ['a ZIP', 'zip=60601', { route: 'lead-queue', zip: '60601' }],
    ['a ZIP list of one', 'zips=99950&segment=equity', { route: 'lead-queue', segment_codes: ['equity'], segment_mode: 'any', zip: '99950' }],
    ['the map\'s ZIP drill: a state beside its ZIP, both carried', 'state=IL&zip=60601', { route: 'lead-queue', state: 'IL', zip: '60601' }],
    ['a state beside a ZIP elsewhere, both carried (never dropped)', 'states=TX&zips=60601&segment_codes=itm,equity&segment_mode=all', { route: 'lead-queue', segment_codes: ['itm', 'equity'], segment_mode: 'all', state: 'TX', zip: '60601' }],
    ['a governed city key', 'cities=ALISO VIEJO~CA', { route: 'lead-queue', city: 'ALISO VIEJO~CA' }],
    ['a city in its own state', 'state=WA&cities=KENT~WA', { route: 'lead-queue', state: 'WA', city: 'KENT~WA' }],
    ['a Competitor ref', 'state=IL&target_lender_ref=Competitor B', { route: 'lead-queue', state: 'IL', lender_ref: 'Competitor B' }],
    ['Competitor Other alone', 'target_lender_ref=Competitor Other', { route: 'lead-queue', lender_ref: 'Competitor Other' }],
    ['the assignee preset', 'state=TX&segment=itm&assigned_to=me', { route: 'lead-queue', segment_codes: ['itm'], segment_mode: 'any', state: 'TX', assignee: 'me' }],
  ])('accepts %s', (_label, query, expected) => {
    expect(build(query)).toEqual(expected);
  });

  it.each([
    ['no filter', ''],
    ['one state (phase 1 owns it)', 'state=TX'],
    ['one segment (phase 1 owns it)', 'segment=itm'],
    ['one state and one segment (phase 1 owns it)', 'state=TX&segment_codes=itm&segment_mode=all'],
    ['the assignee alone', 'assigned_to=me'],
    ['an assignee email', 'state=TX&segment_codes=itm,equity&assigned_to=lo@summit.example'],
    ['two assignee values', 'state=TX&assigned_to=me,someone'],
    ['borrower ids', 'segment_codes=itm,equity&borrower_ids=B-0123456789ABC'],
    ['a cohort', 'segment_codes=itm,equity&cohort_id=c-1'],
    ['an approval status', 'segment_codes=itm,equity&approval_status=pending'],
    ['an outreach status', 'county=17031&outreach_status=drafted'],
    ['a funnel stage', 'county=17031&funnel_stage=approved'],
    ['aged days', 'county=17031&aged_days=7'],
    ['a portfolio criterion', 'county=17031&occupancy=owner'],
    ['a score bound', 'county=17031&min_opportunity_score=70'],
    ['a display param that leaked through', 'county=17031&sort=equity'],
    ['three segments', 'segment_codes=itm,equity,listed&segment_mode=all'],
    ['an unknown segment', 'segment_codes=itm,not_a_segment'],
    ['a segment without templates', 'segment_codes=itm,permit_activity'],
    ['two states', 'states=TX,IL&segment_codes=itm,equity'],
    ['a state outside the federal list', 'state=PR&segment_codes=itm,equity'],
    ['two counties', 'counties=17031,17043'],
    ['a county name', 'county=Cook'],
    ['two ZIPs', 'zips=60601,60602'],
    ['a ZIP+4', 'zip=60601-1234'],
    ['two cities', 'cities=KENT~WA,MEDINA~OH'],
    ['a lower-case city', 'cities=kent~WA'],
    ['a five-word city', 'cities=A B C D E~WA'],
    ['a city outside USPS', 'cities=SAN JUAN~PR'],
    ['a city outside its state', 'state=TX&cities=KENT~WA'],
    ['a county and a ZIP', 'county=17031&zip=60601'],
    ['a ZIP and a city', 'zip=98032&cities=KENT~WA'],
    ['a tenant lender ref', 'state=IL&target_lender_ref=Summit Mortgage'],
    ['two lender refs', 'target_lender_ref=Competitor A,Competitor B'],
    ['an unknown segment mode', 'segment_codes=itm,equity&segment_mode=none'],
  ])('is null for %s', (_label, query) => {
    expect(build(query)).toBeNull();
  });

  it('is null wherever phase 1 already has a reviewed prompt', () => {
    for (const state of STATE_CODES) {
      expect(build(`state=${state}`)).toBeNull();
      for (const code of SEGMENT_CODES) expect(build(`state=${state}&segment=${code}`)).toBeNull();
    }
  });

  it('never carries a value outside the closed codes', () => {
    const context = build('state=IL&county=17031&segment_codes=itm,equity&segment_mode=all&target_lender_ref=Competitor Z&assigned_to=me');
    expect(context).not.toBeNull();
    expect(JSON.stringify(context)).not.toMatch(/@|B-[0-9A-Z]{13}/);
    expect(Object.keys(context ?? {}).sort()).toEqual(['assignee', 'county_fips', 'lender_ref', 'route', 'segment_codes', 'segment_mode', 'state']);
  });
});
