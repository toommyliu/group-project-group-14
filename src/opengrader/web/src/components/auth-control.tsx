import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LogIn, LogOut } from 'lucide-react';
import { authQuery, logout } from '@/api/auth';
import { Button } from './ui/button';

export function AuthControl({ compact = false }: { compact?: boolean }) {
  const queryClient = useQueryClient();
  const { data } = useQuery(authQuery);
  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['auth'] }),
  });

  if (!data?.enabled) return null;
  if (!data.user) {
    return (
      <Button
        variant="outline"
        size={compact ? 'icon-lg' : 'default'}
        render={<a href="/auth/login" />}
        aria-label="Sign in with Google"
      >
        <LogIn aria-hidden="true" />
        {!compact && <span>Sign in</span>}
      </Button>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      {!compact && (
        <span
          className="max-w-48 truncate text-sm text-muted-foreground"
          title={data.user.email}
        >
          {data.user.email}
        </span>
      )}
      <Button
        variant="ghost"
        size={compact ? 'icon-lg' : 'default'}
        disabled={signOut.isPending}
        onClick={() => signOut.mutate()}
        aria-label={`Sign out ${data.user.email}`}
        title={compact ? `Sign out ${data.user.email}` : undefined}
      >
        <LogOut aria-hidden="true" />
        {!compact && (
          <span>{signOut.isPending ? 'Signing out...' : 'Sign out'}</span>
        )}
      </Button>
    </div>
  );
}
