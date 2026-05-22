import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { useOptionalFootprint } from './FootprintProvider';

describe('FootprintProvider fallback contract', () => {
  it('uses generic US-state metadata outside a provider, not a tenant demo footprint', () => {
    function Probe() {
      const footprint = useOptionalFootprint();
      return (
        <span
          data-count={footprint.states.length}
          data-first={footprint.states[0]?.state_code}
          data-has-il={footprint.stateCodes.includes('IL') ? 'yes' : 'no'}
        >
          probe
        </span>
      );
    }

    const html = renderToStaticMarkup(<Probe />);

    expect(html).toContain('data-count="50"');
    expect(html).toContain('data-first="AL"');
    expect(html).toContain('data-has-il="yes"');
  });
});
