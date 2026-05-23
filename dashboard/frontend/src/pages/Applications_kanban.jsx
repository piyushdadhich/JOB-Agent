import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Copy,
  Cpu,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  Plus,
  Send,
  X as XIcon,
} from "lucide-react";
import { api } from "@/api";
import AddJobModal from "@/components/AddJobModal.jsx";
import MarkdownPreview from "@/components/MarkdownPreview.jsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// Kanban column order + display labels. `selected` is the curated
// pool (selected_at set, no docs yet); `docs_ready` flips once both
// resume + cover letter are saved.
const COLUMNS = [
  { id: "selected", label: "Selected" },
  { id: "docs_ready", label: "Docs Ready" },
  { id: "applied", label: "Applied" },
  { id: "interview", label: "Interview" },
  { id: "offer", label: "Offer" },
  { id: "rejected", label: "Rejected" },
];

const TIER_BORDER = {
  TOP_TIER: "border-l-success",
  STRONG: "border-l-accent",
  EXPLORATORY: "border-l-muted-foreground",
};

const TIER_VARIANT = {
  TOP_TIER: "top_tier",
  STRONG: "strong",
};

export default function Applications() {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [openOpp, setOpenOpp] = useState(null);
  const [batchSelected, setBatchSelected] = useState(() => new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState(null);

  function flashToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  }

  async function handleAdded(result) {
    flashToast(`Added: ${result.title} at ${result.employer}`);
    await load();
  }

  const load = useCallback(async () => {
    try {
      const b = await api.applicationsBoard();
      setBoard(b);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const allCards = useMemo(() => {
    if (!board) return [];
    return COLUMNS.flatMap(({ id }) => board.columns[id] || []);
  }, [board]);

  const cardByOpp = useMemo(() => {
    const m = new Map();
    for (const c of allCards) m.set(c.opportunity_id, c);
    return m;
  }, [allCards]);

  async function moveCard(oppId, status) {
    setBusy(true);
    setError(null);
    try {
      await api.applicationsUpdateStatusByOpp(oppId, status);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function toggleBatch(oppId) {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(oppId)) next.delete(oppId);
      else next.add(oppId);
      return next;
    });
  }

  function selectAllDocsReady() {
    if (!board) return;
    const ids = (board.columns.docs_ready || []).map(
      (c) => c.opportunity_id,
    );
    setBatchSelected(new Set(ids));
  }

  function clearBatch() {
    setBatchSelected(new Set());
  }

  async function batchMarkApplied() {
    if (batchSelected.size === 0) return;
    const ok = window.confirm(
      `Mark ${batchSelected.size} posting(s) as applied? Use this ` +
      "for applications submitted outside this dashboard.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      for (const oppId of batchSelected) {
        try {
          await api.applicationsUpdateStatusByOpp(oppId, "applied");
        } catch (e) {
          setError(`opp ${oppId}: ${e.message || e}`);
        }
      }
      clearBatch();
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function batchApplyWithPlaywright() {
    if (batchSelected.size === 0) return;
    const ok = window.confirm(
      `Apply to ${batchSelected.size} positions? Playwright will ` +
      "open each in sequence. You'll review each form before submitting.",
    );
    if (!ok) return;
    const ids = [...batchSelected];
    setBusy(true);
    try {
      for (const oppId of ids) {
        try {
          await api.applyStart(oppId);
        } catch (e) {
          setError(`opp ${oppId}: ${e.message || e}`);
          break;
        }
      }
      clearBatch();
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!board) {
    return (
      <div className="space-y-3">
        {error && (
          <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
            <AlertCircle className="h-4 w-4" />
            <span>{error}</span>
          </Card>
        )}
        <div className="grid grid-cols-6 gap-3">
          {COLUMNS.map((c) => (
            <Skeleton key={c.id} className="h-[280px]" />
          ))}
        </div>
      </div>
    );
  }

  const docsReadyCount = (board.columns.docs_ready || []).length;
  const batchCount = batchSelected.size;

  return (
    <div className="space-y-4">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setError(null)}
            className="ml-auto"
          >
            Dismiss
          </Button>
        </Card>
      )}

      {toast && (
        <Card className="flex items-center gap-2 border-success/40 bg-success/10 p-3 text-sm">
          <CheckCircle2 className="h-4 w-4 text-success" />
          <span>{toast}</span>
        </Card>
      )}

      {/* Top bar: batch actions */}
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">
            {board.stats.total} application{board.stats.total === 1 ? "" : "s"}
          </span>
          <span className="text-xs text-muted-foreground">
            · {board.stats.selected} selected · {board.stats.docs_ready} docs
            ready · {board.stats.applied} applied
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="accent"
              onClick={() => setAddOpen(true)}
            >
              <Plus className="h-3 w-3" />
              Add Job
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={selectAllDocsReady}
              disabled={docsReadyCount === 0 || busy}
              title="Select every Docs Ready posting for batch action"
            >
              <Check className="h-3 w-3" />
              Select Docs Ready ({docsReadyCount})
            </Button>
            {batchCount > 0 && (
              <>
                <Badge variant="accent">{batchCount} selected</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={clearBatch}
                  disabled={busy}
                >
                  <XIcon className="h-3 w-3" />
                  Clear
                </Button>
                <Button
                  size="sm"
                  variant="accent"
                  onClick={batchApplyWithPlaywright}
                  disabled={busy}
                >
                  <Send className="h-3 w-3" />
                  Apply Selected ({batchCount})
                </Button>
                <Button
                  size="sm"
                  variant="success"
                  onClick={batchMarkApplied}
                  disabled={busy}
                >
                  <CheckCircle2 className="h-3 w-3" />
                  Mark All Applied
                </Button>
              </>
            )}
          </div>
        </div>
      </Card>

      {/* Kanban */}
      <div className="grid auto-cols-fr grid-flow-col gap-3 overflow-x-auto pb-4">
        {COLUMNS.map((col) => {
          const cards = board.columns[col.id] || [];
          return (
            <div
              key={col.id}
              className="flex min-w-[240px] flex-col gap-2"
            >
              <div className="flex items-center justify-between px-1">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {col.label}
                </span>
                <Badge variant="muted">{cards.length}</Badge>
              </div>
              <div className="flex max-h-[68vh] flex-col gap-2 overflow-y-auto">
                {cards.length === 0 && (
                  <div className="rounded-md border border-dashed border-border/60 p-3 text-[11px] italic text-muted-foreground">
                    Empty
                  </div>
                )}
                {cards.map((card) => (
                  <KanbanCard
                    key={card.opportunity_id}
                    card={card}
                    selectable={col.id === "docs_ready"}
                    selected={batchSelected.has(card.opportunity_id)}
                    onToggleBatch={() =>
                      toggleBatch(card.opportunity_id)
                    }
                    onOpen={() => setOpenOpp(card.opportunity_id)}
                    onMove={(s) => moveCard(card.opportunity_id, s)}
                    disabled={busy}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {openOpp != null && cardByOpp.has(openOpp) && (
        <ExpandedDialog
          card={cardByOpp.get(openOpp)}
          allCards={allCards}
          onClose={() => setOpenOpp(null)}
          onNavigate={(oppId) => setOpenOpp(oppId)}
          onReload={load}
          onMove={(s) => moveCard(openOpp, s)}
        />
      )}

      <AddJobModal
        open={addOpen}
        onOpenChange={setAddOpen}
        onAdded={handleAdded}
      />
    </div>
  );
}

function KanbanCard({
  card, selectable, selected, onToggleBatch,
  onOpen, onMove, disabled,
}) {
  const borderClass = TIER_BORDER[card.tier] || "border-l-border";
  return (
    <Card
      className={cn(
        "border-l-4 p-3 text-xs transition-colors hover:border-accent/60",
        borderClass,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-mono text-muted-foreground">
            #{card.opportunity_id}
          </div>
          <div className="truncate text-sm font-semibold">
            {card.title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {card.employer}
          </div>
        </div>
        {selectable && (
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleBatch}
            disabled={disabled}
            className="h-4 w-4 cursor-pointer"
            title="Add to batch action"
          />
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {card.tier && (
          <Badge variant={TIER_VARIANT[card.tier] || "outline"}>
            {card.tier}
          </Badge>
        )}
        {card.fit_score != null && (
          <span className="font-mono tabular-nums">
            FIT {card.fit_score}
          </span>
        )}
        {card.location && (
          <span className="truncate">· {card.location}</span>
        )}
      </div>
      <div className="mt-2 flex gap-1 text-[11px]">
        <Badge variant={card.resume_saved ? "success" : "muted"}>
          {card.resume_saved && <Check className="h-3 w-3" />}
          Resume
        </Badge>
        <Badge variant={card.cover_letter_saved ? "success" : "muted"}>
          {card.cover_letter_saved && <Check className="h-3 w-3" />}
          Cover Letter
        </Badge>
      </div>
      <div className="mt-2 flex items-center gap-1">
        <Button
          size="sm"
          variant="outline"
          onClick={onOpen}
          className="flex-1"
          disabled={disabled}
        >
          Open
          <ArrowRight className="h-3 w-3" />
        </Button>
        <StatusMenu
          current={card.status}
          onPick={onMove}
          disabled={disabled}
        />
      </div>
    </Card>
  );
}

function StatusMenu({ current, onPick, disabled }) {
  const [open, setOpen] = useState(false);
  const targets = COLUMNS.filter(
    (c) => c.id !== current && c.id !== "selected",
  );
  return (
    <div className="relative">
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        title="Move to..."
        className="px-2"
      >
        ⋮
      </Button>
      {open && (
        <div
          className="absolute right-0 z-10 mt-1 w-40 rounded-md border border-border bg-popover p-1 shadow-lg"
          onMouseLeave={() => setOpen(false)}
        >
          {targets.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setOpen(false);
                onPick(t.id);
              }}
              className="block w-full rounded px-2 py-1 text-left text-xs hover:bg-muted"
            >
              → {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ExpandedDialog({
  card, allCards, onClose, onNavigate, onReload, onMove,
}) {
  const idx = allCards.findIndex(
    (c) => c.opportunity_id === card.opportunity_id,
  );
  const prev = idx > 0 ? allCards[idx - 1] : null;
  const next = idx >= 0 && idx < allCards.length - 1
    ? allCards[idx + 1] : null;

  return (
    <Dialog open={true} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] w-[min(1100px,96vw)] max-w-none overflow-y-auto p-0">
        <DialogHeader className="border-b border-border p-4">
          <DialogTitle className="flex flex-wrap items-center gap-2 text-base">
            <span className="font-mono text-xs text-muted-foreground">
              #{card.opportunity_id}
            </span>
            <span>{card.title}</span>
            <span className="font-normal text-muted-foreground">
              — {card.employer}
            </span>
            {card.tier && (
              <Badge variant={TIER_VARIANT[card.tier] || "outline"}>
                {card.tier}
              </Badge>
            )}
            {card.fit_score != null && (
              <span className="font-mono text-sm tabular-nums">
                {card.fit_score}/10
              </span>
            )}
            <div className="ml-auto flex items-center gap-2">
              {card.source_url && (
                <Button asChild size="sm" variant="outline">
                  <a
                    href={card.source_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ExternalLink className="h-3 w-3" />
                    View Posting
                  </a>
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={onClose}>
                <XIcon className="h-3 w-3" />
              </Button>
            </div>
          </DialogTitle>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {card.location && <span>{card.location}</span>}
            {card.source && <span>· {card.source}</span>}
            {card.posted_at && <span>· {card.posted_at}</span>}
          </div>
        </DialogHeader>
        <div className="space-y-4 p-4">
          <StatusBreadcrumb
            current={card.status}
            onPick={(s) => onMove(s)}
          />

          <DocSection
            postingId={card.opportunity_id}
            kind="resume"
            savedMarkdown={card.resume_markdown}
            savedAt={card.resume_saved_at}
            onSaved={onReload}
          />

          <DocSection
            postingId={card.opportunity_id}
            kind="cover-letter"
            savedMarkdown={card.cover_letter_markdown}
            savedAt={card.cover_letter_saved_at}
            onSaved={onReload}
          />

          <ApplySection
            card={card}
            onReload={onReload}
            onMarkApplied={() => onMove("applied")}
          />

          <NotesSection
            postingId={card.opportunity_id}
            initial={card.notes}
          />

          <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
            <Button
              size="sm"
              variant="outline"
              disabled={!prev}
              onClick={() =>
                prev && onNavigate(prev.opportunity_id)
              }
            >
              <ChevronLeft className="h-3 w-3" />
              {prev
                ? `Previous: #${prev.opportunity_id} ${prev.employer}`
                : "First card"}
            </Button>
            <span className="text-xs text-muted-foreground">
              {idx + 1} of {allCards.length}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={!next}
              onClick={() =>
                next && onNavigate(next.opportunity_id)
              }
            >
              {next
                ? `Next: #${next.opportunity_id} ${next.employer}`
                : "Last card"}
              <ChevronRight className="h-3 w-3" />
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StatusBreadcrumb({ current, onPick }) {
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-md border border-border bg-muted/30 p-2 text-xs">
      {COLUMNS.map((c, i) => {
        const active = c.id === current;
        return (
          <button
            key={c.id}
            onClick={() => onPick(c.id)}
            className={cn(
              "rounded px-2 py-1 transition-colors",
              active
                ? "bg-accent/20 font-semibold text-foreground"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            {c.label}
            {i < COLUMNS.length - 1 && (
              <span className="ml-1 text-muted-foreground">›</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function DocSection({ postingId, kind, savedMarkdown, savedAt, onSaved }) {
  const label = kind === "resume" ? "Resume" : "Cover Letter";
  const promptFn = kind === "resume"
    ? api.getResumePrompt : api.getCoverLetterPrompt;
  const saveFn = kind === "resume"
    ? api.saveResumeText : api.saveCoverLetterText;
  const downloadKind = kind === "resume" ? "resume" : "cover-letter";

  const [paste, setPaste] = useState(savedMarkdown || "");
  const [savedAtLocal, setSavedAtLocal] = useState(savedAt || null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [prompt, setPrompt] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setPaste(savedMarkdown || "");
    setSavedAtLocal(savedAt || null);
  }, [postingId, kind, savedMarkdown, savedAt]);

  async function copyPrompt() {
    setError(null);
    try {
      const r = prompt || (await promptFn(postingId));
      setPrompt(r);
      await navigator.clipboard.writeText(r.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  async function save() {
    if (!paste.trim()) {
      setError(`Paste Claude's ${label.toLowerCase()} response first.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const r = await saveFn(postingId, paste);
      setSavedAtLocal(
        r.docs_ready_at || new Date().toISOString(),
      );
      onSaved?.();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  function preview() {
    window.open(
      `/api/preview/${postingId}/${downloadKind}.docx`,
      "_blank",
      "noopener",
    );
  }
  function download() {
    window.location.href =
      `/api/download/${postingId}/${downloadKind}.docx`;
  }

  return (
    <Card className="p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-sm font-semibold">
          <FileText className="h-4 w-4 text-muted-foreground" />
          {label}
        </span>
        <div className="flex flex-wrap gap-2">
          {savedAtLocal && (
            <Badge variant="success">
              <Check className="h-3 w-3" />
              Saved {new Date(savedAtLocal).toLocaleString()}
            </Badge>
          )}
          <Button size="sm" variant="outline" onClick={copyPrompt}>
            <Copy className="h-3 w-3" />
            {copied ? "Copied!" : "Copy Prompt"}
          </Button>
        </div>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Textarea
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder={`Paste Claude's ${label.toLowerCase()} markdown here…`}
          spellCheck={false}
          className="min-h-[260px] font-mono text-xs"
        />
        <MarkdownPreview source={paste} className="min-h-[260px]" />
      </div>
      {error && (
        <div className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          variant="accent"
          size="sm"
          onClick={save}
          disabled={saving || !paste.trim()}
        >
          {saving ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Check className="h-3 w-3" />
          )}
          Save {label}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={preview}
          disabled={!savedAtLocal}
        >
          Preview .docx
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={download}
          disabled={!savedAtLocal}
        >
          <Download className="h-3 w-3" />
          Download .docx
        </Button>
      </div>
    </Card>
  );
}

function ApplySection({ card, onReload, onMarkApplied }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ready = card.resume_saved && card.cover_letter_saved;

  async function smartFill() {
    setBusy(true);
    setError(null);
    try {
      await api.applyStart(card.opportunity_id);
      window.alert(
        "Playwright browser opened. Sign in (if needed), review the " +
        "filled form, then submit manually in the browser.",
      );
      await onReload?.();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <Send className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-semibold">Apply</span>
      </div>
      {!ready && (
        <div className="mb-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-xs">
          Save resume + cover letter first to enable Smart Fill.
        </div>
      )}
      {error && (
        <div className="mb-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="accent"
          size="sm"
          onClick={smartFill}
          disabled={busy || !ready}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Cpu className="h-3 w-3" />
          )}
          Smart Fill (Playwright)
        </Button>
        <Button
          variant="success"
          size="sm"
          onClick={onMarkApplied}
          disabled={busy}
        >
          <CheckCircle2 className="h-3 w-3" />
          Mark Applied
        </Button>
        {card.source_url && (
          <Button asChild variant="outline" size="sm">
            <a href={card.source_url} target="_blank" rel="noreferrer">
              <ExternalLink className="h-3 w-3" />
              Open in browser
            </a>
          </Button>
        )}
      </div>
    </Card>
  );
}

function NotesSection({ postingId, initial }) {
  const [notes, setNotes] = useState(initial || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setNotes(initial || "");
    setSaved(false);
  }, [postingId, initial]);

  async function saveOnBlur() {
    if ((initial || "") === notes) return;
    setSaving(true);
    setError(null);
    try {
      await api.applicationsUpdateNotesByOpp(postingId, notes);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <Mail className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-semibold">Notes</span>
        {saving && (
          <Badge variant="muted">
            <Loader2 className="h-3 w-3 animate-spin" />
            Saving…
          </Badge>
        )}
        {saved && !saving && (
          <Badge variant="success">
            <Check className="h-3 w-3" />
            Saved
          </Badge>
        )}
      </div>
      <Textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={saveOnBlur}
        placeholder="Notes — auto-saves when you click away."
        spellCheck={false}
        className="min-h-[100px] text-sm"
      />
      {error && (
        <div className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </Card>
  );
}
