import type { ReactNode } from 'react';
import { cn } from 'cn';

export function PageHeading({
  title,
  description,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('mb-8 space-y-3', className)}>
      <h1
        className="min-h-9 truncate text-3xl leading-9 font-semibold tracking-tight"
        title={typeof title === 'string' ? title : undefined}
      >
        {title}
      </h1>
      {description && (
        <div className="min-h-5 text-muted-foreground">{description}</div>
      )}
    </div>
  );
}
