import type { ScanBatch, ScanPage } from '@/api/scans';

export const DEFAULT_VERSION_LABEL = 'Default version';
export const DETECT_VERSION_LABEL = 'Choose a version';

/** Label for a submission that follows the scan's default version. */
export function inheritedVersionLabel(defaultVersion: string) {
  return `Default (${defaultVersion ? `Version ${defaultVersion}` : 'none chosen'})`;
}

export function matchedVersion(
  pages: ScanPage[],
  scores: ScanBatch['version_scores']
): string | null {
  const totals = new Map<string, number>();
  for (const page of pages)
    for (const [version, score] of Object.entries(scores[page.number] ?? {}))
      totals.set(version, (totals.get(version) ?? 0) + score);
  const [best, second] = [...totals].sort((a, b) => b[1] - a[1]);
  if (!best || best[1] < 25 || best[1] < 1.4 * (second?.[1] ?? 0)) return null;
  return best[0];
}
