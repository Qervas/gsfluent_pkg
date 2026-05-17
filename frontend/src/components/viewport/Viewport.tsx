import { useActiveCell } from "@/lib/use-active-cell";
import { ViserSplatScene } from "./ViserSplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { RenderModeToggle } from "./RenderModeToggle";
import { FpsIndicator } from "./FpsIndicator";
import { PlaybackDriver } from "./PlaybackDriver";
import { PlaybackBar } from "./PlaybackBar";

export function Viewport() {
  const { activeCell } = useActiveCell();
  // Splat mode is available for both static model preview AND sim run
  // playback. Static models bootstrap from /api/models/file/...; sim runs
  // come from /api/runs/<name>/frame/0.ply. Viser handles both kinds
  // behind the same control API now, so any non-null cell is splat-eligible.
  const splatAvailable = !!activeCell;
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
      <RenderModeToggle splatAvailable={splatAvailable} />
      <FpsIndicator />
      <PlaybackBar />
    </div>
  );
}
