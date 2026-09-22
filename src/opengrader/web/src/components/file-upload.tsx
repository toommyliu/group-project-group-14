import { useRef, useState, type ReactNode } from 'react';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ErrorAlert } from '@/components/error-alert';
import { cn } from 'cn';

export function FileUpload({
  title,
  description,
  buttonLabel,
  inputLabel,
  accept,
  multiple = false,
  disabled = false,
  error,
  onFiles,
  children,
}: {
  title: string;
  description: string;
  buttonLabel: string;
  inputLabel: string;
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  error?: string;
  onFiles: (files: File[]) => void;
  children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <div
      className={cn('min-w-0 rounded-lg border border-dashed p-4', {
        'bg-muted': dragging && !disabled,
        'border-solid border-destructive': !!error,
      })}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (!disabled) onFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="space-y-1">
        <h2 className="text-sm font-medium">{title}</h2>
        {error ? (
          <ErrorAlert error={error} className="border-0 bg-transparent p-0" />
        ) : (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      <Button
        type="button"
        className="mt-3 w-full"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        <Upload aria-hidden="true" />
        {buttonLabel}
      </Button>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        className="hidden"
        aria-label={inputLabel}
        onChange={(event) => {
          onFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      {children}
    </div>
  );
}
