import { expect, it, vi } from 'vitest';
import { GhosttyTerminalCore } from './core';

vi.mock('./vendor/ghostty-vt.wasm?url', async () => ({
  default: (await import('./vendor/ghostty-vt.wasm?inline')).default,
}));
vi.mock('./vendor/ghostty-write-pty.wasm?url&no-inline', async () => ({
  default: (await import('./vendor/ghostty-write-pty.wasm?inline')).default,
}));

it('preserves split UTF-8 and suppresses PTY replies from restored history', async () => {
  const replies: string[] = [];
  const core = await GhosttyTerminalCore.create(
    80,
    24,
    8,
    16,
    {
      foreground: { r: 255, g: 255, b: 255 },
      background: { r: 0, g: 0, b: 0 },
      cursor: { r: 255, g: 255, b: 255 },
    },
    (data) => replies.push(data)
  );
  try {
    const bytes = new TextEncoder().encode('hello 🌍');
    core.resetAndWrite(bytes.slice(0, -2));
    core.write(bytes.slice(-2));
    const snapshot = core.snapshot();
    expect(snapshot.rowData[0]?.text).toContain('hello 🌍');
    core.resetAndWrite(new TextEncoder().encode('old output\x1b[6n'));
    expect(replies).toEqual([]);
    core.write('\x1b[6n');
    expect(replies.length).toBe(1);
  } finally {
    core.dispose();
  }
});
