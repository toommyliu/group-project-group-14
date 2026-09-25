import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LogIn, LogOut } from 'lucide-react';
import { authQuery, logout } from '@/api/auth';
import { Button } from './ui/button';

export function AuthControl({
  compact = false,
  variant = 'inline',
}: {
  compact?: boolean;
  /** The sidebar pins the icon in place so collapsing never moves it. */
  variant?: 'inline' | 'sidebar';
}) {
  const queryClient = useQueryClient();
  const { data } = useQuery(authQuery);
  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['auth'] }),
  });

  if (!data?.enabled) return null;
  const sidebar = variant === 'sidebar';
  const buttonProps = sidebar
    ? {
        size: 'default' as const,
        className: `justify-start ps-2 ${compact ? 'w-11 pe-0' : ''}`,
      }
    : { size: compact ? ('icon-lg' as const) : ('default' as const) };
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

  const email = !compact && (
    <span
      className="max-w-48 truncate text-sm text-muted-foreground"
      title={data.user.email}
    >
      {data.user.email}
    </span>
  );
  return (
    <div
      className={
        sidebar
          ? 'flex min-w-0 flex-col items-start gap-1'
          : 'flex min-w-0 items-center gap-2'
      }
    >
      {email && sidebar ? <span className="ps-2">{email}</span> : email}
      <Button
        variant="ghost"
        {...buttonProps}
        disabled={signOut.isPending}
        onClick={() => signOut.mutate()}
        aria-label={`Sign out ${data.user.email}`}
        title={compact ? `Sign out ${data.user.email}` : undefined}
      >
        <LogOut aria-hidden="true" />
        {label(signOut.isPending ? 'Signing out...' : 'Sign out')}
      </Button>
    </div>
  );
}
