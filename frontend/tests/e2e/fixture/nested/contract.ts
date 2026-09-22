/**
 * Values shared between the nested probe (failing.nested.ts) and the outer
 * runner self-test (../runner.fixture.spec.ts). Kept in a module with no
 * `test()` calls so the outer spec can import it without registering the
 * nested test in its own run.
 */
export const NESTED_FAILURE_MESSAGE = 'this nested test must fail';
export const NESTED_TEST_TITLE = 'fails on purpose after rendering a page and attaching a large file';
/**
 * Over 64 KiB once stored: the entry size that stalled Playwright's built-in
 * trace merge on Node 26. The probe fills it with random bytes so deflate
 * cannot shrink it under the threshold.
 */
export const LARGE_ATTACHMENT_BYTES = 100_000;
