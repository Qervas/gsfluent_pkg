import { useActiveCell } from "@/lib/use-active-cell";
import { SplatScene } from "./SplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { FpsIndicator } from "./FpsIndicator";
import { PlaybackBar } from "./PlaybackBar";
import { ReorientControls } from "./ReorientControls";

export function Viewport() {
  const { activeCell } = useActiveCell();
  const hasContent = !!activeCell;

  return (
    <div className="h-full w-full relative bg-canvas">
      <SplatScene />
      {!hasContent && <EmptyState />}
      <ReorientControls />
      <DropZone />
      <FpsIndicator />
      <PlaybackBar />
    </div>
  );
}
