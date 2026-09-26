import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LogIn, LogOut } from 'lucide-react';

import { authQuery, logout } from '@/api/auth';
import { Button } from './ui/button';
import { Avatar, AvatarFallback } from './ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

export function AuthControl({
  compact = false,
  variant = 'inline',
}: {
  compact?: boolean;
  variant?: 'inline' | 'sidebar';
}) {
  const queryClient = useQueryClient();
  const { data } = useQuery(authQuery);
  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: authQuery.queryKey }),
  });

  if (!data?.enabled) return null;

  const sidebar = variant === 'sidebar';

  const buttonProps = sidebar
    ? {
        size: 'default' as const,
        className: `justify-start px-4 ${compact ? 'w-12' : 'w-full'}`,
      }
    : {
        size: compact ? ('icon-lg' as const) : ('default' as const),
      };

  const label = (text: string) =>
    sidebar ? (
      <span className={compact ? 'sr-only' : undefined}>{text}</span>
    ) : (
      !compact && <span>{text}</span>
    );

  if (!data.user) {
    return (
      <Button
        variant="outline"
        {...buttonProps}
        render={<a href="/auth/login" />}
        aria-label="Sign in with Google"
      >
        <LogIn aria-hidden="true" />
        {label('Sign in')}
      </Button>
    );
  }

  const { email } = data.user;

  const name =
    data.user.name.trim() && data.user.name !== email
      ? data.user.name.trim()
      : 'Signed in';

  if (sidebar) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              className="h-11 w-full justify-start gap-2.5 border-0 ps-3 pe-2 text-start text-sidebar-foreground group-data-[collapsible=icon]:w-12 hover:bg-sidebar-accent aria-expanded:bg-sidebar-accent"
            />
          }
          aria-label={`Account: ${name}`}
          title={compact ? name : undefined}
        >
          <Avatar size="sm" aria-hidden="true">
            <AvatarFallback className="text-xs font-medium">
              {Array.from(
                name === 'Signed in' ? email : name
              )[0]?.toUpperCase()}
            </AvatarFallback>
          </Avatar>

          {!compact && (
            <span className="grid min-w-0 flex-1 gap-0.5 leading-tight">
              <span className="truncate text-sm font-medium">{name}</span>

              <span className="truncate text-xs text-muted-foreground">
                {email}
              </span>
            </span>
          )}
        </DropdownMenuTrigger>

        <DropdownMenuContent
          side={compact ? 'right' : 'top'}
          align={compact ? 'end' : 'start'}
          sideOffset={8}
          className={compact ? 'w-56' : undefined}
        >
          <DropdownMenuGroup>
            <DropdownMenuLabel className="grid gap-0.5 select-text">
              <span className="text-sm font-medium wrap-break-word text-popover-foreground">
                {name}
              </span>
              <span className="wrap-anywhere">{email}</span>
            </DropdownMenuLabel>
          </DropdownMenuGroup>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            disabled={signOut.isPending}
            closeOnClick={false}
            onClick={() => signOut.mutate()}
            className="min-h-9 text-sm"
          >
            <LogOut aria-hidden="true" />
            {signOut.isPending ? 'Signing out...' : 'Sign out'}
          </DropdownMenuItem>

          {signOut.isError && (
            <p role="alert" className="px-2 py-2 text-xs text-destructive">
              Could not sign out. Try again.
            </p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      {!compact && (
        <span
          className="max-w-48 truncate text-sm text-muted-foreground"
          title={email}
        >
          {email}
        </span>
      )}

      <Button
        variant="ghost"
        {...buttonProps}
        disabled={signOut.isPending}
        onClick={() => signOut.mutate()}
        aria-label="Sign out"
        title={compact ? 'Sign out' : undefined}
      >
        <LogOut aria-hidden="true" />
        {label(signOut.isPending ? 'Signing out...' : 'Sign out')}
      </Button>
    </div>
  );
}
