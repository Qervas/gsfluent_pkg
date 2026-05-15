import { Switch } from "@/components/ui/switch";
import { HelpIcon } from "./HelpIcon";

export function SwitchInput({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-text-secondary text-xs flex-1 truncate flex items-center gap-1">
        <span className="truncate">{label}</span>
        <HelpIcon hint={hint} />
      </span>
      <Switch checked={value} onCheckedChange={onChange} />
    </div>
  );
}
