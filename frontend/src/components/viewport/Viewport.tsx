import { useActiveCell } from "@/lib/use-active-cell";
import { ViserSplatScene } from "./ViserSplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { FpsIndicator } from "./FpsIndicator";
import { PlaybackDriver } from "./PlaybackDriver";
import { PlaybackBar } from "./PlaybackBar";

export function Viewport() {
  const { activeCell } = useActiveCell();
  const hasContent = !!activeCell;

  return (
    <div className="h-full w-full relative bg-canvas">
      {/* Viser runs headless behind the iframe and is driven by
          ViserSplatScene's control-API POSTs. Sequence picker and
          PlaybackBar feed (cell, frame) into the same `viser_headless.py`
          process; viser owns rendering, React owns everything else. */}
      <ViserSplatScene />
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
