import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { api } from "@/api";
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";
import DailyDigest from "@/components/dashboard/DailyDigest";
import PipelineFunnel from "@/components/dashboard/PipelineFunnel";
import TrendChart from "@/components/dashboard/TrendChart";
import SourceHealth from "@/components/dashboard/SourceHealth";
import EvaluatorStatus from "@/components/dashboard/EvaluatorStatus";
import SkillGapHint from "@/components/dashboard/SkillGapHint";
import SystemHealth from "@/components/dashboard/SystemHealth";

const RANGES = [
  { id: "today", label: "Today" },
  { id: "week", label: "Week" },
  { id: "month", label: "Month" },
];

export default function Dashboard({ goToTab }) {
  const [range, setRange] = useState("week");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getPipeline(range)
      .then((body) => {
        if (cancelled) return;
        setData(body);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range]);

  return (
    <div className="space-y-6">
      <DailyDigest />
      <SkillGapHint goToTab={goToTab} />

      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted-foreground">
            {data
              ? `${data.period.start} → ${data.period.end}`
              : "Loading range…"}
          </p>
        </div>
        <ToggleGroup
          type="single"
          value={range}
          onValueChange={(v) => v && setRange(v)}
          aria-label="Date range"
        >
          {RANGES.map((r) => (
            <ToggleGroupItem key={r.id} value={r.id}>
              {r.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive-foreground">
          <AlertCircle className="h-4 w-4" />
          <span>Failed to load pipeline: {error}</span>
        </Card>
      )}

      {loading && !data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-[100px] w-full" />
            ))}
          </div>
          <Skeleton className="h-[280px] w-full" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-[300px] w-full" />
            <Skeleton className="h-[300px] w-full" />
          </div>
        </>
      )}

      {data && (
        <>
          <PipelineFunnel stages={data.stages} onNavigate={goToTab} />
          <TrendChart data={data.daily_breakdown} />
          <div className="grid gap-4 lg:grid-cols-2">
            <SourceHealth sources={data.sources} />
            <EvaluatorStatus evaluator={data.evaluator} />
          </div>
        </>
      )}
      <SystemHealth />
    </div>
  );
}
