import { expect, it } from 'vitest';
import { terminalInputPackets } from './protocol';

it('keeps large Unicode pastes within the frame limit and reconstructs the input exactly', () => {
  const data = '"\\\r\n🌍'.repeat(5000);
  const packets = terminalInputPackets(data);
  expect(packets.length).toBeGreaterThan(1);
  expect(packets.every((packet) => packet.length <= 32768)).toBe(true);
  expect(packets.map((packet) => JSON.parse(packet)[1]).join('')).toBe(data);
});
