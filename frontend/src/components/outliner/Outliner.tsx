import { ModelTree } from "./ModelTree";
import { RecipeTree } from "./RecipeTree";
import { HistoryTree } from "./HistoryTree";
import { SequenceTree } from "./SequenceTree";
import type { ModelItem } from "@/lib/types";

export function Outliner({
  onLoadRun,
  onPickModel,
}: {
  onLoadRun: (run_name: string) => void;
  onPickModel: (m: ModelItem) => void;
}) {
  return (
    <div className="py-1">
      <ModelTree onPick={onPickModel} />
      <SequenceTree onPick={onLoadRun} />
      <RecipeTree />
      <HistoryTree onPick={onLoadRun} />
    </div>
  );
}
