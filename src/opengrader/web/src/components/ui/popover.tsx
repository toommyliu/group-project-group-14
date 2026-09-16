import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { cn } from 'cn';

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverTitle = PopoverPrimitive.Title;

export function PopoverContent({
  className,
  align = 'end',
  sideOffset = 8,
  collisionAvoidance,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    'align' | 'sideOffset' | 'collisionAvoidance'
  >) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        align={align}
        sideOffset={sideOffset}
        collisionAvoidance={collisionAvoidance}
        className="z-50"
      >
        <PopoverPrimitive.Popup
          className={cn(
            'max-h-(--available-height) w-80 max-w-[calc(100vw-2rem)] overflow-auto bg-popover p-4 text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-none',
            className
          )}
          {...props}
        />
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  );
}
