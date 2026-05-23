import { CheckCircle2, AlertTriangle, Inbox } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

function relativeTime(iso) {
  if (!iso) return "never";
  const seen = new Date(iso);
  const diffMs = Date.now() - seen.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function isStale(iso) {
  if (!iso) return true;
  const ageMs = Date.now() - new Date(iso).getTime();
  return ageMs > 24 * 60 * 60 * 1000; // 24h
}

export default function SourceHealth({ sources }) {
  const active = sources.length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Source health</CardTitle>
        <span className="text-xs text-muted-foreground">
          {active} {active === 1 ? "source" : "sources"} active in range
        </span>
      </CardHeader>
      <CardContent className="space-y-2">
        {sources.length === 0 && (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-border p-4 text-xs text-muted-foreground">
            <Inbox className="h-4 w-4" />
            No discoveries in range.
          </div>
        )}
        {sources.map((source) => {
          const stale = isStale(source.last_seen);
          const Icon = stale ? AlertTriangle : CheckCircle2;
          return (
            <div
              key={source.name}
              className="flex items-center justify-between rounded-md border border-border/50 bg-muted/30 px-3 py-2 text-sm"
            >
              <div className="flex items-center gap-2 truncate">
                <Icon
                  className={cn(
                    "h-4 w-4 shrink-0",
                    stale ? "text-warning" : "text-success",
                  )}
                />
                <span className="truncate font-medium">{source.name}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs tabular-nums">
                  {source.count.toLocaleString()}
                </span>
                <span className="hidden text-[11px] text-muted-foreground sm:inline">
                  {stale ? "stale · " : ""}
                  {relativeTime(source.last_seen)}
                </span>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
