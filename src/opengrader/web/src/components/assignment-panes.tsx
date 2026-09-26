import { useMediaQuery } from '@/hooks/use-media-query';
import { setPreference, usePreferences } from '@/stores/preferences';
import { useGroupRef, usePanelRef } from 'react-resizable-panels';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from './ui/resizable';

export const sidePanelId = 'assignment-side-panel';

const stackedEditorSize = 60;

export function AssignmentPanes({
  editor,
  panel,
  open,
  onOpenChange,
}: {
  editor: ReactNode;
  panel: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // Keep in sync with the editor's min-[65rem] navigation styles.
  const wide = useMediaQuery('(min-width: 65rem)');
  const width = usePreferences((state) => state.editorWidth);
  const groupRef = useGroupRef();
  const panelRef = usePanelRef();
  const pointerResize = useRef(false);
  const [initialOpen] = useState(open);
  const editorSize = wide ? width : stackedEditorSize;

  useEffect(() => {
    const current = panelRef.current;
    if (!current || current.isCollapsed() !== open) return;
    if (open)
      groupRef.current?.setLayout({
        editor: editorSize,
        panel: 100 - editorSize,
      });
    else current.collapse();
  }, [open, editorSize, panelRef, groupRef]);

  return (
    <ResizablePanelGroup
      groupRef={groupRef}
      orientation={wide ? 'horizontal' : 'vertical'}
      defaultLayout={{
        editor: initialOpen ? editorSize : 100,
        panel: initialOpen ? 100 - editorSize : 0,
      }}
      className="min-h-0 flex-1 items-stretch"
      onLayoutChange={(next) => {
        if (
          wide &&
          pointerResize.current &&
          next.editor !== 50 &&
          Math.abs(next.editor - 50) <= 3
        ) {
          groupRef.current?.setLayout({ editor: 50, panel: 50 });
        }
      }}
      onLayoutChanged={(next, { isUserInteraction }) => {
        if (!isUserInteraction || !wide) return;
        const snap = pointerResize.current && Math.abs(next.editor - 50) <= 3;
        pointerResize.current = false;
        const value = snap
          ? { editor: 50, panel: 50 }
          : { editor: next.editor, panel: next.panel };
        if (snap) groupRef.current?.setLayout(value);
        if (value.panel > 0) setPreference('editorWidth', value.editor);
      }}
    >
      <ResizablePanel
        id="editor"
        minSize="25%"
        className={`flex min-w-0 flex-col overflow-hidden! ${open ? (wide ? 'pe-4' : 'pb-4') : ''}`}
      >
        {editor}
      </ResizablePanel>
      <ResizableHandle
        disabled={!open}
        withHandle={open}
        aria-label="Resize editor and side panel"
        className={
          open ? undefined : 'pointer-events-none invisible w-0! after:hidden'
        }
        onPointerDownCapture={() => {
          pointerResize.current = true;
        }}
        onKeyDownCapture={() => {
          pointerResize.current = false;
        }}
      />
      <ResizablePanel
        id="panel"
        panelRef={panelRef}
        minSize="25%"
        collapsible
        onResize={(size) => {
          const collapsed = size.asPercentage === 0;
          if (collapsed === open) onOpenChange(!collapsed);
        }}
        className={`flex min-w-0 flex-col overflow-hidden! ${open ? (wide ? 'ps-4' : 'pt-4') : ''}`}
      >
        <div
          id={sidePanelId}
          hidden={!open}
          className="flex min-h-0 flex-1 flex-col"
        >
          {panel}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
