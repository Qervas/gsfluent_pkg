import { ModelTree } from "./ModelTree";
import { RecipeTree } from "./RecipeTree";
import { HistoryTree } from "./HistoryTree";

export function Outliner({ onLoadRun }: { onLoadRun: (run_name: string) => void }) {
  return (
    <div className="py-1">
      <ModelTree />
      <RecipeTree />
      <HistoryTree onPick={onLoadRun} />
    </div>
  );
}
