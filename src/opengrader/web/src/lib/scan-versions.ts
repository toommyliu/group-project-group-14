export const DEFAULT_VERSION_LABEL = 'Default version';
export const DETECT_VERSION_LABEL = 'Choose a version';

/** Label for a submission that follows the scan's default version. */
export function inheritedVersionLabel(defaultVersion: string) {
  return `Default (${defaultVersion ? `Version ${defaultVersion}` : 'none chosen'})`;
}
