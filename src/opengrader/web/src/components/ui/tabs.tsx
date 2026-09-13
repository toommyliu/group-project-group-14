import { Tabs as TabsPrimitive } from '@base-ui/react/tabs';
import { cn } from 'cn';

export function Tabs(props: TabsPrimitive.Root.Props) {
  return <TabsPrimitive.Root {...props} />;
}

export function TabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      className={cn('flex flex-wrap gap-1 border-b', className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      className={cn(
        'min-h-11 border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground data-active:border-foreground data-active:text-foreground',
        className
      )}
      {...props}
    />
  );
}

export function TabsContent({
  className,
  ...props
}: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel className={cn('min-w-0 pt-4', className)} {...props} />
  );
}
