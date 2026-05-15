import { HelpCircle } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Small "?" affordance next to a labelled control. Renders nothing
 *  when no hint is provided so the row stays clean for fields whose
 *  meaning is obvious. The icon is the trigger; hovering or focusing
 *  it shows a Radix-styled tooltip with the hint text.
 *
 *  Discoverability matters more than density here — users shouldn't
 *  have to roll their mouse across every row to find out whether
 *  there's help. */
export function HelpIcon({ hint }: { hint?: string }) {
  if (!hint) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label="help"
          className="text-text-muted hover:text-text-primary shrink-0 cursor-help focus:outline-none focus-visible:ring-1 focus-visible:ring-accent rounded"
        >
          <HelpCircle size={12} />
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" align="end" className="max-w-xs whitespace-normal leading-snug">
        {hint}
      </TooltipContent>
    </Tooltip>
  );
}
