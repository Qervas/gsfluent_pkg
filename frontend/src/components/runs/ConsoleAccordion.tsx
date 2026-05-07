import { ChevronUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useStore } from "@/lib/store";

export function ConsoleAccordion() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const simLog = useStore((s) => s.simLog);

  useEffect(() => {
    if (open && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [simLog, open]);

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 hover:text-text-primary"
        title={open ? "Hide console" : "Show console"}
      >
        <ChevronUp
          size={11}
          className={open ? "rotate-180 transition-transform" : "transition-transform"}
        />
        console
      </button>
      {open && (
        <div className="absolute bottom-8 left-0 right-0 h-72 bg-canvas border-t border-border z-10 shadow-xl">
          <div
            ref={ref}
            className="h-full overflow-auto font-mono text-[11px] p-2 leading-tight whitespace-pre-wrap"
          >
            {simLog.length === 0 ? (
              <span className="text-text-muted">(no output yet)</span>
            ) : (
              simLog.map((line, i) => (
                <div key={i} className="text-text-primary">
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </>
  );
}
