export type AuthUser = {
  id: string;
  email: string;
  name: string;
};

export type AuthStatus = {
  enabled: boolean;
  user: AuthUser | null;
};

export const authQuery = {
  queryKey: ['auth'],
  queryFn: getAuthStatus,
};

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch('/auth/me');
  if (!response.ok) throw new Error('Could not check sign-in status.');
  return response.json() as Promise<AuthStatus>;
}

export async function logout(): Promise<void> {
  const response = await fetch('/auth/logout', { method: 'POST' });
  if (!response.ok) throw new Error('Could not sign out.');
}
