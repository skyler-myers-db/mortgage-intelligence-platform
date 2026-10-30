/**
 * A Cotality share table keeps one asset key on every install (M3-PR2).
 *
 * The API names a share table where this install mounts it (`fqn`,
 * `asset_path`) and adds its canonical name as `source_key`. The asset map is
 * written in canonical names, so a lineage node resolves through
 * `source_key`; a canonical install, whose `source_key` equals its `fqn`,
 * resolves exactly as it did before the field existed.
 */
import { describe, expect, it } from 'vitest';
import { ASSET_KEYS_BY_SOURCE, assetKeyForLineageNode, assetKeyForSource } from './drawerSources';

const CANONICAL_PROPERTY_DOMAIN = 'cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3';
const CONFIGURED_PROPERTY_DOMAIN = 'acme_share.mortgage.property_domain_v3';

describe('assetKeyForLineageNode', () => {
  it('resolves a configured share table through its canonical source_key', () => {
    expect(assetKeyForSource(CONFIGURED_PROPERTY_DOMAIN)).toBeNull();
    expect(
      assetKeyForLineageNode({ fqn: CONFIGURED_PROPERTY_DOMAIN, source_key: CANONICAL_PROPERTY_DOMAIN }),
    ).toBe('entrada_eval_property_domain_v3');
    expect(
      assetKeyForLineageNode({ asset_path: CONFIGURED_PROPERTY_DOMAIN, source_key: CANONICAL_PROPERTY_DOMAIN }),
    ).toBe('entrada_eval_property_domain_v3');
  });

  it('never maps a configured location that carries no source_key', () => {
    expect(assetKeyForLineageNode({ fqn: CONFIGURED_PROPERTY_DOMAIN })).toBeNull();
    expect(assetKeyForLineageNode({ asset_path: CONFIGURED_PROPERTY_DOMAIN, source_key: null })).toBeNull();
  });

  it('keys a canonical install exactly as its fqn did', () => {
    for (const [fqn, assetKey] of Object.entries(ASSET_KEYS_BY_SOURCE)) {
      const sourceKey = fqn.startsWith('cotality_mortgage_data.') ? fqn : null;
      expect(assetKeyForLineageNode({ fqn, source_key: sourceKey }), fqn).toBe(assetKeyForSource(fqn));
      expect(assetKeyForLineageNode({ asset_path: fqn, source_key: sourceKey }), fqn).toBe(assetKey);
    }
  });
});
