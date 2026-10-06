import {
  useRef,
  useState,
  type ComponentProps,
  type MouseEventHandler,
  type ReactNode,
} from 'react';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  Copy,
  Search,
  X,
} from 'lucide-react';
import { cn } from 'cn';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

export function FilterField({
  label,
  value,
  onChange,
  className,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div className={cn('relative w-full sm:w-64', className)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        aria-label={label}
        placeholder={label}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="ps-8 text-ellipsis"
      />
    </div>
  );
}

export function PeopleTable({
  columns,
  header,
  headerClassName,
  bodyRef,
  children,
  ...props
}: ComponentProps<'div'> & {
  columns: string;
  header: ReactNode;
  headerClassName?: string;
  bodyRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    <div
      role="table"
      className={cn(
        'grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] text-sm',
        columns
      )}
      {...props}
    >
      <div
        role="rowgroup"
        className={cn('col-span-full grid grid-cols-subgrid', headerClassName)}
      >
        <PeopleRow aria-rowindex={1}>{header}</PeopleRow>
      </div>
      <div
        ref={bodyRef}
        role="rowgroup"
        className="col-span-full row-start-2 grid auto-rows-max grid-cols-subgrid content-start overflow-y-auto overscroll-contain"
      >
        {children}
      </div>
    </div>
  );
}

export function PeopleRow({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      role="row"
      className={cn(
        'col-span-full grid min-h-11 grid-cols-subgrid items-center border-b',
        className
      )}
      {...props}
    />
  );
}

export function PeopleCell({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div role="cell" className={cn('min-w-0 px-2', className)} {...props} />
  );
}

export function PeopleHead({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      role="columnheader"
      className={cn('flex h-10 items-center px-2 font-medium', className)}
      {...props}
    />
  );
}

export function SortableHead({
  label,
  sorted,
  onSort,
  className,
  children,
}: {
  label: ReactNode;
  sorted: 'asc' | 'desc' | false;
  onSort: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <PeopleHead
      aria-sort={
        sorted === 'asc'
          ? 'ascending'
          : sorted === 'desc'
            ? 'descending'
            : 'none'
      }
      className={cn('gap-2', className)}
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ms-2.5"
        onClick={onSort}
      >
        {label}
        {sorted === 'asc' ? (
          <ArrowUp aria-hidden="true" />
        ) : sorted === 'desc' ? (
          <ArrowDown aria-hidden="true" />
        ) : (
          <ArrowUpDown aria-hidden="true" className="text-muted-foreground" />
        )}
      </Button>
      {children}
    </PeopleHead>
  );
}

export function RowAction({
  action,
  target,
  onClick,
  children,
}: {
  action: string;
  target: string;
  onClick: MouseEventHandler<HTMLButtonElement>;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            aria-label={`${action} ${target}`}
            className={cn(
              buttonVariants({ variant: 'ghost', size: 'icon' }),
              'size-11'
            )}
            onClick={onClick}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{action}</TooltipContent>
    </Tooltip>
  );
}

const emailTailLength = 16;

function breakBeforeDomain(text: string) {
  const at = text.indexOf('@');
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <wbr className="sm:hidden" />
      {text.slice(at)}
    </>
  );
}

export function Email({ email }: { email: string }) {
  return (
    <span
      title={email}
      className="min-w-0 wrap-anywhere whitespace-normal sm:flex sm:whitespace-nowrap"
    >
      <span className="sm:truncate">
        {breakBeforeDomain(email.slice(0, -emailTailLength))}
      </span>
      <span className="sm:shrink-0">
        {breakBeforeDomain(email.slice(-emailTailLength))}
      </span>
    </span>
  );
}

const copyResults = {
  copied: 'Copied',
  failed: "Couldn't copy",
} as const;

/** Reveals on hover or focus within the nearest `group/copy` ancestor. */
export function CopyButton({
  value,
  what,
}: {
  value: string;
  what: 'name' | 'SID' | 'email';
}) {
  const [result, setResult] = useState<keyof typeof copyResults | null>(null);
  const [open, setOpen] = useState(false);
  const dismiss = useRef<ReturnType<typeof setTimeout>>(undefined);
  const label = result ? copyResults[result] : `Copy ${what}`;

  async function copy() {
    let next: keyof typeof copyResults = 'copied';
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      next = 'failed';
    }
    clearTimeout(dismiss.current);
    setResult(next);
    setOpen(true);
    dismiss.current = setTimeout(() => setOpen(false), 1500);
  }

  return (
    <>
      <Tooltip
        open={open}
        onOpenChange={(next, details) => {
          if (!next && details.reason === 'trigger-press') return;
          setOpen(next);
        }}
        onOpenChangeComplete={(nowOpen) => {
          if (!nowOpen) setResult(null);
        }}
      >
        <TooltipTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Copy ${value}`}
              className="-my-1 shrink-0 group-focus-within/copy:opacity-100 group-hover/copy:opacity-100 [@media(hover:hover)]:opacity-0"
              onClick={() => void copy()}
            />
          }
        >
          {result === 'copied' ? (
            <Check aria-hidden="true" />
          ) : result === 'failed' ? (
            <X aria-hidden="true" />
          ) : (
            <Copy aria-hidden="true" />
          )}
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <span role="status" className="sr-only">
        {result ? `${label} ${value}` : ''}
      </span>
    </>
  );
}
