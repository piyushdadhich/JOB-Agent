import { useEffect, useState } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2 } from "lucide-react";
import { api } from "@/api";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Per-check fix hints surfaced when a check is not ok.
const FIX_HINTS = {
  scheduled_task:
    "If the nightly task failed, re-register it from Settings, or " +
    "in an Admin PowerShell run the register script.",
  evaluator:
    "The evaluator hasn't run recently — check the scheduled task " +
    "or run scripts/run_daily.py manually.",
  sources:
    "A source hasn't returned postings in over 24h — check its " +
    "config or run discovery.",
  api_budget:
    "Google AI Studio budget is low — it resets daily at midnight " +
    "Pacific.",
};

function statusIcon(status) {
  if (status === "ok") {
    return <CheckCircle2 className="h-4 w-4 text-success" />;
  }
  if (status === "warning") {
    return <AlertTriangle className="h-4 w-4 text-warning" />;
  }
  return <AlertCircle className="h-4 w-4 text-destructive" />;
}

export default function SystemHealth() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    api
      .health()
      .then(setData)
      .catch((e) => setError(e.message || String(e)));
  }, []);

  if (error) {
    return (
      <Card className="border-destructive/40 bg-destructive/10 p-3 text-sm">
        Health check failed: {error}
      </Card>
    );
  }
  if (!data) return null;

  return (
    <Card id="system-health" className="space-y-2 p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">System Health</span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
            data.status === "healthy"
              ? "bg-success/15 text-success"
              : data.status === "degraded"
                ? "bg-warning/15 text-warning"
                : "bg-destructive/15 text-destructive",
          )}
        >
          {data.status}
        </span>
      </div>
      <div className="divide-y divide-border">
        {data.checks.map((c) => {
          const clickable = c.status !== "ok" && FIX_HINTS[c.name];
          return (
            <div key={c.name}>
              <button
                type="button"
                disabled={!clickable}
                onClick={() =>
                  setExpanded((p) => (p === c.name ? null : c.name))
                }
                className={cn(
                  "flex w-full items-center gap-2 py-1.5 text-left text-xs",
                  clickable && "cursor-pointer hover:text-foreground",
                )}
              >
                {statusIcon(c.status)}
                <span className="font-medium capitalize">
                  {c.name.replace("_", " ")}:
                </span>
                <span className="text-muted-foreground">
                  {c.message}
                </span>
              </button>
              {expanded === c.name && FIX_HINTS[c.name] && (
                <div className="pb-2 pl-6 text-[11px] text-muted-foreground">
                  {FIX_HINTS[c.name]}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
