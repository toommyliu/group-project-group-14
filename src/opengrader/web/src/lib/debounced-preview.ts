/** Cancel both the debounce and delivery of in-flight results after an edit. */
export function schedulePreview<T>(
  request: (signal: AbortSignal) => Promise<T>,
  publish: (result: { data: T } | { error: unknown }) => void,
  delay = 300
) {
  const controller = new AbortController();
  let active = true;
  const timer = setTimeout(async () => {
    try {
      const data = await request(controller.signal);
      if (active) publish({ data });
    } catch (error) {
      if (active) publish({ error });
    }
  }, delay);
  return () => {
    active = false;
    clearTimeout(timer);
    controller.abort();
  };
}
