import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const POLL_MS = 2000;

export default function FirstRunProgress({ onFinish }) {
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const [started, setStarted] = useState(false);

  // Kick off the first run on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await api.setupFirstRunStart();
        if (!cancelled) setStarted(true);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll progress until status is terminal.
  useEffect(() => {
    if (!started) return undefined;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const r = await api.setupFirstRunProgress();
        if (cancelled) return;
        setProgress(r);
        if (r.status === "running") {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [started]);

  const terminal =
    progress && (progress.status === "done" || progress.status === "failed");

  return (
    <Card>
      <CardContent className="space-y-3 p-6 text-sm">
        <p className="text-muted-foreground">
          Running your first discovery + evaluation pass. This pulls
          fresh postings from every source you enabled in Step 6, then
          scores them against your career inventory. First runs are
          slower than subsequent ones because the source caches are cold.
        </p>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        {progress && (
          <div className="space-y-2">
            <div className="text-xs">
              Status: <strong>{progress.status}</strong>
            </div>
            <ul className="space-y-1 text-xs">
              {(progress.stages || []).map((s, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span
                    className={
                      "h-2 w-2 rounded-full " +
                      (s.state === "done"
                        ? "bg-success"
                        : s.state === "in_progress"
                          ? "bg-accent"
                          : "bg-muted-foreground")
                    }
                  />
                  {s.name} · {s.state}
                </li>
              ))}
            </ul>
            {progress.summary && (
              <div className="rounded-md border border-border bg-muted/30 p-2 text-xs">
                {progress.summary.message}
              </div>
            )}
          </div>
        )}

        {terminal && (
          <div className="flex justify-end">
            <Button onClick={onFinish}>Open dashboard</Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
