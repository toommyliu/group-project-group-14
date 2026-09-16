export function errorMessage(
  error: unknown,
  fallback = 'An unexpected error occurred.'
) {
  const message = error instanceof Error ? error.message : error;
  return typeof message === 'string' && message.trim() ? message : fallback;
}
