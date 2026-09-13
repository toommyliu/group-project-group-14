export function errorMessage(
  error: unknown,
  fallback = 'Request failed. Try again.'
) {
  return error instanceof Error ? error.message : fallback;
}
