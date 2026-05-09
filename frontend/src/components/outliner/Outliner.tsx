import { ModelTree } from "./ModelTree";
import { RecipeTree } from "./RecipeTree";
import { HistoryTree } from "./HistoryTree";
import { SequenceTree } from "./SequenceTree";

export function Outliner({ onLoadRun }: { onLoadRun: (run_name: string) => void }) {
  return (
    <div className="py-1">
      <ModelTree />
      <SequenceTree onPick={onLoadRun} />
      <RecipeTree />
      <HistoryTree onPick={onLoadRun} />
    </div>
  );
}
