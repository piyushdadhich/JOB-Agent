import { Cloud, Clock, RotateCw } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const TIER_VARIANTS = {
  TOP_TIER: "top_tier",
  STRONG: "strong",
  EXPLORATORY: "accent",
  SKIP: "muted",
};

export default function EvaluatorStatus({ evaluator }) {
  const usagePct =
    evaluator.budget > 0
      ? Math.min(100, Math.round((evaluator.calls_today / evaluator.budget) * 100))
      : 0;
  const latencySec = (evaluator.avg_latency_ms / 1000).toFixed(1);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cloud className="h-4 w-4 text-accent" />
          Cloud evaluator
        </CardTitle>
        <span className="text-xs text-muted-foreground">
          Model: <span className="font-mono">{evaluator.model || "—"}</span>
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1 flex items-baseline justify-between text-xs">
            <span className="text-muted-foreground">Today</span>
            <span className="font-mono tabular-nums">
              {evaluator.calls_today.toLocaleString()} /{" "}
              {evaluator.budget.toLocaleString()}{" "}
              <span className="text-muted-foreground">({usagePct}%)</span>
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${usagePct}%` }}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="rounded-md border border-border/50 bg-muted/30 p-2">
            <div className="text-muted-foreground">This week</div>
            <div className="mt-1 font-mono text-base font-semibold tabular-nums">
              {evaluator.calls_this_week.toLocaleString()}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-muted/30 p-2">
            <div className="text-muted-foreground">This month</div>
            <div className="mt-1 font-mono text-base font-semibold tabular-nums">
              {evaluator.calls_this_month.toLocaleString()}
            </div>
          </div>
        </div>

        {evaluator.tokens && (
          <div>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Cloud tokens
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-md border border-border/50 bg-muted/30 p-2">
                <div className="text-[10px] text-muted-foreground">Today</div>
                <div className="mt-1 font-mono text-xs tabular-nums">
                  {evaluator.tokens.today_input.toLocaleString()} in
                </div>
                <div className="font-mono text-xs tabular-nums">
                  {evaluator.tokens.today_output.toLocaleString()} out
                </div>
              </div>
              <div className="rounded-md border border-border/50 bg-muted/30 p-2">
                <div className="text-[10px] text-muted-foreground">This week</div>
                <div className="mt-1 font-mono text-xs tabular-nums">
                  {evaluator.tokens.this_week_input.toLocaleString()} in
                </div>
                <div className="font-mono text-xs tabular-nums">
                  {evaluator.tokens.this_week_output.toLocaleString()} out
                </div>
              </div>
              <div className="rounded-md border border-border/50 bg-muted/30 p-2">
                <div className="text-[10px] text-muted-foreground">This month</div>
                <div className="mt-1 font-mono text-xs tabular-nums">
                  {evaluator.tokens.this_month_input.toLocaleString()} in
                </div>
                <div className="font-mono text-xs tabular-nums">
                  {evaluator.tokens.this_month_output.toLocaleString()} out
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="rounded-md border border-border/50 bg-muted/30 p-2">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Clock className="h-3 w-3" />
              Avg latency
            </div>
            <div className="mt-1 font-mono text-base font-semibold tabular-nums">
              {latencySec}s
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-muted/30 p-2">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <RotateCw className="h-3 w-3" />
              Retries pending
            </div>
            <div className="mt-1 font-mono text-base font-semibold tabular-nums">
              {evaluator.retries_pending}
            </div>
          </div>
        </div>

        <div>
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Tier breakdown
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(evaluator.tiers).map(([tier, count]) => (
              <Badge key={tier} variant={TIER_VARIANTS[tier] || "muted"}>
                <span className="font-mono tabular-nums">{count}</span>
                <span className="ml-1 text-[9px] opacity-80">{tier}</span>
              </Badge>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
