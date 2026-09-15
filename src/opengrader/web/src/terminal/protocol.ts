export function terminalInputPackets(data: string): string[] {
  // Keep pastes within the server's frame limit without splitting surrogate pairs.
  return (data.match(/[\s\S]{1,4096}/gu) ?? []).map((part) =>
    JSON.stringify(['stdin', part])
  );
}
