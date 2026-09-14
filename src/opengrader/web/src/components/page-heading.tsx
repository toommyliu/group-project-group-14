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
        '@container mb-8 flex flex-wrap items-center gap-x-4 gap-y-3',
        className
      )}
    >
      <h1
        className="min-h-9 min-w-0 flex-1 basis-full truncate text-3xl leading-9 font-semibold tracking-tight @min-[36rem]:basis-0"
        title={typeof title === 'string' ? title : undefined}
      >
        {title}
      </h1>
      {actions && (
        <div className="max-w-full shrink-0 @min-[36rem]:ms-auto">
          {actions}
        </div>
      )}
      {description && (
        <div className="min-h-5 basis-full text-muted-foreground">
          {description}
        </div>
      )}
    </div>
  );
}
