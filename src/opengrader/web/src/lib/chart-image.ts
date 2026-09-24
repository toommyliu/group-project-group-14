import { renderChartImage } from '@tanstack/charts/export';

function chartPng(target: HTMLElement) {
  return renderChartImage(target, {
    type: 'image/png',
    scale: 2,
    background: getComputedStyle(target)
      .getPropertyValue('--background')
      .trim(),
  });
}

export async function copyChartImage(target: HTMLElement) {
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
    throw new Error(
      'Image copying is unavailable. Open the image in a new tab instead.'
    );
  }
  // Start the clipboard write during the click; Safari requires user activation.
  await navigator.clipboard.write([
    new ClipboardItem({ 'image/png': chartPng(target) }),
  ]);
}

export async function openChartImage(target: HTMLElement) {
  const preview = window.open('', '_blank');
  if (!preview) throw new Error('Allow pop-ups for this site, then try again.');
  preview.opener = null;
  preview.document.title = 'Preparing chart image';
  preview.document.body.textContent = 'Preparing chart image…';
  try {
    const blob = await chartPng(target);
    const url = URL.createObjectURL(blob);
    preview.location.replace(url);
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    preview.close();
    throw error;
  }
}
