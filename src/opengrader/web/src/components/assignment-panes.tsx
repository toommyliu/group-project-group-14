import { usePreference } from '@/hooks/use-preference';
import { useGroupRef } from 'react-resizable-panels';
import { useRef, useSyncExternalStore, type ReactNode } from 'react';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './ui/resizable';

const wideQuery = '(min-width: 65rem)';

function subscribe(callback: () => void) {
  const query = window.matchMedia(wideQuery);
  query.addEventListener('change', callback);
  return () => query.removeEventListener('change', callback);
}

export function AssignmentPanes({
  editor,
  preview,
}: {
  editor: ReactNode;
  preview: ReactNode;
}) {
  const wide = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(wideQuery).matches
  );
  const [width, setWidth] = usePreference('editorWidth');
  const groupRef = useGroupRef();
  const pointerResize = useRef(false);

  if (!wide)
    return (
      <div className="grid gap-8">
        {editor}
        {preview}
      </div>
    );

  return (
    <ResizablePanelGroup
      groupRef={groupRef}
      orientation="horizontal"
      defaultLayout={{ editor: width, preview: 100 - width }}
      className="items-stretch"
      onLayoutChange={(next) => {
        if (
          pointerResize.current &&
          next.editor !== 50 &&
          Math.abs(next.editor - 50) <= 3
        ) {
          groupRef.current?.setLayout({ editor: 50, preview: 50 });
        }
      }}
      onLayoutChanged={(next, { isUserInteraction }) => {
        if (!isUserInteraction) return;
        const snap = pointerResize.current && Math.abs(next.editor - 50) <= 3;
        pointerResize.current = false;
        const value = snap
          ? { editor: 50, preview: 50 }
          : { editor: next.editor, preview: next.preview };
        if (snap) groupRef.current?.setLayout(value);
        setWidth(value.editor);
      }}
    >
      <ResizablePanel id="editor" minSize="25%" className="min-w-0 pe-4">
        {editor}
      </ResizablePanel>
      <ResizableHandle
        withHandle
        aria-label="Resize QA file and preview"
        onPointerDownCapture={() => {
          pointerResize.current = true;
        }}
        onKeyDownCapture={() => {
          pointerResize.current = false;
        }}
      />
      <ResizablePanel id="preview" minSize="25%" className="min-w-0 ps-4">
        {preview}
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
