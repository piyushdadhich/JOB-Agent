import { AlertTriangle, ExternalLink, Check, X, Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const TIER_VARIANT = {
  TOP_TIER: "top_tier",
  STRONG: "strong",
};

const GRADE_STYLE = {
  A: "bg-emerald-500 text-white",
  B: "bg-sky-500 text-white",
  C: "bg-yellow-500 text-black",
  D: "bg-orange-500 text-white",
  F: "bg-red-600 text-white",
};

const SEVERITY_STYLE = {
  high: "bg-red-100 text-red-800 border-red-300",
  medium: "bg-orange-100 text-orange-800 border-orange-300",
  low: "bg-yellow-100 text-yellow-800 border-yellow-300",
};

const SENTIMENT_STYLE = {
  positive: "bg-emerald-100 text-emerald-800 border-emerald-300",
  neutral: "bg-slate-100 text-slate-700 border-slate-300",
  negative: "bg-red-100 text-red-800 border-red-300",
};

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function PostingCard({
  item,
  status,
  onSelect,
  onUnselect,
  onSkip,
  onUnskip,
  onFlag,
  busy,
}) {
  const isSelected = status === "selected";
  const isSkipped = status === "skipped";

  const meta = [
    item.location,
    item.source,
    item.posted_at && formatDate(item.posted_at),
    item.ats_platform,
  ].filter(Boolean);

  return (
    <Card
      className={cn(
        "transition-all",
        isSelected && "border-success bg-success/5",
        isSkipped && "border-dashed text-muted-foreground",
      )}
    >
      <CardContent className="grid grid-cols-[auto_1fr_auto] gap-4 p-4">
        <div className="flex flex-col items-start gap-1">
          <Badge
            variant={TIER_VARIANT[item.tier] || "outline"}
            className="self-start"
          >
            {item.tier || "—"}
          </Badge>
          {item.letter_grade && (
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[11px] font-bold",
                GRADE_STYLE[item.letter_grade] || "bg-slate-300",
              )}
              title="Letter grade (A=8+, B=6.5+, C=5+, D=3+, F<3)"
            >
              {item.letter_grade}
            </span>
          )}
        </div>

        <div className="min-w-0 space-y-2">
          <div className="min-w-0 flex-1">
            <div className="font-semibold leading-tight">
              {item.title}
              <span className="font-normal text-muted-foreground">
                {" "}— {item.employer}
              </span>
            </div>
            {meta.length > 0 && (
              <div className="mt-1 text-xs text-muted-foreground">
                {meta.join(" · ")}
              </div>
            )}
          </div>

          {item.reasoning && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {item.reasoning}
            </p>
          )}

          {Array.isArray(item.red_flags) && item.red_flags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {item.red_flags.map((rf, i) => (
                <span
                  key={`rf-${i}`}
                  className={cn(
                    "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px]",
                    SEVERITY_STYLE[rf.severity] || SEVERITY_STYLE.low,
                  )}
                  title={`Severity: ${rf.severity || "low"}`}
                >
                  <AlertTriangle className="h-2.5 w-2.5" />
                  {rf.flag}
                </span>
              ))}
            </div>
          )}

          {Array.isArray(item.culture_signals)
            && item.culture_signals.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {item.culture_signals.map((cs, i) => (
                <span
                  key={`cs-${i}`}
                  className={cn(
                    "rounded border px-1.5 py-0.5 text-[10px]",
                    SENTIMENT_STYLE[cs.sentiment] || SENTIMENT_STYLE.neutral,
                  )}
                  title={`Culture: ${cs.sentiment || "neutral"}`}
                >
                  {cs.signal}
                </span>
              ))}
            </div>
          )}

          {Array.isArray(item.interview_plan)
            && item.interview_plan.length > 0 && (
            <div className="rounded border border-sky-200 bg-sky-50 p-2 text-xs">
              <div className="mb-1 font-semibold text-sky-900">
                Interview Plan
              </div>
              <ul className="ml-4 list-disc space-y-1 text-sky-900">
                {item.interview_plan.map((ip, i) => (
                  <li key={`ip-${i}`}>
                    <span className="font-medium">
                      {ip.topic || `Point ${i + 1}`}:
                    </span>{" "}
                    {ip.talking_point}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            {item.source_url && (
              <Button
                asChild
                size="sm"
                variant="outline"
              >
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5"
                >
                  <ExternalLink className="h-3 w-3" />
                  View posting
                </a>
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => isSkipped
                ? onUnskip(item.opportunity_id)
                : onSkip(item.opportunity_id)}
            >
              {busy && (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
              {isSkipped ? "Undo Skip" : (
                <>
                  <X className="h-3 w-3" />
                  Skip
                </>
              )}
            </Button>
            {onFlag && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => onFlag(item)}
                title="Flag as bad match — removes from shortlist + trains future evals"
              >
                <AlertTriangle className="h-3 w-3" />
                Flag
              </Button>
            )}
            <Button
              size="sm"
              variant={isSelected ? "outline" : "accent"}
              disabled={busy}
              onClick={() => isSelected
                ? onUnselect(item.opportunity_id)
                : onSelect(item.opportunity_id)}
              className={cn(
                isSelected &&
                  "border-destructive/40 text-destructive hover:bg-destructive/10",
              )}
            >
              {busy && (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
              {isSelected ? (
                <>
                  <X className="h-3 w-3" />
                  Deselect
                </>
              ) : (
                <>
                  <Check className="h-3 w-3" />
                  Select to apply
                </>
              )}
            </Button>
          </div>
        </div>

        <div className="flex flex-col items-end gap-1 pl-2">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Fit
          </span>
          <span className="font-mono text-2xl font-semibold leading-none tabular-nums">
            {item.fit_score != null ? item.fit_score : "—"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
