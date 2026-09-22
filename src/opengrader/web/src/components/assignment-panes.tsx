import { useWideAssignmentLayout } from '@/hooks/use-wide-assignment-layout';
import { usePreference } from '@/hooks/use-preference';
import { useGroupRef, usePanelRef } from 'react-resizable-panels';
import { useRef, useState, type ReactNode } from 'react';
import { Button } from './ui/button';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './ui/resizable';

export function AssignmentPanes({
  renderEditor,
  preview,
}: {
  renderEditor: (previewToggle: ReactNode) => ReactNode;
  preview: ReactNode;
}) {
  const wide = useWideAssignmentLayout();
  const [width, setWidth] = usePreference('editorWidth');
  const groupRef = useGroupRef();
  const previewRef = usePanelRef();
  const pointerResize = useRef(false);
  const [collapsed, setCollapsed] = useState(true);
  const togglePreview = () => {
    if (wide) {
      if (collapsed) previewRef.current?.expand();
      else previewRef.current?.collapse();
    }
    setCollapsed(!collapsed);
  };
  const toggle = (
    <Button
      type="button"
      variant="outline"
      aria-expanded={!collapsed}
      aria-controls="assignment-pdf-preview"
      onClick={togglePreview}
    >
      {collapsed ? 'Show PDF' : 'Hide PDF'}
    </Button>
  );

  if (!wide)
    return (
      <div className="grid gap-8">
        {renderEditor(toggle)}
        <div id="assignment-pdf-preview" hidden={collapsed}>
          {preview}
        </div>
      </div>
    );

  // The editor toolbar sticks to the page viewport, so its panel wrappers must not scroll.
  return (
    <ResizablePanelGroup
      groupRef={groupRef}
      orientation="horizontal"
      defaultLayout={{
        editor: collapsed ? 100 : width,
        preview: collapsed ? 0 : 100 - width,
      }}
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
        if (value.preview > 0) setWidth(value.editor);
      }}
    >
      <ResizablePanel
        id="editor"
        minSize="25%"
        className="min-w-0 overflow-visible! pe-4"
      >
        {renderEditor(toggle)}
      </ResizablePanel>
      <ResizableHandle
        withHandle={!collapsed}
        aria-label="Resize QA file and preview"
        className={collapsed ? 'pointer-events-none invisible' : undefined}
        onPointerDownCapture={() => {
          pointerResize.current = true;
        }}
        onKeyDownCapture={() => {
          pointerResize.current = false;
        }}
      />
      <ResizablePanel
        id="preview"
        panelRef={previewRef}
        minSize="25%"
        collapsible
        onResize={(size) => setCollapsed(size.asPercentage === 0)}
        className="min-w-0 ps-4"
      >
        <div id="assignment-pdf-preview" hidden={collapsed}>
          {preview}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
