import { useActiveCell } from "@/lib/use-active-cell";
import { useStore } from "@/lib/store";
import { SplatScene } from "./SplatScene";
import { ViserSplatScene } from "./ViserSplatScene";
import { ViserToggle } from "./ViserToggle";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { FpsIndicator } from "./FpsIndicator";
import { PlaybackDriver } from "./PlaybackDriver";
import { PlaybackBar } from "./PlaybackBar";

export function Viewport() {
  const { activeCell, isSequence } = useActiveCell();
  const hasContent = !!activeCell;
  const viserEnabled = useStore((s) => s.viserEnabled);
  const inBrowserRenderer = useStore((s) => s.inBrowserRenderer);
  const useSplatScene = inBrowserRenderer && isSequence;

  return (
    <div className="h-full w-full relative bg-canvas">
      {/* Viser runs headless behind the iframe and is driven by
          ViserSplatScene's control-API POSTs. Sequence picker and
          PlaybackBar feed (cell, frame) into the same `viser_headless.py`
          process; viser owns rendering, React owns everything else.
          Gated by viserEnabled so the user can unmount the iframe when
          the splat renderer crashes or NaN's mid-session. */}
      {useSplatScene ? (
        <SplatScene />
      ) : viserEnabled ? (
        <ViserSplatScene />
      ) : (
        <div className="h-full w-full flex items-center justify-center bg-canvas text-text-muted text-sm">
          <div className="text-center max-w-md px-6">
            <div className="mb-2 text-text-primary">Splat viewer disabled.</div>
            <div className="text-xs">
              The viser iframe is unmounted. Click <code>Splats off</code> in
              the top-right to re-enable.
            </div>
          </div>
        </div>
      )}
      <ViserToggle />
      {/* Frame advance drives currentFrameIdx in the Zustand store;
          ViserSplatScene's effect forwards each bump to the control API. */}
      <PlaybackDriver />
      {!hasContent && <EmptyState />}
      <DropZone />
      <FpsIndicator />
      <PlaybackBar />
    </div>
  );
}
