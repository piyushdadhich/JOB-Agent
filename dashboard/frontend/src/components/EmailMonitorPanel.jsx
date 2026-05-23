import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Inbox,
  Mail,
  RefreshCw,
} from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function relativeTime(iso) {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "never";
  const diffMs = Date.now() - t;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function dotColor(status) {
  if (status === "active") return "bg-success";
  if (status === "stale") return "bg-warning";
  return "bg-destructive";
}

function dotLabel(status) {
  if (status === "active") return "Active";
  if (status === "stale") return "Stale";
  return "Not configured";
}

function deriveStatus(s) {
  if (!s || !s.configured) return "unconfigured";
  if (!s.last_check) return "stale";
  const ageMs = Date.now() - new Date(s.last_check).getTime();
  return ageMs > 6 * 60 * 60 * 1000 ? "stale" : "active";
}

export default function EmailMonitorPanel() {
  const [status, setStatus] = useState(null);
  const [recent, setRecent] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setupGuide, setSetupGuide] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, r] = await Promise.all([
        api.emailMonitorStatus(),
        api.emailMonitorRecent(),
      ]);
      setStatus(s);
      setRecent(r);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const checkNow = async () => {
    setRunning(true);
    setError(null);
    try {
      const r = await api.emailMonitorCheckNow();
      if (r.status === "error" && r.error) {
        setError(r.error);
      }
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setRunning(false);
    }
  };

  const openSetupGuide = async () => {
    setSetupOpen((v) => !v);
    if (!setupGuide) {
      try {
        const g = await api.emailMonitorSetupGuide();
        setSetupGuide(g.markdown);
      } catch (e) {
        setError(e.message || String(e));
      }
    }
  };

  const derived = deriveStatus(status);

  return (
    <Card>
      <CardContent className="space-y-3 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-semibold">Email Monitor</span>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  dotColor(derived),
                )}
              />
              {dotLabel(derived)}
            </span>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={checkNow}
            disabled={running}
          >
            <RefreshCw
              className={cn("mr-1 h-3 w-3", running && "animate-spin")}
            />
            Check Now
          </Button>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive-foreground">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {status && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Last check: {relativeTime(status.last_check)}</span>
            <span>
              24h: {status.emails_checked_24h} emails checked,{" "}
              {status.postings_parsed_24h} postings found
            </span>
          </div>
        )}

        {recent && recent.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
              aria-expanded={expanded}
            >
              {expanded ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
              Recent parsed ({recent.length})
            </button>
            {expanded && (
              <ul className="mt-2 space-y-1 text-xs">
                {recent.slice(0, 5).map((e, i) => (
                  <li
                    key={`${e.email_subject}-${i}`}
                    className="flex flex-wrap items-center gap-1 rounded border border-border/50 bg-muted/30 px-2 py-1"
                  >
                    <Inbox className="h-3 w-3 shrink-0 text-muted-foreground" />
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {e.sender}
                    </span>
                    <span className="text-muted-foreground">—</span>
                    <span className="truncate">
                      {e.posting_titles.slice(0, 2).join(", ")}
                      {e.postings_extracted > 2 &&
                        ` +${e.postings_extracted - 2} more`}
                    </span>
                    <Badge variant="muted" className="ml-auto">
                      {e.parser_used}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {derived === "unconfigured" && (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs">
            <p className="text-foreground">
              Gmail not connected. Connect to discover jobs from
              recruiter emails and newsletters.
            </p>
            <button
              type="button"
              onClick={openSetupGuide}
              className="mt-1 text-accent underline-offset-2 hover:underline"
            >
              {setupOpen ? "Hide" : "Setup Guide"}
            </button>
            {setupOpen && setupGuide && (
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-[11px] text-foreground">
                {setupGuide}
              </pre>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
