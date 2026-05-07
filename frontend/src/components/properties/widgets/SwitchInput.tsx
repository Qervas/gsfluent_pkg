import { Switch } from "@/components/ui/switch";

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
    <div className="flex items-center gap-2 py-0.5" title={hint}>
      <span className="text-text-secondary text-xs flex-1 truncate">{label}</span>
      <Switch checked={value} onCheckedChange={onChange} />
    </div>
  );
}
