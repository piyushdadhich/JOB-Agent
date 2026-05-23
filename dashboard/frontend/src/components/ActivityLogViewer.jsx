import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const PAGE = 50;

const CATEGORIES = [
  { id: "all", label: "All" },
  { id: "discovery", label: "Discovery" },
  { id: "evaluation", label: "Evaluation" },
  { id: "generation", label: "Generation" },
  { id: "application", label: "Application" },
  { id: "scheduled_task", label: "Scheduled" },
  { id: "system", label: "System" },
];

const STATUS_DOT = {
  success: "bg-success",
  error: "bg-destructive",
  warning: "bg-warning",
  running: "bg-accent",
};

function formatTs(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// Scrollable activity feed with category filters. Embeddable —
// rendered as a section on the Settings page.
export default function ActivityLogViewer() {
  const [category, setCategory] = useState("all");
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(
    async (cat, offset) => {
      setLoading(true);
      setError(null);
      try {
        const r = await api.activityLog({
          category: cat, limit: PAGE, offset,
        });
        setTotal(r.total);
        setItems((prev) =>
          offset === 0 ? r.items : [...prev, ...r.items],
        );
      } catch (e) {
        setError(e.message || String(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    load(category, 0);
  }, [category, load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {CATEGORIES.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => setCategory(c.id)}
            className={cn(
              "rounded px-2 py-1 text-xs transition-colors",
              category === c.id
                ? "bg-accent/20 font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            {c.label}
          </button>
        ))}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => load(category, 0)}
          disabled={loading}
        >
          <RefreshCw
            className={cn("h-3 w-3", loading && "animate-spin")}
          />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          {error}
        </div>
      )}

      {items.length === 0 && !loading && (
        <div className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
          No activity recorded yet.
        </div>
      )}

      <div className="max-h-[420px] space-y-1 overflow-y-auto">
        {items.map((it) => (
          <div
            key={it.id}
            className="flex gap-2 rounded border border-border/60 bg-muted/20 p-2 text-xs"
          >
            <span
              className={cn(
                "mt-1 h-2 w-2 shrink-0 rounded-full",
                STATUS_DOT[it.status] || "bg-muted-foreground",
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {formatTs(it.timestamp)}
                </span>
                <span className="font-medium capitalize">
                  {it.category}
                </span>
                <span className="text-muted-foreground">
                  {it.action.replace(/_/g, " ")}
                </span>
              </div>
              {it.summary && (
                <div className="mt-0.5 text-muted-foreground">
                  {it.summary}
                </div>
              )}
              {it.error_message && (
                <div className="mt-0.5 text-destructive-foreground">
                  {it.error_message}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {items.length < total && (
        <Button
          size="sm"
          variant="outline"
          className="w-full"
          onClick={() => load(category, items.length)}
          disabled={loading}
        >
          Load more ({items.length} of {total})
        </Button>
      )}
    </div>
  );
}
