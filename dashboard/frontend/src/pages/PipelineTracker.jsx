import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Check,
  Clipboard,
  StickyNote,
  Trash2,
} from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const COLUMNS = [
  { id: "shortlisted", label: "Shortlisted", accent: "border-l-strong" },
  { id: "applied", label: "Applied", accent: "border-l-accent" },
  { id: "interview", label: "Interview", accent: "border-l-top-tier" },
  { id: "offer", label: "Offer", accent: "border-l-success" },
  { id: "rejected", label: "Rejected", accent: "border-l-destructive" },
];

const NEXT_BUCKET = {
  shortlisted: "applied",
  applied: "interview",
  interview: "offer",
  offer: null,
  rejected: null,
};

const TIER_VARIANT = {
  TOP_TIER: "top_tier",
  STRONG: "strong",
  EXPLORATORY: "muted",
};

function relativeStageLabel(days, status) {
  if (status === "shortlisted") {
    if (days === 0) return "Selected today";
    if (days === 1) return "Selected 1 day ago";
    return `Selected ${days} days ago`;
  }
  if (status === "applied") {
    if (days === 0) return "Applied today";
    if (days === 1) return "Applied 1 day ago";
    return `Applied ${days} days ago`;
  }
  if (days === 0) return "In stage today";
  if (days === 1) return "In stage 1 day";
  return `In stage ${days} days`;
}

