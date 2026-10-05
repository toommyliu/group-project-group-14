import type { ReactNode } from 'react';
import { cn } from 'cn';

/** Sizes to the wider of both labels so switching to the busy label doesn't shift layout. */
export function SteadyLabel({
  busy,
  idle,
  working,
}: {
  busy: boolean;
  idle: ReactNode;
  working: ReactNode;
}) {
  return (
    <span className="grid justify-items-center">
      <span
        className={cn(
          'col-start-1 row-start-1 inline-flex items-center gap-2',
          busy && 'invisible'
        )}
      >
        {idle}
      </span>
      <span
        className={cn(
          'col-start-1 row-start-1 inline-flex items-center gap-2',
          !busy && 'invisible'
        )}
      >
        {working}
      </span>
    </span>
  );
}
