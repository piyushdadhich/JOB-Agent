import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Copy,
  Cpu,
  ExternalLink,
  Loader2,
  Send,
  SkipForward,
  StopCircle,
  ThumbsUp,
  X as XIcon,
} from "lucide-react";
import { api } from "@/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const POLL_MS = 1500;

function isLinkedIn(row) {
  if (row?.ats_platform && row.ats_platform.toLowerCase() === "linkedin") {
    return true;
  }
  return /linkedin\.com\/jobs\/view/i.test(row?.source_url || "");
}

const SESSION_TONE = {
  starting: { variant: "muted", label: "Launching browser…" },
  filling: { variant: "accent", label: "Filling form…" },
  awaiting_plan_approval: { variant: "warning", label: "Plan ready — review" },
  ready_for_submit: { variant: "warning", label: "Ready for review" },
  submitting: { variant: "accent", label: "Submitting…" },
  submitted: { variant: "success", label: "Submitted" },
  aborted: { variant: "destructive", label: "Aborted" },
  error: { variant: "destructive", label: "Error" },
  not_supported: { variant: "muted", label: "ATS not supported" },
};

const ACTIVE_STATES = new Set([
  "starting", "filling", "awaiting_plan_approval",
  "ready_for_submit", "submitting",
]);

export default function Apply() {
  const [ready, setReady] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [batch, setBatch] = useState(null);
  const pollRef = useRef(null);

  async function refreshList() {
    try {
      const rows = await api.listApplications({ stage: "ready_to_apply" });
      setReady(rows);
    } catch (e) {
      setError(String(e));
    }
  }

  async function refreshStatus() {
    try {
      const r = await api.applyStatus();
      setSession(r.session || null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    refreshList();
    refreshStatus();
    const params = new URLSearchParams(window.location.search);
    const batchId = params.get("batch_id");
    if (batchId) {
      loadBatch(batchId);
    }
  }, []);

  async function loadBatch(batchId) {
    try {
      const next = await api.batchApplyNext(batchId);
      setBatch({ id: batchId, ...next });
    } catch (e) {
      setError(String(e));
      setBatch(null);
    }
  }

  async function advanceBatch(action) {
    if (!batch?.id) return;
    try {
      const r = await api.batchApplyAdvance(batch.id, action);
      setBatch({ id: batch.id, ...r });
      await refreshList();
    } catch (e) {
      setError(String(e));
    }
  }

  function exitBatch() {
    setBatch(null);
    const params = new URLSearchParams(window.location.search);
    params.delete("batch_id");
    const qs = params.toString();
    window.history.replaceState(
      null, "",
      `${window.location.pathname}${qs ? `?${qs}` : ""}`,
    );
  }

  useEffect(() => {
    const active = session && ACTIVE_STATES.has(session.state);
    if (!active) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (!pollRef.current) {
      pollRef.current = setInterval(refreshStatus, POLL_MS);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [session?.state]);

  async function start(postingId, { smart = false, dryRun = false } = {}) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applyStart(postingId, { smart, dryRun });
      await refreshStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function approvePlan(postingId) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applyApprovePlan(postingId);
      await refreshStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function skipPlan(postingId) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applySkipPlan(postingId);
      await refreshStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function submit(postingId) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applySubmit(postingId);
      await refreshStatus();
      await refreshList();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function abort(postingId) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applyAbort(postingId);
      await refreshStatus();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function markApplied(postingId) {
    setBusyId(postingId);
    setError(null);
    try {
      await api.applyMarkApplied(postingId, "marked applied via dashboard");
      await refreshList();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (ready === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-[80px] w-full" />
        <Skeleton className="h-[80px] w-full" />
      </div>
    );
  }

  const atsRows = ready.filter((r) => !isLinkedIn(r));
  const liRows = ready.filter(isLinkedIn);

  return (
    <div className="space-y-6">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      {batch && !batch.done && (
        <Card className="border-accent/40 bg-accent/5">
          <CardContent className="space-y-2 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                Batch apply — posting {batch.position} of {batch.total}
              </span>
              <span className="text-xs text-muted-foreground">
                {batch.applied} applied · {batch.skipped} skipped
              </span>
            </div>
            <div className="text-sm">
              #{batch.opportunity_id} {batch.title} —{" "}
              <span className="text-muted-foreground">{batch.employer}</span>
            </div>
            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                size="sm"
                variant="success"
                onClick={() => advanceBatch("applied")}
              >
                <ArrowRight className="h-3 w-3" />
                Mark advanced (applied)
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => advanceBatch("skipped")}
              >
                <SkipForward className="h-3 w-3" />
                Skip this one
              </Button>
              <Button size="sm" variant="ghost" onClick={exitBatch}>
                <XIcon className="h-3 w-3" />
                Exit batch mode
              </Button>
            </div>
            {batch.notes && batch.notes.length > 0 && (
              <details className="pt-1 text-xs text-muted-foreground">
                <summary className="cursor-pointer">
                  {batch.notes.length} note
                  {batch.notes.length === 1 ? "" : "s"}
                </summary>
                <ul className="ml-4 list-disc pt-1">
                  {batch.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              </details>
            )}
          </CardContent>
        </Card>
      )}

      {batch?.done && (
        <Card className="border-success/40 bg-success/10">
          <CardContent className="flex flex-wrap items-center justify-between gap-2 p-4">
            <span className="text-sm font-semibold">
              Batch complete — {batch.applied} applied,{" "}
              {batch.skipped} skipped ({batch.total} total)
            </span>
            <Button size="sm" variant="outline" onClick={exitBatch}>
              Done
            </Button>
          </CardContent>
        </Card>
      )}

      {session && (
        <ActiveSessionBanner
          session={session}
          busy={busyId === session.posting_id}
          onSubmit={() => submit(session.posting_id)}
          onAbort={() => abort(session.posting_id)}
          onApprovePlan={() => approvePlan(session.posting_id)}
          onSkipPlan={() => skipPlan(session.posting_id)}
        />
      )}

      <SectionHeader
        label="Ready to apply"
        meta={`${atsRows.length} via ATS`}
      />
      {atsRows.length === 0 && !session && (
        <Card className="border-dashed p-6 text-center text-xs text-muted-foreground">
          No ATS-driven postings ready. Save resume + cover letter on
          the Prompts tab first.
        </Card>
      )}
      {atsRows.map((row) => {
        const isCurrent = session && session.posting_id === row.opportunity_id;
        const disabled =
          busyId === row.opportunity_id || (session && !isCurrent);
        return (
          <ReadyCard
            key={row.id}
            row={row}
            disabled={disabled}
            busy={busyId === row.opportunity_id}
            onApply={() => start(row.opportunity_id)}
            onApplySmart={() => start(row.opportunity_id, { smart: true })}
            onApplySmartDry={() =>
              start(row.opportunity_id, { smart: true, dryRun: true })
            }
            onMarkApplied={() => markApplied(row.opportunity_id)}
          />
        );
      })}

      {liRows.length > 0 && (
        <>
          <SectionHeader
            label="LinkedIn (apply manually)"
            meta={`${liRows.length} pending`}
          />
          {liRows.map((row) => (
            <LinkedInCard
              key={row.id}
              row={row}
              busy={busyId === row.opportunity_id}
              onMarkApplied={() => markApplied(row.opportunity_id)}
            />
          ))}
        </>
      )}
    </div>
  );
}

function SectionHeader({ label, meta }) {
  return (
    <div className="flex items-baseline justify-between border-b border-border pb-1">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </h3>
      {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
    </div>
  );
}

function ReadyCard({
  row, disabled, busy,
  onApply, onApplySmart, onApplySmartDry, onMarkApplied,
}) {
  const savedAt = row.docs_ready_at
    ? new Date(row.docs_ready_at).toLocaleString()
    : "—";
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">
            #{row.opportunity_id} {row.opportunity_title}
            <span className="font-normal text-muted-foreground">
              {" "}— {row.employer}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            {row.ats_platform && (
              <Badge variant="accent">{row.ats_platform.toUpperCase()}</Badge>
            )}
            <span>Resume ✓ · Cover letter ✓ · Saved {savedAt}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={onMarkApplied}
            disabled={busy}
            title="For manual / off-platform flows"
          >
            Mark applied
          </Button>
          {onApplySmartDry && (
            <Button
              size="sm"
              variant="outline"
              onClick={onApplySmartDry}
              disabled={disabled}
              title="Smart filler in dry-run mode — fills the form for review but never submits"
            >
              <Cpu className="h-3 w-3" />
              Smart (dry)
            </Button>
          )}
          {onApplySmart && (
            <Button
              size="sm"
              variant="outline"
              onClick={onApplySmart}
              disabled={disabled}
              title="Use SmartFormFiller (LLM-driven, plan reviewed before fills)"
            >
              <Cpu className="h-3 w-3" />
              Smart
            </Button>
          )}
          <Button
            size="sm"
            variant="accent"
            onClick={onApply}
            disabled={disabled}
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Send className="h-3 w-3" />
            )}
            Apply
            <ArrowRight className="h-3 w-3" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function LinkedInCard({ row, busy, onMarkApplied }) {
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">
            #{row.opportunity_id} {row.opportunity_title}
            <span className="font-normal text-muted-foreground">
              {" "}— {row.employer}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            <Badge variant="strong">LINKEDIN</Badge>
            <span className="ml-2">
              Open in browser, paste resume + cover letter, then mark applied.
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {row.source_url && (
            <Button asChild size="sm" variant="outline">
              <a href={row.source_url} target="_blank" rel="noreferrer">
                <ExternalLink className="h-3 w-3" />
                Open on LinkedIn
              </a>
            </Button>
          )}
          <Button
            size="sm"
            variant="success"
            disabled={busy}
            onClick={onMarkApplied}
          >
            <CheckCircle2 className="h-3 w-3" />
            Mark as applied
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ActiveSessionBanner({
  session, busy, onSubmit, onAbort, onApprovePlan, onSkipPlan,
}) {
  const {
    state, posting_id, ats, fields_filled, error: sessionError,
    pending_questions, using_smart_filler,
    extracted_fields, fill_plan, plan_validation,
  } = session;
  const tone = SESSION_TONE[state] || { variant: "muted", label: state };
  const showSubmit = state === "ready_for_submit";
  const showPlanReview = state === "awaiting_plan_approval";
  const canAbort = !["submitted", "aborted", "error"].includes(state);

  return (
    <Card className="border-accent/40 bg-accent/5">
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">
              Apply session · posting #{posting_id}
            </span>
            {ats && <Badge variant="accent">{ats.toUpperCase()}</Badge>}
            {using_smart_filler && (
              <Badge variant="strong">
                <Cpu className="h-3 w-3" />
                SMART
              </Badge>
            )}
          </div>
          <Badge variant={tone.variant}>
            {state === "submitting" || state === "starting" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : null}
            {tone.label}
          </Badge>
        </div>

        {sessionError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
            {sessionError}
          </div>
        )}

        {fields_filled && fields_filled.length > 0 && (
          <div className="text-xs text-muted-foreground">
            Filled: {fields_filled.join(" · ")}
          </div>
        )}

        {showPlanReview && (
          <PlanReview
            fields={extracted_fields}
            plan={fill_plan}
            validation={plan_validation}
          />
        )}

        {pending_questions && pending_questions.length > 0 && (
          <PendingQuestions items={pending_questions} />
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          {showPlanReview && (
            <>
              <Button
                size="sm"
                variant="success"
                onClick={onApprovePlan}
                disabled={busy}
              >
                <ThumbsUp className="h-3 w-3" />
                Approve plan & fill
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={onSkipPlan}
                disabled={busy}
                title="Fill the form manually in the browser instead"
              >
                <SkipForward className="h-3 w-3" />
                Skip — fill manually
              </Button>
            </>
          )}
          {showSubmit && (
            <Button
              size="sm"
              variant="accent"
              onClick={onSubmit}
              disabled={busy}
            >
              <Send className="h-3 w-3" />
              Submit
            </Button>
          )}
          {canAbort && (
            <Button
              size="sm"
              variant="destructive"
              onClick={onAbort}
              disabled={busy}
            >
              <StopCircle className="h-3 w-3" />
              Abort
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function PlanReview({ fields, plan, validation }) {
  if (!plan || plan.length === 0) {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
        SmartFormFiller produced no plan. Use “Skip” to fill manually.
      </div>
    );
  }
  const labelByIndex = new Map(
    (fields || []).map((f) => [f.index, f.label || `field_${f.index}`]),
  );
  const trustPct = validation?.fraction_valid != null
    ? Math.round(validation.fraction_valid * 100)
    : null;
  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wider text-muted-foreground">
          LLM fill plan ({plan.length} action{plan.length === 1 ? "" : "s"})
        </span>
        {trustPct != null && (
          <Badge
            variant={validation?.is_trustworthy ? "success" : "warning"}
          >
            {trustPct}% selectors valid
          </Badge>
        )}
      </div>
      <div className="max-h-[260px] overflow-auto rounded-md border border-border/60 bg-muted/20">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-muted/60 text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-2 py-1 text-left">Field</th>
              <th className="px-2 py-1 text-left">Action</th>
              <th className="px-2 py-1 text-left">Value</th>
              <th className="px-2 py-1 text-left">Source</th>
            </tr>
          </thead>
          <tbody>
            {plan.map((a, i) => (
              <tr key={i} className="border-t border-border/30">
                <td className="px-2 py-1">{labelByIndex.get(a.field) || `#${a.field}`}</td>
                <td className="px-2 py-1 font-mono">{a.action}</td>
                <td className="px-2 py-1 truncate max-w-[280px]">
                  {a.action === "upload"
                    ? `📎 ${a.file || "?"}`
                    : a.value || (a.action === "skip" ? "—" : "")}
                </td>
                <td className="px-2 py-1 text-muted-foreground">{a.source || "llm"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {validation?.issues && validation.issues.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            {validation.issues.length} validation issue{validation.issues.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-1 list-disc pl-5">
            {validation.issues.slice(0, 8).map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function PendingQuestions({ items }) {
  return (
    <div className="space-y-2 border-t border-border/60 pt-3">
      <div className="text-xs text-muted-foreground">
        {items.length} screening question{items.length === 1 ? "" : "s"} need a
        review — copy your answer into the browser before submitting.
      </div>
      {items.map((item, i) => (
        <PendingQuestionRow key={i} item={item} />
      ))}
    </div>
  );
}

function PendingQuestionRow({ item }) {
  const [draft, setDraft] = useState(item.draft || "");
  const [confirmed, setConfirmed] = useState(item.confirmed);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  async function save() {
    if (!draft.trim()) {
      setError("Answer must be non-empty.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.applyConfirmAnswer(item.question, draft);
      setConfirmed(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setError(`Clipboard write failed: ${e.message}`);
    }
  }

  return (
    <div
      className={cn(
        "rounded-md border p-3 text-xs",
        confirmed
          ? "border-success/40 bg-success/10"
          : "border-border bg-muted/30",
      )}
    >
      <div className="text-sm font-semibold">{item.question}</div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">
        Draft via {item.source}
      </div>
      <Textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setConfirmed(false);
        }}
        spellCheck={false}
        className="mt-2 min-h-[80px] font-mono text-xs"
      />
      {error && (
        <div className="mt-1 text-xs text-destructive-foreground">
          {error}
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={confirmed ? "success" : "accent"}
          onClick={save}
          disabled={busy || !draft.trim()}
        >
          {busy && <Loader2 className="h-3 w-3 animate-spin" />}
          {confirmed && !busy && <CheckCircle2 className="h-3 w-3" />}
          {confirmed ? "Saved" : "Save & cache"}
        </Button>
        <Button size="sm" variant="outline" onClick={copy} disabled={!draft}>
          <Copy className="h-3 w-3" />
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}
