import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Multi-select dropdown with click-outside dismissal.
 *
 * Props:
 *   label         visible button text
 *   options       string[]    distinct values for this axis
 *   selected      string[]    currently selected
 *   onChange      (string[]) => void
 *   disabledHint  tooltip when options is empty
 */
export default function MultiSelectDropdown({
  label,
  options,
  selected,
  onChange,
  disabledHint = "Will populate as evaluations run",
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const isEmpty = options.length === 0;

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  function toggle(opt) {
    if (selected.includes(opt)) {
      onChange(selected.filter((s) => s !== opt));
    } else {
      onChange([...selected, opt]);
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={isEmpty}
        title={isEmpty ? disabledHint : undefined}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-xs font-medium",
          "hover:bg-accent/40",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        <span>{label}</span>
        {selected.length > 0 && (
          <Badge variant="accent" className="h-5">
            {selected.length}
          </Badge>
        )}
        <ChevronDown className="h-3 w-3 text-muted-foreground" />
      </button>

      {open && !isEmpty && (
        <div className="absolute left-0 top-full z-20 mt-1 w-64 rounded-md border border-border bg-popover p-2 shadow-md">
          <div className="flex justify-between px-1 pb-1 text-xs">
            <button
              type="button"
              onClick={() => onChange([...options])}
              className="text-accent-foreground hover:underline"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-muted-foreground hover:underline"
            >
              Clear
            </button>
          </div>
          <div className="mt-1 max-h-64 overflow-y-auto border-t border-border pt-1">
            {options.map((opt) => (
              <label
                key={opt}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-accent/50"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(opt)}
                  onChange={() => toggle(opt)}
                  className="h-4 w-4 cursor-pointer"
                />
                <span className="truncate">{opt}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
