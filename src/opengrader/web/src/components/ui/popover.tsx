import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { cn } from 'cn';
import { usePortalContainer } from '@/lib/portal-container';

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverTitle = PopoverPrimitive.Title;

export function PopoverContent({
  className,
  align = 'end',
  side,
  sideOffset = 8,
  anchor,
  collisionAvoidance,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    'align' | 'side' | 'sideOffset' | 'anchor' | 'collisionAvoidance'
  >) {
  return (
    <PopoverPrimitive.Portal container={usePortalContainer()}>
      <PopoverPrimitive.Positioner
        align={align}
        side={side}
        sideOffset={sideOffset}
        anchor={anchor}
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
