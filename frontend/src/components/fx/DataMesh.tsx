import { useMemo } from 'react';

/**
 * DataMesh — subtle drifting particle field. 36 tiny dots (1–2px) distributed
 * via a seeded pseudo-random generator so the pattern is stable across renders.
 * Three color variants (teal / bright / accent) cycle through the dots; each
 * has its own animation duration via `.data-mesh__dot--a|b|c` so the whole
 * field breathes asynchronously without requiring per-dot inline delays.
 *
 * Absolute-fill, pointer-events: none, z-index 0. Expected to live inside the
 * app's `.main` region which establishes the stacking context.
 */

const DOT_COUNT = 36;

// Deterministic PRNG so the layout doesn't jitter between renders or routes.
function mulberry32(seed: number) {
  let t = seed;
  return () => {
    t |= 0;
    t = (t + 0x6D2B79F5) | 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

interface Dot {
  cx: number; // viewBox 0..100
  cy: number;
  r: number;  // 0.6 .. 1.2
  variant: 'a' | 'b' | 'c';
  delay: number; // seconds
}

function useDots(): Dot[] {
  return useMemo(() => {
    const rand = mulberry32(0xC07A71); // 'COTA71' — static seed
    const variants: Array<'a' | 'b' | 'c'> = ['a', 'b', 'c'];
    return Array.from({ length: DOT_COUNT }, (_, i): Dot => ({
      cx: rand() * 100,
      cy: rand() * 100,
      r: 0.6 + rand() * 0.6,
      variant: variants[i % 3],
      delay: Math.round(rand() * 60 * 10) / 10,
    }));
  }, []);
}

export function DataMesh() {
  const dots = useDots();
  return (
    <div className="data-mesh" aria-hidden="true">
      <svg
        className="data-mesh__svg"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {dots.map((d, i) => (
          <circle
            key={i}
            className={`data-mesh__dot data-mesh__dot--${d.variant}`}
            cx={d.cx}
            cy={d.cy}
            r={d.r}
            style={{ animationDelay: `-${d.delay}s` }}
          />
        ))}
      </svg>
    </div>
  );
}
