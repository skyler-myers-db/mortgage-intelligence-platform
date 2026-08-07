/**
 * @vitest-environment happy-dom
 *
 * Linkage surfaces on the Genie answer:
 *   - the trailing "Source: <catalog.schema.table>" disclosure becomes a
 *     Catalog Explorer link when the workspace host is known, and stays
 *     plain text when it is not
 *   - table cells for borrower_id / state drill into the same routes the
 *     drill-down cards and the geography map use; city stays plain
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer: vi.fn() }) }));
vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { genieFeedback: vi.fn().mockResolvedValue({ accepted: true }) } };
});

const workspaceHost = vi.hoisted(() => ({ value: null as string | null }));
vi.mock('../HealthProvider', () => ({ useWorkspaceHost: () => workspaceHost.value }));

import { GenieAnswer } from './GenieAnswer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const HOST = 'https://dbc-test.cloud.databricks.com';
const BORROWER = 'B-7K2M9QX4TB3PZ';

function payload(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'The average loan age is 5.25 years. Source: mip.gold.borrower_360.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'conv-1',
    message_id: 'msg-1',
    genie_status: 'COMPLETED',
    ...overrides,
  } as unknown as GenieAnswerShape;
}

describe('GenieAnswer UC + route linkage', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    workspaceHost.value = HOST;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const mount = (p: GenieAnswerShape) => {
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieAnswer payload={p} />
        </MemoryRouter>,
      );
    });
  };

  it('links the trailing Source disclosure to Catalog Explorer in a new tab', () => {
    mount(payload());
    const link = container.querySelector('a.uc-asset-link') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link?.getAttribute('href')).toBe(`${HOST}/explore/data/mip/gold/borrower_360`);
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.getAttribute('rel')).toContain('noopener');
    expect(link?.textContent).toBe('mip.gold.borrower_360');
  });

  it('keeps the "Source:" label as text and does not swallow the sentence period', () => {
    mount(payload());
    expect(container.textContent).toContain('Source: mip.gold.borrower_360');
    const link = container.querySelector('a.uc-asset-link');
    expect(link?.textContent?.endsWith('.')).toBe(false);
  });

  it('degrades to plain text when the workspace host is unknown', () => {
    workspaceHost.value = null;
    mount(payload());
    expect(container.querySelector('a.uc-asset-link')).toBeNull();
    expect(container.textContent).toContain('Source: mip.gold.borrower_360');
  });

  it('links borrower_id and state table cells, leaving city plain', () => {
    mount(
      payload({
        answer: 'Top borrowers.',
        table_rows: [{ borrower_id: BORROWER, state: 'IL', city: 'Chicago' }],
      } as Partial<GenieAnswerShape>),
    );
    const hrefs = Array.from(container.querySelectorAll('.genie-answer__table a')).map((a) =>
      a.getAttribute('href'),
    );
    expect(hrefs).toContain(`/borrower-360/${BORROWER}`);
    expect(hrefs).toContain('/lead-queue?state=IL');
    expect(hrefs.some((h) => h?.includes('Chicago'))).toBe(false);
  });

  it('leaves a borrower_id that is not a masked id as plain text', () => {
    mount(
      payload({
        answer: 'Rows.',
        table_rows: [{ borrower_id: 'not-a-masked-id', state: 'IL' }],
      } as Partial<GenieAnswerShape>),
    );
    const hrefs = Array.from(container.querySelectorAll('.genie-answer__table a')).map((a) =>
      a.getAttribute('href'),
    );
    expect(hrefs).toEqual(['/lead-queue?state=IL']);
  });
});
