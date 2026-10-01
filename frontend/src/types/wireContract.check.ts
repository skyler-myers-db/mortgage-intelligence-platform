/**
 * Wire-contract helper types (audit 2026-09-21 quality-04 / stack-v1,
 * decision D-api-types-a3 P1). Compile-time assertions only: no runtime
 * module imports this file, and only the sibling wireContract.<domain>.check.ts
 * files do. tsc checks every check file through `include: ["src"]`.
 *
 * A response pair is `WireFits<wire, hand>` plus `NoPhantomKeys<hand, wire>`:
 * every body the server can send fits the hand type, and the hand type names
 * no key the wire lacks. A request pair is `WireFits<hand, wire>` plus
 * `DeepNoPhantomKeys<hand, wire>`: every body the client can send fits the
 * schema, and no undeclared key (one the server would drop after it has left
 * the browser) hides in a nested object or array, down to depth 4.
 *
 * A failing element carries a dated, owner-tagged wire-drift expect-error
 * directive on the line above (its exact form is pinned by
 * tests/unit/test_frontend_wire_contract.py), so tsc fails the entry as soon
 * as the drift stops reproducing.
 */

export type Expect<T extends true> = T;

export type WireFits<W, H> = [W] extends [H] ? true : false;

export type Elem<T> = T extends readonly (infer E)[] ? E : T;

/** The object shape of T (arrays and null unwrapped); `unknown`, an open wire value, has none. */
export type Shape<T> = unknown extends T ? never : Extract<Elem<NonNullable<T>>, object>;

export type NoPhantomKeys<H, W> = [Exclude<keyof Shape<H>, keyof Shape<W>>] extends [never] ? true : false;

type DeepLevel = 0 | 1 | 2 | 3 | 4;
type NextLevel = [0, 0, 1, 2, 3];

/** NoPhantomKeys through every common object-valued property (arrays and null unwrapped), depth cap 4. */
export type DeepNoPhantomKeys<H, W, D extends DeepLevel = 4> = [D] extends [0]
  ? true
  : NoPhantomKeys<H, W> extends true
    ? [CommonKeysClean<Shape<H>, Shape<W>, NextLevel[D]>] extends [true]
      ? true
      : false
    : false;

type CommonKeysClean<H, W, D extends DeepLevel> = {
  [K in keyof H & keyof W]: [Shape<H[K]>] extends [never]
    ? true
    : [Shape<W[K]>] extends [never]
      ? true
      : DeepNoPhantomKeys<H[K], W[K], D>;
}[keyof H & keyof W];
