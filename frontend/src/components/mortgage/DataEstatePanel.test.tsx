/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataEstateResponse } from '../../types';
import { DataEstatePanel } from './DataEstatePanel';

const appMocks = vi.hoisted(() => ({
  setDrawer: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    setDrawer: appMocks.setDrawer,
  }),
}));

function estate(): DataEstateResponse {
  return {
    generated_at: '2026-06-15T00:00:00Z',
    lender_name: 'Summit Mortgage',
    public_demo_masking: true,
    known_data_gaps: [],
    proof_assets: ['mip.gold.lead_population'],
    lanes: [
      {
        id: 'databricks',
        title: 'Databricks governance layer',
        description: 'Unity Catalog and Lakebase governed assets.',
        status: 'live',
        assets: [
          {
            name: 'UC Gold Lead Population',
            label: 'Ranked lead queue table',
            status: 'live',
            uc_object: 'mip.gold.lead_population',
            catalog_explorer_url:
              'https://dbc-test.cloud.databricks.com/explore/data/mip/gold/lead_population',
            row_count: 1234,
            last_updated: '2026-06-15T00:00:00Z',
            note: 'Gold lead population is current.',
            synthetic_demo: false,
          },
        ],
      },
    ],
  };
}

describe('DataEstatePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    appMocks.setDrawer.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('shows an immediate Catalog Explorer action for governed Unity Catalog assets', () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <DataEstatePanel estate={estate()} />
        </MemoryRouter>,
      );
    });

    const catalogLink = container.querySelector<HTMLAnchorElement>(
      'a[aria-label="Open Ranked lead queue table in Catalog Explorer"]',
    );

    expect(catalogLink).toBeTruthy();
    expect(catalogLink?.getAttribute('href')).toBe(
      'https://dbc-test.cloud.databricks.com/explore/data/mip/gold/lead_population',
    );
    expect(catalogLink?.textContent).toContain('Catalog Explorer');
  });

  it('uses DOM-safe IDs for expanded asset details', () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <DataEstatePanel estate={estate()} />
        </MemoryRouter>,
      );
    });

    const expandButton = container.querySelector<HTMLButtonElement>('.data-estate__asset');
    expect(expandButton).toBeTruthy();
    act(() => expandButton!.click());

    const controls = expandButton!.getAttribute('aria-controls');
    expect(controls).toBeTruthy();
    expect(controls).not.toMatch(/\s/);
    expect(container.querySelector(`#${controls}`)).toBeTruthy();
  });

  it('renders an unavailable readiness source as "readiness unavailable" with a warn tone', () => {
    const base = estate();
    const lane = base.lanes[0]!;
    const unavailableEstate: DataEstateResponse = {
      ...base,
      lanes: [
        {
          ...lane,
          status: 'unavailable',
          assets: [
            lane.assets[0]!,
            {
              name: 'MLS Listings',
              label: 'Listing activity',
              status: 'unavailable',
              uc_object: null,
              catalog_explorer_url: null,
              row_count: null,
              last_updated: null,
              note: 'readiness summary has no row for this source',
              synthetic_demo: false,
            },
          ],
        },
      ],
    };
    act(() => {
      root.render(
        <MemoryRouter>
          <DataEstatePanel estate={unavailableEstate} />
        </MemoryRouter>,
      );
    });

    const assets = [...container.querySelectorAll<HTMLButtonElement>('.data-estate__asset')];
    const listing = assets.find((button) => button.textContent?.includes('Listing activity'));
    expect(listing?.querySelector('.data-estate__asset-meta')?.textContent).toContain('readiness unavailable');
    expect(listing?.querySelector('.status-dot')?.classList.contains('status-dot--warn')).toBe(true);

    const proof = container.querySelector('.data-estate__lane-proof');
    expect(proof?.classList.contains('chip--warning')).toBe(true);
    expect(proof?.textContent).toBe('1 live · 1 readiness unavailable');
  });
});
