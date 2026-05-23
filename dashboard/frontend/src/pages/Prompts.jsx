import { useEffect, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Copy,
} from "lucide-react";
import { api } from "@/api";
import PromptPanel from "@/components/PromptPanel.jsx";
import ResumeCostCard from "@/components/prompts/ResumeCostCard.jsx";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const TIER_VARIANT = {
  TOP_TIER: "top_tier",
  STRONG: "strong",
};

function postingComplete(p) {
  return !!(p.resume_text && p.cover_letter_text);
}

function PostingNav({ queue, cursor, setCursor }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {queue.map((item, idx) => {
        const done = postingComplete(item);
        const active = idx === cursor;
        return (
          <button
            key={item.opportunity_id}
            onClick={() => setCursor(idx)}
            title={`#${item.opportunity_id} ${item.employer}`}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-full border text-[11px] font-mono font-semibold transition-colors",
              active && "ring-2 ring-ring ring-offset-1 ring-offset-background",
              done
                ? "border-success bg-success/15 text-success"
                : active
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-border bg-muted text-muted-foreground hover:bg-muted/60",
            )}
          >
            {done ? <Check className="h-3.5 w-3.5" /> : idx + 1}
          </button>
        );
      })}
    </div>
  );
}

export default function Prompts() {
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [batchMode, setBatchMode] = useState(false);
  const [batchIds, setBatchIds] = useState([]);
  const [batchCount, setBatchCount] = useState(0);
  const [batchPaste, setBatchPaste] = useState("");

  async function refresh() {
    try {
      const rows = await api.listApplications({ stage: "needs_prompts" });
      setQueue(rows);
      setCursor((c) => Math.min(c, Math.max(0, rows.length - 1)));
    } catch (e) {
      setError(String(e));
    }
  }

  async function copyAllPrompts() {
    try {
      const data = await api.getBatchPrompts();
      if (!data.count) {
        setError("Nothing in the prompts queue to copy.");
        return;
      }
      setBatchIds(data.ids);
      setBatchCount(data.count);
      await navigator.clipboard.writeText(data.batch_prompt);
      setBatchMode(true);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveAllResponses() {
    try {
      await api.saveBatchResponses({
        response: batchPaste,
        ids: batchIds,
      });
      setBatchMode(false);
      setBatchPaste("");
      setBatchIds([]);
      setBatchCount(0);
      setError(null);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  function cancelBatch() {
    if (
      batchPaste.trim() &&
      !window.confirm(
        "Discard the pasted response? Anything in the textarea will be lost.",
      )
    ) {
      return;
    }
    setBatchMode(false);
    setBatchPaste("");
    setBatchIds([]);
    setBatchCount(0);
  }

  useEffect(() => {
    refresh();
  }, []);

  if (queue === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-[80px] w-full" />
        <Skeleton className="h-[400px] w-full" />
        <Skeleton className="h-[400px] w-full" />
      </div>
    );
  }

  if (queue.length === 0) {
    return (
      <Card className="border-dashed p-10 text-center text-sm text-muted-foreground">
        No selected postings. Pick some from the Shortlist tab.
      </Card>
    );
  }

  const current = queue[cursor];
  const hasResume = !!current.resume_text;
  const hasCoverLetter = !!current.cover_letter_text;
  const docsReady = !!current.docs_ready_at;
  const readyCount = queue.filter(postingComplete).length;

  return (
    <div className="space-y-4">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      <ResumeCostCard />

      <Card className="p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Prompts queue
            </div>
            <div className="mt-1 text-sm">
              <span className="font-mono tabular-nums">
                {readyCount} of {queue.length}
              </span>{" "}
              <span className="text-muted-foreground">ready</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!batchMode && queue.length > 1 && (
              <Button onClick={copyAllPrompts} variant="accent" size="sm">
                <Copy className="h-3 w-3" />
                Copy All Prompts ({queue.length} jobs)
              </Button>
            )}
            <PostingNav queue={queue} cursor={cursor} setCursor={setCursor} />
          </div>
        </div>
      </Card>

      {batchMode && (
        <Card className="space-y-4 p-4">
          <div className="text-sm">
            Prompts copied for{" "}
            <span className="font-mono tabular-nums">{batchCount}</span> jobs.
            Paste into Claude, then paste the full response below.
          </div>
          <Textarea
            value={batchPaste}
            onChange={(e) => setBatchPaste(e.target.value)}
            placeholder="Paste Claude's full response here…"
            spellCheck={false}
            className="min-h-[400px] font-mono text-xs"
          />
          <div className="flex gap-2">
            <Button
              onClick={saveAllResponses}
              variant="accent"
              disabled={!batchPaste.trim()}
            >
              <Check className="h-3 w-3" />
              Save All Responses
            </Button>
            <Button variant="outline" onClick={cancelBatch}>
              Cancel
            </Button>
          </div>
        </Card>
      )}

      <Card className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
              #{current.opportunity_id}
            </div>
            <div className="truncate text-base font-semibold">
              {current.employer}{" "}
              <span className="font-normal text-muted-foreground">
                — {current.opportunity_title}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {current.tier && (
              <Badge variant={TIER_VARIANT[current.tier] || "outline"}>
                {current.tier}
              </Badge>
            )}
            {current.fit_score != null && (
              <span className="font-mono text-sm tabular-nums">
                {current.fit_score}/10
              </span>
            )}
            <Badge variant={hasResume ? "success" : "muted"}>
              {hasResume ? <Check className="h-3 w-3" /> : null} Resume
            </Badge>
            <Badge variant={hasCoverLetter ? "success" : "muted"}>
              {hasCoverLetter ? <Check className="h-3 w-3" /> : null} Cover letter
            </Badge>
            {docsReady && <Badge variant="accent">→ Ready to apply</Badge>}
          </div>
        </div>
      </Card>

      <PromptPanel
        key={`r-${current.opportunity_id}`}
        postingId={current.opportunity_id}
        kind="resume"
        savedPrompt={current.resume_prompt || null}
        savedText={current.resume_text || ""}
        onSaved={refresh}
      />

      <PromptPanel
        key={`cl-${current.opportunity_id}`}
        postingId={current.opportunity_id}
        kind="cover-letter"
        savedPrompt={current.cover_letter_prompt || null}
        savedText={current.cover_letter_text || ""}
        onSaved={refresh}
      />

      <div className="flex items-center justify-between gap-2 pt-2">
        <Button
          variant="outline"
          size="sm"
          disabled={cursor === 0}
          onClick={() => setCursor((c) => c - 1)}
        >
          <ChevronLeft className="h-3 w-3" />
          Previous
        </Button>
        <span className="text-xs text-muted-foreground">
          {queue.length} in queue
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={cursor >= queue.length - 1}
          onClick={() => setCursor((c) => c + 1)}
        >
          Next
          <ChevronRight className="h-3 w-3" />
        </Button>
      </div>
    </div>
  );
}
