import { errorMessage } from '@/lib/error-message';
import { useEffect, useRef, useState } from 'react';
import { terminalInputPackets } from '@/terminal/protocol';
import { GhosttyTerminalSurface } from '@/terminal/ghostty/surface';

/** A connection can disappear without ending the server-owned process. */
export function RemoteTerminal({ id }: { id: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState('Connecting…');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const mount = container.current;
    if (!mount) return;
    let terminal: GhosttyTerminalSurface | undefined;
    let socket: WebSocket | undefined;
    let disposed = false;
    let replaying = true;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    function send(packet: unknown[]) {
      if (socket?.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify(packet));
    }
    function resize(cols: number, rows: number) {
      send([
        'resize',
        {
          width: Math.max(2, Math.min(cols, 500)),
          height: Math.max(2, Math.min(rows, 300)),
        },
      ]);
    }
    const focus = () => send(['focus']);
    const blur = () => send(['blur']);
    mount.addEventListener('focusin', focus);
    mount.addEventListener('focusout', blur);
    function connect() {
      replaying = true;
      const url = new URL(`/api/terminals/${id}/ws`, window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const connection = new WebSocket(url);
      socket = connection;
      connection.binaryType = 'arraybuffer';
      connection.onmessage = (event: MessageEvent<unknown>) => {
        if (disposed || !terminal) return;
        if (event.data instanceof ArrayBuffer) {
          terminal.write(new Uint8Array(event.data));
          return;
        }
        if (typeof event.data !== 'string') return;
        const packet: unknown = JSON.parse(event.data);
        if (
          packet &&
          typeof packet === 'object' &&
          'type' in packet &&
          packet.type === 'snapshot' &&
          'data' in packet &&
          typeof packet.data === 'string'
        ) {
          const history = Uint8Array.from(atob(packet.data), (char) =>
            char.charCodeAt(0)
          );
          terminal.resetAndWrite(history);
          replaying = false;
          failures = 0;
          resize(terminal.cols, terminal.rows);
          if (mount?.contains(document.activeElement)) focus();
          setMessage('Connected');
        }
      };
      connection.onclose = (event) => {
        if (disposed) return;
        replaying = true;
        if ([4404, 4409, 1008].includes(event.code)) {
          setMessage(event.reason || 'This session is unavailable.');
          return;
        }
        setMessage('Disconnected. Reconnecting…');
        reconnectTimer = setTimeout(
          connect,
          Math.min(1000 * 2 ** failures++, 10000)
        );
      };
    }
    GhosttyTerminalSurface.create(mount, {
      theme: {
        background: { r: 18, g: 18, b: 18 },
        foreground: { r: 238, g: 238, b: 238 },
        cursor: { r: 238, g: 238, b: 238 },
      },
      font: { size: 14 },
      onData: (data) => {
        if (!replaying && socket?.readyState === WebSocket.OPEN) {
          for (const packet of terminalInputPackets(data)) socket.send(packet);
        }
      },
      onResize: resize,
      onSelectionChange: () => {},
      beforeKey: (event) => {
        if (event.key === 'Escape' && event.shiftKey) {
          event.preventDefault();
          mount.previousElementSibling?.querySelector('button')?.focus();
          return false;
        }
        return true;
      },
      onLinkActivate: (text) => {
        if (/^https?:\/\//i.test(text))
          window.open(text, '_blank', 'noopener,noreferrer');
      },
    })
      .then((surface) => {
        if (disposed) {
          surface.dispose();
          return;
        }
        terminal = surface;
        connect();
      })
      .catch((error: unknown) => {
        if (!disposed)
          setMessage(errorMessage(error, 'Could not load the terminal.'));
      });
    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      mount.removeEventListener('focusin', focus);
      mount.removeEventListener('focusout', blur);
      socket?.close();
      terminal?.dispose();
    };
  }, [id, attempt]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#121212] text-[#eeeeee]">
      <div className="flex items-center justify-between gap-3 border-b border-white/20 px-3 py-2 text-xs">
        <span role="status">{message}</span>
        <span className="hidden text-white/70 sm:inline">
          Shift+Esc to leave terminal
        </span>
        <button
          className="min-h-8 px-2 underline focus-visible:outline-2"
          onClick={() => setAttempt((value) => value + 1)}
        >
          Reconnect
        </button>
      </div>
      <div
        ref={container}
        className="relative min-h-0 flex-1 overflow-hidden [--app-scrollbar-thumb-hover:#aaa] [--app-scrollbar-thumb:#777] [--app-scrollbar-width:10px] focus-within:outline-2 focus-within:-outline-offset-2 focus-within:outline-white"
        aria-label="Remote terminal"
      />
    </div>
  );
}
