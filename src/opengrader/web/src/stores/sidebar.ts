import { create } from 'zustand';
import { setPreference, usePreferences } from './preferences';

const useResizePreview = create<{ width: number | null }>(() => ({
  width: null,
}));

export function useSidebarWidth() {
  const preview = useResizePreview((state) => state.width);
  const saved = usePreferences((state) => state.sidebarWidth);
  return preview ?? saved;
}

export function previewSidebarWidth(width: number | null) {
  useResizePreview.setState({ width });
}

export function saveSidebarWidth(width: number) {
  setPreference('sidebarWidth', width);
  useResizePreview.setState({ width: null });
}
