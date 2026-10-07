/**
 * The Borrower 360 page title, "Borrower B-…", in every id-bearing branch
 * (warming up, the inline error, the skeleton, loaded, and DossierFailure's
 * calm outage) as the same element at the same position, so a branch change
 * keeps its state and the morph target exists in the first committed frame.
 *
 * deviation:borrower-id-morph (lib/borrowerMorph): when a Lead Queue row link
 * just marked this id, the id span carries the shared view-transition-name
 * for the route's View Transition, then lets it go after --dur-base plus a
 * frame margin, so no stale or duplicate name survives into a later
 * transition. The name is set inline only; app.transitions.css carries none.
 */
import { useEffect, useState } from 'react';
import { borrowerMorphNameFor, clearBorrowerMorph } from '../lib/borrowerMorph';

/** --dur-base (the token, read at runtime) plus a frame margin. */
function releaseMs(): number {
  const raw = window.getComputedStyle(document.documentElement).getPropertyValue('--dur-base').trim();
  const amount = Number.parseFloat(raw);
  return (Number.isFinite(amount) ? (raw.endsWith('ms') ? amount : amount * 1000) : 0) + 100;
}

export function BorrowerTitle({ id }: { id: string }) {
  const [name, setName] = useState(() => borrowerMorphNameFor(id));
  useEffect(() => {
    if (!name) return undefined;
    const release = window.setTimeout(() => {
      setName(undefined);
      clearBorrowerMorph();
    }, releaseMs());
    return () => window.clearTimeout(release);
  }, [name]);
  return (
    <>
      Borrower{' '}
      <span className="page-title__id" style={name ? { viewTransitionName: name } : undefined}>
        {id}
      </span>
    </>
  );
}
