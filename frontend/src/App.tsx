import { useEffect } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Outliner } from "@/components/outliner/Outliner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useStreamClient } from "@/lib/use-stream";
import { useStore } from "@/lib/store";

export default function App() {
  const client = useStreamClient();
  const resetForNewRun = useStore((s) => s.resetForNewRun);

  useEffect(() => {
    client.connect();
  }, [client]);

  const onLoadRun = (run_name: string) => {
    resetForNewRun(run_name);
    client.subscribe(run_name);
  };

  return (
    <AppShell
      outliner={<Outliner onLoadRun={onLoadRun} />}
      viewport={<div className="bg-elevated h-full" />}
      properties={
        <div className="p-3 space-y-3">
          <div className="text-xs text-text-secondary uppercase tracking-wider">
            Properties (Task 2.6)
          </div>
          <div>
            <Label>Recipe</Label>
            <Select>
              <SelectTrigger>
                <SelectValue placeholder="Pick one" />
              </SelectTrigger>
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
      }
    />
  );
}
