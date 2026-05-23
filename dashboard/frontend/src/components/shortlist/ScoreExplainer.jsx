import { useEffect, useState } from "react";
import { api } from "@/api";
import { Skeleton } from "@/components/ui/skeleton";

export default function ScoreExplainer({ postingId, open }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !postingId) return undefined;
    let cancelled = false;
    setData(null);
    setError(null);
    api
      .explainScore(postingId)
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [postingId, open]);

  if (!open) return null;
  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
        Explain failed: {error}
      </div>
    );
  }
  if (data === null) {
    return <Skeleton className="h-32 w-full" />;
  }

  if (
    data.matched_skills.length === 0
    && data.missed_skills.length === 0
  ) {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs text-muted-foreground">
        Not scored against the inventory yet.
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2 text-xs">
      <div>
        <span className="font-medium">Coverage:</span>{" "}
        {data.coverage_pct.toFixed(1)}% (
        {data.matched_skills.length} matched ·{" "}
        {data.missed_skills.length} missed)
      </div>

      {data.matched_skills.length > 0 && (
        <div>
          <div className="text-muted-foreground">Matched</div>
          <div className="flex flex-wrap gap-1 pt-1">
            {data.matched_skills.slice(0, 20).map((s, i) => (
              <span
                key={i}
                className="rounded border border-success/40 bg-success/10 px-1.5 py-0.5"
              >
                {s}
              </span>
            ))}
            {data.matched_skills.length > 20 && (
              <span className="text-muted-foreground">
                + {data.matched_skills.length - 20} more
              </span>
            )}
          </div>
        </div>
      )}

      {data.missed_skills.length > 0 && (
        <div>
          <div className="text-muted-foreground">Missing</div>
          <div className="flex flex-wrap gap-1 pt-1">
            {data.missed_skills.slice(0, 20).map((s, i) => (
              <span
                key={i}
                className="rounded border border-destructive/40 bg-destructive/10 px-1.5 py-0.5"
              >
                {s}
              </span>
            ))}
            {data.missed_skills.length > 20 && (
              <span className="text-muted-foreground">
                + {data.missed_skills.length - 20} more
              </span>
            )}
          </div>
        </div>
      )}

      {data.reasoning && (
        <div>
          <div className="text-muted-foreground">Reasoning</div>
          <pre className="whitespace-pre-wrap text-[11px]">
            {data.reasoning.slice(0, 1200)}
            {data.reasoning.length > 1200 ? "…" : ""}
          </pre>
        </div>
      )}
    </div>
  );
}
