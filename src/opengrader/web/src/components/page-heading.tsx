import type { ReactNode } from 'react';
import { cn } from 'cn';

export function PageHeading({
  title,
  description,
  className,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  className?: string;
  actions?: ReactNode;
}) {
  return (
    <div
      className={cn(
        'mb-8 flex flex-wrap items-center gap-x-4 gap-y-3',
        className
      )}
    >
      {/* Size to the title so actions share its row until they no longer fit.
          Reserve a control's height so the title doesn't move between pages. */}
      <h1
        className="min-h-11 min-w-0 flex-1 basis-auto truncate py-1 text-3xl leading-9 font-semibold tracking-tight"
        title={typeof title === 'string' ? title : undefined}
      >
        {title}
      </h1>
      {actions && <div className="max-w-full shrink-0">{actions}</div>}
      {description && (
        <div className="min-h-5 basis-full text-muted-foreground">
          {description}
        </div>
      )}
    </div>
  );
}
