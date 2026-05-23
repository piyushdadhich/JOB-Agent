import { CheckCircle2, Loader2, X, XCircle } from "lucide-react";
import { useTask } from "@/contexts/TaskContext";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// App-wide banner for the active background task. Rendered at the
// top of AppLayout so batch-generation progress is visible on every
// page and survives navigation.
export default function GlobalTaskBanner() {
  const { activeTask, dismissTask } = useTask();
  if (!activeTask) return null;

  const { label, total, completed, status, current_opp_id,
    current_employer, errors, startedAt } = activeTask;
  const pct = total ? Math.round((completed / total) * 100) : 0;

  let eta = "";
  if (status === "running" && completed > 0 && startedAt) {
    const elapsed = (Date.now() - startedAt) / 1000;
    const remain = Math.round((elapsed / completed) * (total - completed));
    eta = remain > 5 ? `~${Math.ceil(remain / 60)} min remaining`
      : "almost done";
  }

  const tone =
    status === "error" ? "border-destructive/40 bg-destructive/10"
      : status === "done" ? "border-success/40 bg-success/10"
        : "border-accent/40 bg-accent/5";

  return (
    <div className={cn("border-b px-6 py-2 text-sm", tone)}>
      {status === "running" && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5 font-medium">
            <Loader2 className="h-4 w-4 animate-spin text-accent" />
            {label}: {completed} of {total} ({pct}%)
          </span>
          <div className="h-2 w-40 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          {current_opp_id != null && (
            <span className="text-xs text-muted-foreground">
              Current: #{current_opp_id}
              {current_employer ? ` ${current_employer}` : ""}
            </span>
          )}
          {eta && (
            <span className="text-xs text-muted-foreground">{eta}</span>
          )}
        </div>
      )}
      {status === "done" && (
        <div className="flex flex-wrap items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-success" />
          <span>
            {label} complete — {completed} of {total} generated.
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={dismissTask}
          >
            <X className="h-3 w-3" />
            Dismiss
          </Button>
        </div>
      )}
      {status === "error" && (
        <div className="flex flex-wrap items-center gap-2">
          <XCircle className="h-4 w-4 text-destructive" />
          <span>
            {label} failed at {completed}/{total}
            {errors && errors.length ? `: ${errors[0]}` : ""}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={dismissTask}
          >
            <X className="h-3 w-3" />
            Dismiss
          </Button>
        </div>
      )}
    </div>
  );
}
