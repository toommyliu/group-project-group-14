import { useMediaQuery } from '@/hooks/use-media-query';
import { setPreference, usePreferences } from '@/stores/preferences';
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
  initiallyExpanded = false,
  contained = false,
}: {
  renderEditor: (previewToggle: ReactNode) => ReactNode;
  preview: ReactNode;
  initiallyExpanded?: boolean;
  contained?: boolean;
}) {
  // Keep in sync with the editor's min-[65rem] navigation styles.
  const wide = useMediaQuery('(min-width: 65rem)');
  const width = usePreferences((state) => state.editorWidth);
  const groupRef = useGroupRef();
  const previewRef = usePanelRef();
  const pointerResize = useRef(false);
  const [collapsed, setCollapsed] = useState(!initiallyExpanded);
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
      <div
        className={
          contained
            ? collapsed
              ? 'grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] overflow-hidden'
              : 'grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,35svh)] gap-4 overflow-hidden'
            : 'grid gap-8'
        }
      >
        {renderEditor(toggle)}
        <div
          id="assignment-pdf-preview"
          hidden={collapsed}
          className={contained ? 'min-h-0 overflow-y-auto' : undefined}
        >
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
      className={`items-stretch overflow-visible! ${contained ? 'min-h-0 flex-1' : ''}`}
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
        if (value.preview > 0) setPreference('editorWidth', value.editor);
      }}
    >
      <ResizablePanel
        id="editor"
        minSize="25%"
        className={`min-w-0 overflow-visible! ${collapsed ? '' : 'pe-4'}`}
      >
        {renderEditor(toggle)}
      </ResizablePanel>
      <ResizableHandle
        withHandle={!collapsed}
        aria-label="Resize QA file and preview"
        className={
          collapsed
            ? 'pointer-events-none invisible w-0! after:hidden'
            : undefined
        }
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
