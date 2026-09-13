import type { ReactNode } from 'react';

export function PageHeading({
  title,
  description,
}: {
  title: ReactNode;
  description: ReactNode;
}) {
  return (
    <div className="mb-8 space-y-3">
      <h1 className="min-h-9 text-3xl leading-9 font-semibold tracking-tight text-balance break-words">
        {title}
      </h1>
      <div className="min-h-5 text-muted-foreground">{description}</div>
    </div>
  );
}
