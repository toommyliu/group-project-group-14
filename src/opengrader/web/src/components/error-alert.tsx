import type { ReactNode } from 'react';
import { CircleAlert } from 'lucide-react';
import { errorMessage } from '@/lib/error-message';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';

export function ErrorAlert({
  error,
  title,
  fallback,
  className,
  children,
}: {
  error: unknown;
  title?: string;
  fallback?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <Alert variant="destructive" className={className}>
      <CircleAlert aria-hidden="true" />
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className="min-w-0 break-words whitespace-pre-wrap">
        {errorMessage(error, fallback)}
        {children && (
          <div className="mt-2 flex flex-col items-start gap-2">{children}</div>
        )}
      </AlertDescription>
    </Alert>
  );
}
