import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function SelectInput({
  label,
  value,
  options,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5" title={hint}>
      <span className="text-text-secondary text-xs flex-1 truncate">{label}</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-32">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
