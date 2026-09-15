import { useWideAssignmentLayout } from '@/hooks/use-wide-assignment-layout';
import { usePreference } from '@/hooks/use-preference';
import { useGroupRef } from 'react-resizable-panels';
import { useRef, type ReactNode } from 'react';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './ui/resizable';

export function AssignmentPanes({
  editor,
  preview,
}: {
  editor: ReactNode;
  preview: ReactNode;
}) {
  const wide = useWideAssignmentLayout();
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

  // The editor toolbar sticks to the page viewport, so its panel wrappers must not scroll.
  return (
    <ResizablePanelGroup
      groupRef={groupRef}
      orientation="horizontal"
      defaultLayout={{ editor: width, preview: 100 - width }}
      className="items-stretch overflow-visible!"
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
      <ResizablePanel
        id="editor"
        minSize="25%"
        className="min-w-0 overflow-visible! pe-4"
      >
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
