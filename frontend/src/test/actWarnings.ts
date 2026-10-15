/**
 * Fail a suite on React's "not wrapped in act(...)" warning (audit
 * quality-06, W5e).
 *
 * The warning is only a console.error, so a suite that regresses keeps
 * passing while it prints. `watchActWarnings()` spies on console.error and
 * CALLS THROUGH (nothing is silenced; the warning still prints). The function
 * it returns removes the spy and returns the component named by each act
 * warning seen since the watch began. Start the watch in a beforeEach; in the
 * afterEach drain in-flight work (settleInAct) and unmount first, then stop
 * it and assert the list is empty, so an update that lands after the test
 * body still counts. Test-only: production code never imports src/test.
 */
import { vi } from 'vitest';

const ACT_WARNING = 'not wrapped in act(';

/** Watch console.error; the returned stop() restores it and lists the act warnings seen. */
export function watchActWarnings(): () => string[] {
  const spy = vi.spyOn(console, 'error');
  return () => {
    // Read before mockRestore(), which clears the recorded calls.
    const components = spy.mock.calls
      .filter(([format]) => typeof format === 'string' && format.includes(ACT_WARNING))
      .map(([, component]) => String(component));
    spy.mockRestore();
    return components;
  };
}
