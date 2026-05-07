import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export function PropertyFolder({
  title,
  defaultOpen = true,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1 px-2 py-1.5 text-xs font-medium uppercase tracking-wider text-accent hover:bg-elevated"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {title}
      </button>
      {open && <div className="px-3 pb-2 space-y-1">{children}</div>}
    </div>
  );
}
