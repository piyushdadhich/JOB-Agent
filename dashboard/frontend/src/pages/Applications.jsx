import {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
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
  Sparkles,
  X as XIcon,
} from "lucide-react";
import { api } from "@/api";
import { useTask } from "@/contexts/TaskContext";
import AddJobModal from "@/components/AddJobModal.jsx";
import MarkdownPreview from "@/components/MarkdownPreview.jsx";
import StatusBoard from "@/components/applications/StatusBoard.jsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/input";
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

// Status buckets, in queue order. `selected` = curated pool with no
// docs yet; `docs_ready` = both resume + cover letter saved.
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
const TIER_VARIANT = { TOP_TIER: "top_tier", STRONG: "strong" };

// Queue sort: pending docs first (work to do), then ready, then the
// rest; fit score breaks ties.
const STATUS_RANK = {
  selected: 0, docs_ready: 1, applied: 2,
  interview: 3, offer: 4, rejected: 5,
};

export default function Applications() {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState("queue"); // queue | board
  const [selectedOpp, setSelectedOpp] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [usage, setUsage] = useState(null);

  const flashToast = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  }, []);

  const load = useCallback(async () => {
    try {
      const b = await api.applicationsBoard();
      setBoard(b);
      return b;
    } catch (e) {
      setError(e.message || String(e));
      return null;
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      setUsage(await api.cloudUsageToday());
    } catch {
      /* usage badge is best-effort */
    }
  }, []);

  useEffect(() => {
    load();
    loadUsage();
  }, [load, loadUsage]);

  const allCards = useMemo(() => {
    if (!board) return [];
    const flat = COLUMNS.flatMap(({ id }) => board.columns[id] || []);
    return flat.slice().sort((a, b) => {
      const r = STATUS_RANK[a.status] - STATUS_RANK[b.status];
      if (r !== 0) return r;
      return (b.fit_score ?? 0) - (a.fit_score ?? 0);
    });
  }, [board]);

  // Default-select the first card once the board loads.
  useEffect(() => {
    if (selectedOpp == null && allCards.length > 0) {
      setSelectedOpp(allCards[0].opportunity_id);
    }
  }, [allCards, selectedOpp]);

  const cardByOpp = useMemo(() => {
    const m = new Map();
    for (const c of allCards) m.set(c.opportunity_id, c);
    return m;
  }, [allCards]);

  async function handleAdded(result) {
    flashToast(`Added: ${result.title} at ${result.employer}`);
    await load();
    setSelectedOpp(result.opportunity_id);
    setView("queue");
  }

  async function afterMutation() {
    await load();
    await loadUsage();
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
        <Skeleton className="h-[70vh] w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
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

      <div className="flex flex-wrap items-center gap-3">
        <ToggleGroup
          type="single"
          value={view}
          onValueChange={(v) => v && setView(v)}
          aria-label="View"
        >
          <ToggleGroupItem value="queue">Process Queue</ToggleGroupItem>
          <ToggleGroupItem value="board">Status Board</ToggleGroupItem>
        </ToggleGroup>
        <UsageBadge usage={usage} />
        <div className="ml-auto">
          <Button
            size="sm"
            variant="accent"
            onClick={() => setAddOpen(true)}
          >
            <Plus className="h-3 w-3" />
            Add Job
          </Button>
        </div>
      </div>

      {view === "queue" ? (
        <ProcessQueue
          board={board}
          allCards={allCards}
          cardByOpp={cardByOpp}
          selectedOpp={selectedOpp}
          setSelectedOpp={setSelectedOpp}
          onReload={afterMutation}
          onError={setError}
          flashToast={flashToast}
        />
      ) : (
        <StatusBoard
          board={board}
          onOpenItem={(oppId) => {
            setSelectedOpp(oppId);
            setView("queue");
          }}
          onReload={afterMutation}
          onError={setError}
          flashToast={flashToast}
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

function UsageBadge({ usage }) {
  if (!usage) return null;
  const remaining = usage.remaining ?? 0;
  const low = remaining < 100;
  const b = usage.breakdown || {};
  return (
    <span
      className={cn(
        "flex items-center gap-1 rounded-md border px-2 py-1 text-xs",
        low
          ? "border-destructive/40 bg-destructive/10 text-destructive-foreground"
          : "border-border bg-muted/40 text-muted-foreground",
      )}
      title={
        `Evaluation: ${b.eval || 0} · ` +
        `Resume: ${b.resume || 0} · ` +
        `Cover letter: ${b.cover_letter || 0}`
      }
    >
      ☁️ API: {usage.total_used}/{usage.daily_limit} today ·{" "}
      {remaining} left
    </span>
  );
}

// --- Process Queue (split-pane) ----------------------------------

function ProcessQueue({
  board, allCards, cardByOpp, selectedOpp, setSelectedOpp,
  onReload, onError, flashToast,
}) {
  const { activeTask, startTask } = useTask();
  const [batchBusy, setBatchBusy] = useState(false);
  const [genNonce, setGenNonce] = useState(0);
  const [genBusy, setGenBusy] = useState(false);

  const batchRunning = activeTask?.status === "running";

  const docsReady = board.stats.docs_ready
    + board.stats.applied + board.stats.interview
    + board.stats.offer;
  const total = board.stats.total;
  const pct = total > 0 ? Math.round((docsReady / total) * 100) : 0;

  const remaining = useMemo(
    () => allCards.filter(
      (c) => !(c.resume_saved && c.cover_letter_saved),
    ).length,
    [allCards],
  );

  const selectedCard =
    selectedOpp != null ? cardByOpp.get(selectedOpp) : null;
  const selectedIdx = allCards.findIndex(
    (c) => c.opportunity_id === selectedOpp,
  );

  // The global TaskContext owns the poll loop; reload the board as
  // the batch advances so queue checkmarks track completion.
  useEffect(() => {
    if (activeTask) onReload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.completed, activeTask?.status]);

  // force=false: fill only postings missing docs.
  // force=true: regenerate every shortlisted posting, replacing
  // existing content (e.g. after updating contact details).
  async function runGenerateAll(force) {
    const poolSize =
      board.stats.selected + board.stats.docs_ready;
    if (!force && remaining === 0) return;
    if (force && poolSize === 0) return;
    const ok = window.confirm(
      force
        ? `Regenerate all ${poolSize} resumes and cover letters? ` +
          "This replaces existing content with fresh generations " +
          "using your updated contact details."
        : `Generate resume + cover letter for ${remaining} jobs?\n` +
          `Uses ~${remaining * 2} Google AI Studio calls.`,
    );
    if (!ok) return;
    try {
      const start = await api.applicationsGenerateAll(
        ["resume", "cover_letter"], force,
      );
      // Hand the task to the global context — progress now shows in
      // the app-wide banner and survives navigation.
      startTask(
        "generate-all",
        force ? "Regenerating resumes" : "Generating resumes",
        start.task_id,
        start.total,
      );
    } catch (e) {
      onError(e.message || String(e));
    }
  }

  const generateAll = () => runGenerateAll(false);
  const regenerateAll = () => runGenerateAll(true);

  async function batchApplyReady() {
    const ready = allCards.filter(
      (c) => c.status === "docs_ready",
    );
    if (ready.length === 0) return;
    const ok = window.confirm(
      `Apply to ${ready.length} ready position(s)? Playwright ` +
      "opens each in sequence — review each form before submitting.",
    );
    if (!ok) return;
    setBatchBusy(true);
    try {
      for (const c of ready) {
        try {
          await api.applyStart(c.opportunity_id);
        } catch (e) {
          onError(`opp ${c.opportunity_id}: ${e.message || e}`);
          break;
        }
      }
      await onReload();
    } finally {
      setBatchBusy(false);
    }
  }

  function selectByIndex(idx) {
    if (idx < 0 || idx >= allCards.length) return;
    setSelectedOpp(allCards[idx].opportunity_id);
  }

  // G shortcut: generate resume + cover letter for the selected job,
  // then remount the detail pane (bump genNonce) so both textareas
  // re-seed from the freshly-saved markdown.
  async function generateBoth(oppId) {
    if (oppId == null || genBusy) return;
    setGenBusy(true);
    try {
      for (const t of ["resume", "cover_letter"]) {
        try {
          await api.applicationsGenerateDoc(oppId, t);
        } catch (e) {
          onError(`opp ${oppId} ${t}: ${e.message || e}`);
        }
      }
      await onReload();
      setGenNonce((n) => n + 1);
    } finally {
      setGenBusy(false);
    }
  }

  // Keyboard navigation. Arrows / J / K move the selection; G
  // generates; Escape blurs. Disabled while a textarea/input is
  // focused so typing (and Ctrl+S inside a doc section) is intact.
  useEffect(() => {
    function onKey(e) {
      const tag = document.activeElement?.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      const idx = allCards.findIndex(
        (c) => c.opportunity_id === selectedOpp,
      );
      if (e.key === "ArrowUp" || e.key === "k" || e.key === "K") {
        e.preventDefault();
        selectByIndex(idx - 1);
      } else if (e.key === "ArrowDown" || e.key === "j" || e.key === "J") {
        e.preventDefault();
        selectByIndex(idx + 1);
      } else if (e.key === "g" || e.key === "G") {
        e.preventDefault();
        generateBoth(selectedOpp);
      } else if (e.key === "Escape") {
        document.activeElement?.blur?.();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allCards, selectedOpp, genBusy]);

  return (
    <div
      className="flex gap-3 overflow-hidden"
      style={{ height: "calc(100vh - 12rem)" }}
    >
      {/* Left pane — queue list */}
      <div className="flex h-full w-[30%] min-w-[260px] flex-col gap-2">
        <Card className="p-3">
          <div className="text-xs font-medium text-muted-foreground">
            Progress: {docsReady}/{total} docs ready
          </div>
          <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-success transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="mt-0.5 text-right text-[10px] text-muted-foreground">
            {pct}%
          </div>
          <Button
            size="sm"
            variant="accent"
            className="mt-2 w-full"
            onClick={generateAll}
            disabled={remaining === 0 || batchRunning}
          >
            {batchRunning ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Sparkles className="h-3 w-3" />
            )}
            Generate All ({remaining} remaining)
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="mt-1.5 w-full"
            onClick={regenerateAll}
            disabled={batchRunning}
            title="Regenerate every posting, replacing existing docs"
          >
            <Sparkles className="h-3 w-3" />
            Regenerate All
          </Button>
        </Card>

        <div className="flex-1 space-y-1.5 overflow-y-auto pr-1">
          {allCards.length === 0 && (
            <Card className="border-dashed p-4 text-center text-xs text-muted-foreground">
              No applications yet. Use "Add Job" or select postings
              on the Shortlist tab.
            </Card>
          )}
          {allCards.map((card) => (
            <QueueItem
              key={card.opportunity_id}
              card={card}
              active={card.opportunity_id === selectedOpp}
              generatingNow={
                batchRunning
                && activeTask.current_opp_id === card.opportunity_id
              }
              onClick={() => setSelectedOpp(card.opportunity_id)}
            />
          ))}
        </div>

        <Button
          size="sm"
          variant="success"
          onClick={batchApplyReady}
          disabled={batchBusy || board.stats.docs_ready === 0}
        >
          {batchBusy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Send className="h-3 w-3" />
          )}
          Apply All Ready ({board.stats.docs_ready})
        </Button>
        <div className="text-center text-[10px] text-muted-foreground">
          ↑↓/JK navigate · G generate · Ctrl+S save · Esc blur
          {genBusy && " · generating…"}
        </div>
      </div>

      {/* Right pane — detail */}
      <div className="h-full flex-1 overflow-y-auto">
        {selectedCard ? (
          <DetailPane
            key={`${selectedCard.opportunity_id}:${genNonce}`}
            card={selectedCard}
            hasPrev={selectedIdx > 0}
            hasNext={selectedIdx < allCards.length - 1}
            onPrev={() => selectByIndex(selectedIdx - 1)}
            onNext={() => selectByIndex(selectedIdx + 1)}
            prevCard={allCards[selectedIdx - 1]}
            nextCard={allCards[selectedIdx + 1]}
            onReload={onReload}
            onError={onError}
            onSelectIndex={selectByIndex}
            selectedIdx={selectedIdx}
            queueLength={allCards.length}
          />
        ) : (
          <Card className="flex h-full items-center justify-center p-10 text-sm text-muted-foreground">
            Select a job from the queue.
          </Card>
        )}
      </div>
    </div>
  );
}

function QueueItem({ card, active, generatingNow = false, onClick }) {
  const done = card.resume_saved && card.cover_letter_saved;
  const borderClass = generatingNow
    ? "border-l-accent"
    : active
      ? "border-l-accent"
      : done
        ? "border-l-success"
        : TIER_BORDER[card.tier] || "border-l-warning";
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-md border border-l-4 p-2 text-left text-xs transition-colors",
        borderClass,
        active
          ? "bg-accent/10 ring-1 ring-accent/40"
          : "bg-card hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="shrink-0">
          {generatingNow ? (
            <Loader2 className="inline h-3 w-3 animate-spin text-accent" />
          ) : active ? "▶" : done ? "✅" : "⬜"}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">
          #{card.opportunity_id}
        </span>
        <span className="truncate font-semibold">{card.employer}</span>
      </div>
      <div className="mt-0.5 truncate text-muted-foreground">
        {card.title}
        {card.fit_score != null && ` · FIT ${card.fit_score}`}
      </div>
      <div className="mt-1 flex gap-2 text-[10px]">
        <span className={card.resume_saved ? "text-success" : "text-muted-foreground"}>
          {card.resume_saved ? "✓" : "⬜"} Resume
        </span>
        <span className={card.cover_letter_saved ? "text-success" : "text-muted-foreground"}>
          {card.cover_letter_saved ? "✓" : "⬜"} Cover
        </span>
      </div>
    </button>
  );
}

function DetailPane({
  card, hasPrev, hasNext, onPrev, onNext, prevCard, nextCard,
  onReload, onError, onSelectIndex, selectedIdx, queueLength,
}) {
  const ready = card.resume_saved && card.cover_letter_saved;
  return (
    <Card className="space-y-4 p-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border pb-3">
        <div className="min-w-0">
          <div className="font-mono text-[11px] text-muted-foreground">
            #{card.opportunity_id}
          </div>
          <div className="text-base font-semibold">
            {card.title}
            <span className="font-normal text-muted-foreground">
              {" "}· {card.employer}
            </span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {card.location && <span>{card.location}</span>}
            {card.fit_score != null && (
              <span>· FIT {card.fit_score}</span>
            )}
            {card.tier && (
              <Badge variant={TIER_VARIANT[card.tier] || "outline"}>
                {card.tier}
              </Badge>
            )}
          </div>
        </div>
        {card.source_url && (
          <Button asChild size="sm" variant="outline">
            <a href={card.source_url} target="_blank" rel="noreferrer">
              <ExternalLink className="h-3 w-3" />
              View Posting
            </a>
          </Button>
        )}
      </div>

      <DocSection
        oppId={card.opportunity_id}
        kind="resume"
        savedMarkdown={card.resume_markdown}
        savedAt={card.resume_saved_at}
        onReload={onReload}
        onError={onError}
      />
      <DocSection
        oppId={card.opportunity_id}
        kind="cover-letter"
        savedMarkdown={card.cover_letter_markdown}
        savedAt={card.cover_letter_saved_at}
        onReload={onReload}
        onError={onError}
      />

      {ready && (
        <ApplySection card={card} onReload={onReload} onError={onError} />
      )}

      <NotesSection oppId={card.opportunity_id} initial={card.notes} />

      <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
        <Button
          size="sm"
          variant="outline"
          disabled={!hasPrev}
          onClick={onPrev}
        >
          <ChevronLeft className="h-3 w-3" />
          {prevCard
            ? `#${prevCard.opportunity_id} ${prevCard.employer}`
            : "First"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {selectedIdx + 1} of {queueLength}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={!hasNext}
          onClick={onNext}
        >
          {nextCard
            ? `#${nextCard.opportunity_id} ${nextCard.employer}`
            : "Last"}
          <ChevronRight className="h-3 w-3" />
        </Button>
      </div>
    </Card>
  );
}

// One resume / cover-letter section: Generate, Copy Prompt, paste,
// live preview, save. Save never blanks — the textarea keeps its
// content and a "Saved" badge appears.
function DocSection({
  oppId, kind, savedMarkdown, savedAt, onReload, onError,
}) {
  const label = kind === "resume" ? "Resume" : "Cover Letter";
  const generateType = kind === "resume" ? "resume" : "cover_letter";
  const promptFn = kind === "resume"
    ? api.getResumePrompt : api.getCoverLetterPrompt;
  const saveFn = kind === "resume"
    ? api.saveResumeText : api.saveCoverLetterText;

  const [paste, setPaste] = useState(savedMarkdown || "");
  const [lastSaved, setLastSaved] = useState(savedMarkdown || "");
  const [savedAtLocal, setSavedAtLocal] = useState(savedAt || null);
  const [generating, setGenerating] = useState(false);
  const [genElapsed, setGenElapsed] = useState(0);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [localErr, setLocalErr] = useState(null);
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptText, setPromptText] = useState(null);
  const [promptLoading, setPromptLoading] = useState(false);
  const taRef = useRef(null);
  const genTimerRef = useRef(null);

  // Tick an elapsed-seconds counter while a generation is in flight.
  useEffect(() => {
    if (generating) {
      setGenElapsed(0);
      genTimerRef.current = setInterval(
        () => setGenElapsed((s) => s + 1), 1000,
      );
    } else if (genTimerRef.current) {
      clearInterval(genTimerRef.current);
      genTimerRef.current = null;
    }
    return () => {
      if (genTimerRef.current) clearInterval(genTimerRef.current);
    };
  }, [generating]);

  // Seed ONLY on item switch (oppId/kind), never on a board refresh —
  // a refresh after save must not clobber in-progress edits.
  useEffect(() => {
    setPaste(savedMarkdown || "");
    setLastSaved(savedMarkdown || "");
    setSavedAtLocal(savedAt || null);
    setLocalErr(null);
    setPromptOpen(false);
    setPromptText(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oppId, kind]);

  async function ensurePrompt() {
    if (promptText != null) return promptText;
    setPromptLoading(true);
    try {
      const r = await promptFn(oppId);
      setPromptText(r.prompt);
      return r.prompt;
    } finally {
      setPromptLoading(false);
    }
  }

  async function togglePrompt() {
    const next = !promptOpen;
    setPromptOpen(next);
    if (next && promptText == null) {
      try {
        await ensurePrompt();
      } catch (e) {
        setLocalErr(e.message || String(e));
      }
    }
  }

  const dirty = paste !== lastSaved;

  // Generate is always available. When content already exists the
  // button reads "Regenerate" and confirms before replacing — the
  // freshly generated text comes back UNSAVED so the user reviews
  // it before it overwrites the saved copy.
  async function generate() {
    const hasContent = paste.trim().length > 0;
    if (hasContent) {
      const ok = window.confirm(
        `Regenerate ${label.toLowerCase()}? This replaces the ` +
        "current content — review and save before it sticks.",
      );
      if (!ok) return;
    }
    setGenerating(true);
    setLocalErr(null);
    try {
      const r = await api.applicationsGenerateDoc(oppId, generateType);
      setPaste(r.markdown);
      if (hasContent) {
        // Regeneration: leave it dirty so the user reviews + saves.
        setLastSaved("");
      } else {
        setLastSaved(r.markdown);
        setSavedAtLocal(r.docs_ready_at || new Date().toISOString());
      }
      await onReload?.();
    } catch (e) {
      setLocalErr(e.message || String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function copyPrompt() {
    setLocalErr(null);
    try {
      const text = await ensurePrompt();
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setLocalErr(e.message || String(e));
    }
  }

  async function save() {
    if (!paste.trim()) {
      setLocalErr(`Generate or paste a ${label.toLowerCase()} first.`);
      return;
    }
    setSaving(true);
    setLocalErr(null);
    try {
      const r = await saveFn(oppId, paste);
      setLastSaved(paste);
      setSavedAtLocal(r.docs_ready_at || new Date().toISOString());
      await onReload?.();
    } catch (e) {
      setLocalErr(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  // Auto-save safety net: if the user clicks away with unsaved
  // edits, persist them so switching items never loses work.
  function onBlur() {
    if (dirty && paste.trim() && !saving) save();
  }

  function onKeyDown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      taRef.current?.blur();
    }
  }

  function preview() {
    window.open(`/api/preview/${oppId}/${kind}.docx`, "_blank", "noopener");
  }
  function download() {
    window.location.href = `/api/download/${oppId}/${kind}.docx`;
  }

  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-sm font-semibold">
          <FileText className="h-4 w-4 text-muted-foreground" />
          {label}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {savedAtLocal && !dirty && (
            <Badge variant="success">
              <Check className="h-3 w-3" />
              Saved {new Date(savedAtLocal).toLocaleTimeString()}
            </Badge>
          )}
          {dirty && (
            <Badge variant="warning">Unsaved changes</Badge>
          )}
          <Button
            size="sm"
            variant={paste.trim() ? "outline" : "accent"}
            onClick={generate}
            disabled={generating}
            data-generate={kind}
          >
            {generating ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Sparkles className="h-3 w-3" />
            )}
            {generating
              ? "Generating…"
              : paste.trim()
                ? "Regenerate"
                : "Generate"}
          </Button>
          <Button size="sm" variant="outline" onClick={copyPrompt}>
            <Copy className="h-3 w-3" />
            {copied ? "Copied!" : "Copy Prompt"}
          </Button>
        </div>
      </div>
      {/* Collapsible: the exact prompt sent to Gemma. */}
      <div className="mb-2 rounded-md border border-border bg-muted/30">
        <button
          type="button"
          onClick={togglePrompt}
          className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] font-medium text-muted-foreground hover:text-foreground"
          aria-expanded={promptOpen}
        >
          {promptOpen ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          {promptOpen ? "Hide" : "View"} prompt sent to Gemma
          {promptLoading && (
            <Loader2 className="h-3 w-3 animate-spin" />
          )}
        </button>
        {promptOpen && (
          <pre className="max-h-[260px] overflow-auto border-t border-border px-3 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
            {promptText ?? "Loading prompt…"}
          </pre>
        )}
      </div>

      {generating && (
        <div className="mb-2 rounded-md border border-accent/40 bg-accent/5 p-3">
          <div className="flex items-center gap-2 text-xs font-medium">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
            Generating {label.toLowerCase()} via Gemma… {genElapsed}s
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-accent" />
          </div>
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Textarea
          ref={taRef}
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          readOnly={generating}
          placeholder={
            generating
              ? `Generating ${label.toLowerCase()} via Gemma…`
              : `Generate, or paste Claude's ${label.toLowerCase()} markdown…`
          }
          spellCheck={false}
          className="min-h-[240px] font-mono text-xs"
        />
        <MarkdownPreview source={paste} className="min-h-[240px]" />
      </div>
      {localErr && (
        <div className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{localErr}</span>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="accent"
          onClick={save}
          disabled={saving || !paste.trim()}
        >
          {saving ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Check className="h-3 w-3" />
          )}
          Save
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={preview}
          disabled={!savedAtLocal}
        >
          Preview .docx
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={download}
          disabled={!savedAtLocal}
        >
          <Download className="h-3 w-3" />
          Download .docx
        </Button>
      </div>
    </div>
  );
}

function ApplySection({ card, onReload, onError }) {
  const [busy, setBusy] = useState(false);

  async function smartFill() {
    setBusy(true);
    try {
      const r = await api.applyStart(card.opportunity_id);
      if (r && r.state === "linkedin_easy_apply") {
        window.alert(
          r.detail ||
            "This is LinkedIn Easy Apply — open it manually in your " +
            "browser, submit, then click Mark Applied.",
        );
      } else {
        window.alert(
          "Playwright browser opened. Sign in if needed, review the " +
          "filled form, then submit manually in the browser.",
        );
      }
      await onReload?.();
    } catch (e) {
      onError?.(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function markApplied() {
    setBusy(true);
    try {
      await api.applicationsUpdateStatusByOpp(
        card.opportunity_id, "applied",
      );
      await onReload?.();
    } catch (e) {
      onError?.(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-2 flex items-center gap-2">
        <Send className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-semibold">Apply</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="accent"
          onClick={smartFill}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Cpu className="h-3 w-3" />
          )}
          Smart Fill (Playwright)
        </Button>
        <Button
          size="sm"
          variant="success"
          onClick={markApplied}
          disabled={busy}
        >
          <CheckCircle2 className="h-3 w-3" />
          Mark Applied
        </Button>
        <Button asChild size="sm" variant="outline">
          <a
            href={`/api/download/${card.opportunity_id}/resume.docx`}
          >
            <Download className="h-3 w-3" />
            Resume
          </a>
        </Button>
        <Button asChild size="sm" variant="outline">
          <a
            href={`/api/download/${card.opportunity_id}/cover-letter.docx`}
          >
            <Download className="h-3 w-3" />
            Cover Letter
          </a>
        </Button>
      </div>
    </div>
  );
}

function NotesSection({ oppId, initial }) {
  const [notes, setNotes] = useState(initial || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setNotes(initial || "");
    setSaved(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oppId]);

  async function saveOnBlur() {
    if ((initial || "") === notes) return;
    setSaving(true);
    setErr(null);
    try {
      await api.applicationsUpdateNotesByOpp(oppId, notes);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-md border border-border p-3">
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
        className="min-h-[80px] text-sm"
      />
      {err && (
        <div className="mt-2 text-xs text-destructive-foreground">
          {err}
        </div>
      )}
    </div>
  );
}
