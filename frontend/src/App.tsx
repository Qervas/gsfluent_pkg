import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export default function App() {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <div className="h-10 border-b border-border px-3 flex items-center gap-2 backdrop-blur bg-canvas/85">
        <span className="text-accent">●</span>
        <span className="font-semibold">gsfluent</span>
        <span className="text-text-muted">·</span>
        <span className="text-text-secondary text-xs">no model loaded</span>
        <div className="ml-auto flex gap-2">
          <Button>Run</Button>
          <Button variant="destructive">Cancel</Button>
        </div>
      </div>
      <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-xs">
        <span className="text-accent border-b-2 border-accent pb-0.5">Sim</span>
        <span className="text-text-muted">Compare (soon)</span>
        <span className="text-text-muted">Render (soon)</span>
        <span className="text-text-muted">Recipes (soon)</span>
      </div>
      <div className="flex-1 grid grid-cols-[200px_1fr_280px]">
        <div className="border-r border-border p-3 text-xs text-text-secondary">Outliner</div>
        <div className="bg-elevated"></div>
        <div className="border-l border-border p-3 space-y-3">
          <div className="text-xs text-text-secondary uppercase tracking-wider">Sample primitives</div>
          <div>
            <Label>Recipe</Label>
            <Select>
              <SelectTrigger><SelectValue placeholder="Pick one" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="jelly">jelly</SelectItem>
                <SelectItem value="metal">metal</SelectItem>
                <SelectItem value="sand">sand</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Particles</Label>
            <Slider defaultValue={[200000]} min={20000} max={2000000} step={10000} />
          </div>
          <div>
            <Label>n_grid</Label>
            <Input type="number" defaultValue={150} />
          </div>
          <div className="flex items-center gap-2">
            <Switch defaultChecked />
            <Label>Move camera</Label>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost">⌘K</Button>
            <Button variant="outline">Reset</Button>
          </div>
        </div>
      </div>
      <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted">
        <span className="text-accent">●</span>
        <span>idle</span>
        <span className="ml-auto">⌘K</span>
      </div>
    </div>
  );
}
