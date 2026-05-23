import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function DailyDigest() {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.digestToday();
        if (!cancelled) setD(r);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <Card className="border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
        Digest failed: {error}
      </Card>
    );
  }
  if (d === null) {
    return <Skeleton className="h-16 w-full" />;
  }

  const total = d.new_top_tier + d.new_strong + d.new_exploratory;

  return (
    <Card>
      <CardContent className="space-y-2 p-4 text-sm">
        <div className="flex items-baseline justify-between">
          <div className="font-semibold">Last {d.window_hours}h</div>
          <div className="text-xs text-muted-foreground">
            {d.generated_at.slice(0, 16).replace("T", " ")}
          </div>
        </div>
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-muted-foreground">TOP_TIER</div>
            <div className="text-lg font-semibold">{d.new_top_tier}</div>
          </div>
          <div>
            <div className="text-muted-foreground">STRONG</div>
            <div className="text-lg font-semibold">{d.new_strong}</div>
          </div>
          <div>
            <div className="text-muted-foreground">EXPLORATORY</div>
            <div className="text-lg font-semibold">{d.new_exploratory}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Follow-up</div>
            <div className="text-lg font-semibold">{d.needs_followup}</div>
          </div>
        </div>
        {total === 0 && d.needs_followup === 0 ? (
          <div className="text-xs text-muted-foreground">
            No new evaluations or follow-ups in this window.
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
