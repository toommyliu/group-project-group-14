import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

export type FilterOption<T extends string> = {
  value: T;
  label: string;
  count: number;
};

export function FilterTabs<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: FilterOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <Tabs
      value={value}
      onValueChange={(next: T) => onChange(next)}
      className="max-w-full min-w-0 @max-[34rem]:w-full"
    >
      <TabsList
        aria-label={label}
        className="max-w-full @max-[34rem]:grid @max-[34rem]:h-auto! @max-[34rem]:w-full @max-[34rem]:grid-cols-2"
      >
        {options.map((option) => (
          <TabsTrigger
            key={option.value}
            value={option.value}
            className="px-2.5 text-sm @max-[34rem]:h-9"
          >
            {option.label}
            <span className="text-muted-foreground tabular-nums">
              {option.count}
            </span>
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
