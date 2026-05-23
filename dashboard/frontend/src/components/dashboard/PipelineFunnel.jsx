import { ChevronRight, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Each funnel card's click target. Stages with no actionable
// destination (Discovered, Evaluated) stay null and the cards
// render non-clickable.
const NAV_FOR_STAGE = {
  Discovered: null,
  Evaluated: null,
  Shortlisted: "shortlist",  // browse and select
  Selected: "prompts",       // generate resume + CL prompts
  "Docs Ready": "apply",     // submit via Playwright agent
  Applied: "history",        // track status
  Interview: "history",
  Offer: "history",
};

function DeltaBadge({ delta, deltaPct }) {
  if (delta === 0 || delta === null) {
    return (
      <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <Minus className="h-3 w-3" />
        <span className="font-mono">0</span>
      </span>
    );
  }
  const positive = delta > 0;
  const Icon = positive ? TrendingUp : TrendingDown;
  const color = positive ? "text-success" : "text-destructive-foreground";
  const sign = positive ? "+" : "";
  const pct = deltaPct !== null && deltaPct !== undefined
    ? ` (${sign}${deltaPct.toFixed(1)}%)`
    : "";
  return (
    <span className={cn("flex items-center gap-1 text-[11px]", color)}>
      <Icon className="h-3 w-3" />
      <span className="font-mono">
        {sign}
        {delta}
        {pct}
      </span>
    </span>
  );
}

export default function PipelineFunnel({ stages, onNavigate }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      {stages.map((stage, idx) => {
        const target = NAV_FOR_STAGE[stage.name];
        const clickable = !!target && stage.count > 0;
        return (
          <div key={stage.name} className="relative">
            <Card
              onClick={
                clickable && onNavigate
                  ? () => onNavigate(target)
                  : undefined
              }
              className={cn(
                "flex h-full flex-col justify-between gap-2 p-3 transition-colors",
                clickable && "cursor-pointer hover:border-accent",
              )}
            >
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {stage.name}
              </div>
              <div className="font-mono text-[28px] font-semibold leading-none tabular-nums">
                {stage.count.toLocaleString()}
              </div>
              <DeltaBadge delta={stage.delta} deltaPct={stage.delta_pct} />
            </Card>
            {idx < stages.length - 1 && (
              <ChevronRight
                className="pointer-events-none absolute -right-2 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-muted-foreground xl:block"
                aria-hidden
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