function CardItem({
  card,
  onMove,
  onReject,
  onNotesSave,
  highlight,
  innerRef,
}) {
  const [notesOpen, setNotesOpen] = useState(false);
  const [draft, setDraft] = useState(card.notes || "");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setDraft(card.notes || "");
  }, [card.notes]);

  const save = async () => {
    setSaving(true);
    try {
      await onNotesSave(card.application_id, draft);
      setNotesOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const copyDetails = async () => {
    const lines = [card.employer, card.title];
    if (card.applied_at) {
      lines.push(`Applied: ${card.applied_at.slice(0, 10)}`);
    }
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      // Clipboard failures are silent — user can read the card directly.
    }
  };

  const next = NEXT_BUCKET[card.status];

  return (
    <Card
      ref={innerRef}
      className={cn(
        "border-l-4 transition-colors",
        COLUMNS.find((c) => c.id === card.status)?.accent,
        highlight && "ring-2 ring-warning",
      )}
    >
      <CardContent className="space-y-2 p-3 text-xs">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold leading-tight">
              {card.employer}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {card.title}
            </div>
            {card.location && (
              <div className="truncate text-[11px] text-muted-foreground/80">
                {card.location}
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1">
            {card.tier && (
              <Badge variant={TIER_VARIANT[card.tier] || "outline"}>
                {card.tier}
              </Badge>
            )}
            {card.fit_score != null && (
              <span className="font-mono text-base font-semibold leading-none tabular-nums">
                {card.fit_score}
              </span>
            )}
          </div>
        </div>

        <p className="text-[11px] text-muted-foreground">
          {relativeStageLabel(card.days_in_stage, card.status)}
        </p>

        {card.needs_followup && (
          <div className="flex items-center justify-between gap-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[11px] text-warning">
            <div className="flex items-center gap-1.5">
              <AlertTriangle className="h-3 w-3" />
              {card.days_in_stage}+ days — follow up?
            </div>
            <button
              type="button"
              onClick={copyDetails}
              className="flex items-center gap-1 rounded border border-warning/40 px-1.5 py-0.5 text-[10px] font-medium hover:bg-warning/20"
              title="Copy employer + title + applied date to clipboard"
            >
              <Clipboard className="h-3 w-3" />
              {copied ? "Copied" : "Copy details"}
            </button>
          </div>
        )}

        {notesOpen ? (
          <div className="space-y-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={3}
              className="w-full rounded border border-border bg-background p-2 text-xs"
              placeholder="Notes about this application…"
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={save} disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDraft(card.notes || "");
                  setNotesOpen(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setNotesOpen(true)}
            className="flex w-full items-start gap-1 rounded border border-dashed border-border/60 px-2 py-1 text-left text-[11px] text-muted-foreground hover:bg-muted/40"
            title="Click to edit notes"
          >
            <StickyNote className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="line-clamp-2">
              {card.notes ? card.notes : "Add notes…"}
            </span>
          </button>
        )}

        <div className="flex flex-wrap items-center gap-1 pt-1">
          {next && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onMove(card.application_id, next)}
              title={`Move to ${next}`}
            >
              <ArrowRight className="h-3 w-3" />
              {next}
            </Button>
          )}
          {card.status !== "rejected" && (
            <Button
              size="sm"
              variant="outline"
              className="border-destructive/40 text-destructive hover:bg-destructive/10"
              onClick={() => onReject(card.application_id)}
              title="Move to Rejected"
            >
              <Trash2 className="h-3 w-3" />
              Reject
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Toast({ message, onDone }) {
  useEffect(() => {
    if (!message) return undefined;
    const t = setTimeout(() => onDone?.(), 4000);
    return () => clearTimeout(t);
  }, [message, onDone]);
  if (!message) return null;
  return (
    <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-md border border-success/40 bg-success/10 px-3 py-2 text-sm text-success shadow-lg">
      <Check className="h-4 w-4" />
      {message}
    </div>
  );
}

export default function PipelineTracker() {
  const [data, setData] = useState(null);
  const [statsExtra, setStatsExtra] = useState(null);
  const [error, setError] = useState(null);
  const [highlightId, setHighlightId] = useState(null);
  const [toast, setToast] = useState(null);
  const cardRefs = useRef({});

  const load = useCallback(async () => {
    setError(null);
    try {
      const [board, stats] = await Promise.all([
        api.pipelineBoard(),
        api.pipelineStats(),
      ]);
      setData(board);
      setStatsExtra(stats);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const moveCard = async (id, nextStatus) => {
    try {
      await api.pipelineUpdateStatus(id, nextStatus);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const rejectCard = async (id) => {
    try {
      await api.pipelineUpdateStatus(id, "rejected");
      await load();
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const saveNotes = async (id, notes) => {
    await api.pipelineUpdateNotes(id, notes);
    await load();
  };

  const stats = data?.stats;

  const firstFollowupId = useMemo(() => {
    if (!data) return null;
    for (const colId of ["applied", "interview"]) {
      const cards = data.columns[colId] || [];
      const f = cards.find((c) => c.needs_followup);
      if (f) return f.application_id;
    }
    return null;
  }, [data]);

  const scrollToFollowups = () => {
    if (firstFollowupId == null) return;
    const el = cardRefs.current[firstFollowupId];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightId(firstFollowupId);
      setTimeout(() => setHighlightId(null), 2400);
    }
  };

  return (
    <div className="space-y-4">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      {/* Stats bar */}
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <div>
            {statsExtra ? (
              <>
                <span className="text-muted-foreground">This month:</span>{" "}
                <span className="font-semibold">
                  {statsExtra.this_month.applied}
                </span>{" "}
                applied ·{" "}
                <span className="font-semibold">
                  {statsExtra.this_month.interview}
                </span>{" "}
                interview ·{" "}
                <span className="font-semibold">
                  {statsExtra.this_month.offer}
                </span>{" "}
                offer
              </>
            ) : (
              <Skeleton className="h-4 w-72" />
            )}
          </div>
          {stats && stats.needs_followup > 0 && (
            <button
              type="button"
              onClick={scrollToFollowups}
              className="flex items-center gap-1 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-xs text-warning hover:bg-warning/20"
            >
              <AlertTriangle className="h-3 w-3" />
              {stats.needs_followup} application
              {stats.needs_followup === 1 ? "" : "s"} need
              {stats.needs_followup === 1 ? "s" : ""} follow-up
            </button>
          )}
          {stats && (
            <span className="ml-auto text-xs text-muted-foreground">
              Total in pipeline: {stats.total}
            </span>
          )}
        </div>
      </Card>

      {/* Kanban columns */}
      {data === null ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5">
          {COLUMNS.map((c) => (
            <Skeleton key={c.id} className="h-80 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5">
          {COLUMNS.map((col) => {
            const cards = data.columns[col.id] || [];
            return (
              <div key={col.id} className="flex min-w-0 flex-col gap-2">
                <div
                  className={cn(
                    "flex items-center justify-between rounded-md border border-border bg-card px-3 py-2",
                  )}
                >
                  <span className="text-sm font-semibold">{col.label}</span>
                  <Badge variant="muted">{cards.length}</Badge>
                </div>
                <div className="space-y-2">
                  {cards.length === 0 && (
                    <div className="rounded-md border border-dashed border-border/50 p-4 text-center text-xs text-muted-foreground">
                      None
                    </div>
                  )}
                  {cards.map((card) => (
                    <CardItem
                      key={card.application_id}
                      card={card}
                      onMove={moveCard}
                      onReject={rejectCard}
                      onNotesSave={saveNotes}
                      highlight={highlightId === card.application_id}
                      innerRef={(el) => {
                        if (el) cardRefs.current[card.application_id] = el;
                      }}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  );
}
