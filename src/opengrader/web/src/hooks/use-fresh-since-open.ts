import { useState } from 'react';

/**
 * True once every query has been read again since a sheet was opened.
 *
 * A sheet that edits the answer key file re-reads it on opening, because the
 * terminal app may have changed it. What is cached is shown in the meantime
 * by default — and a form built on it is thrown away the moment the fresh
 * read lands, along with anything typed into it. Waiting for this keeps the
 * form from appearing until it is the one that will stay.
 *
 * `stamps` are the queries' `dataUpdatedAt`.
 */
export function useFreshSinceOpen(open: boolean, stamps: number[]): boolean {
  const [wasOpen, setWasOpen] = useState(false);
  const [baseline, setBaseline] = useState<number[] | null>(null);
  if (open !== wasOpen) {
    setWasOpen(open);
    setBaseline(open ? stamps : null);
  }
  return (
    open &&
    baseline !== null &&
    stamps.every((stamp, index) => stamp !== baseline[index])
  );
}
