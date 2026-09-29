import { cn } from '@/lib/utils';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

export function VersionSelect({
  id,
  label,
  versions,
  value,
  emptyLabel,
  disabled,
  stacked = false,
  onChange,
}: {
  id: string;
  label: string;
  versions: string[];
  value: string;
  emptyLabel: string;
  disabled: boolean;
  /** Put the label above a full-width menu, for narrow columns. */
  stacked?: boolean;
  onChange: (value: string | null) => void;
}) {
  const items = [
    { value: '', label: emptyLabel },
    ...versions.map((version) => ({
      value: version,
      label: `Version ${version}`,
    })),
  ];
  return (
    <div
      className={cn(
        'min-w-0',
        stacked ? 'grid gap-1.5' : 'flex items-center gap-2'
      )}
    >
      <Label
        htmlFor={id}
        className={cn(
          'shrink-0 text-muted-foreground',
          // Inline, match the text-sm control labels beside it so baselines line up.
          !stacked && 'text-sm leading-normal'
        )}
      >
        {label}
      </Label>
      <Select
        items={items}
        value={value}
        disabled={disabled}
        onValueChange={(next) => {
          if (next !== null) onChange(next || null);
        }}
      >
        <SelectTrigger
          id={id}
          size="sm"
          className={cn('min-w-0', stacked && 'w-full')}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="start" alignItemWithTrigger={false}>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
