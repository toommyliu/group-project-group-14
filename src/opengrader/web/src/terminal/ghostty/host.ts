/** Browser helpers replacing T3 Code's application-specific imports. */
export const SELECTION_MULTI_CLICK_INTERVAL_MS = 500;

export function isMacPlatform(platform: string): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}

export function isMonospaceFamily(family: string): boolean {
  const context = document.createElement('canvas').getContext('2d');
  if (!context) return true;
  context.font = `32px ${family}, monospace`;
  const advances = ['i', 'W', '0', ' '].map(
    (text) => context.measureText(text).width
  );
  return advances.every((width) => Math.abs(width - advances[0]!) < 0.1);
}
