import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const TOUR_STEPS = [
  {
    title: "Welcome",
    body:
      "Quick walk-through of the dashboard. Six stops; takes about " +
      "30 seconds.",
    tab: "dashboard",
  },
  {
    title: "Today's metrics",
    body:
      "Pipeline snapshot for the agent-day (3 AM Toronto rollover). " +
      "How many posts the agent discovered, evaluated, and shortlisted.",
    tab: "dashboard",
  },
  {
    title: "Shortlist",
    body:
      "Your queue. Each card is a posting the evaluator rated STRONG " +
      "or TOP_TIER for your inventory. Select to advance into Apply.",
    tab: "shortlist",
  },
  {
    title: "Prompts",
    body:
      "Selected postings get resume + cover-letter prompts. " +
      "Either run them through a hosted LLM or copy/paste into " +
      "Claude / GPT / Gemini manually.",
    tab: "prompts",
  },
  {
    title: "Apply",
    body:
      "Playwright form-filler walks through each Greenhouse / Lever / " +
      "Ashby / Workday form. It NEVER auto-submits — you confirm " +
      "every field before the final click.",
    tab: "apply",
  },
  {
    title: "Pipeline + Expansion",
    body:
      "Pipeline tracks status (applied → interview → offer). " +
      "Expansion runs weekly and suggests new role-types / employers " +
      "based on what's been scoring well.",
    tab: "pipeline",
  },
];

const STORAGE_KEY = "jobagent_tour_seen_v1";

export default function Tour({ setTab }) {
  const [open, setOpen] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!window.localStorage.getItem(STORAGE_KEY)) {
      setOpen(true);
    }
  }, []);

  if (!open) return null;

  const step = TOUR_STEPS[stepIdx];
  if (step?.tab) {
    // Eagerly hop to the relevant tab so the tour and the UI agree.
    setTimeout(() => setTab?.(step.tab), 0);
  }

  const dismiss = () => {
    window.localStorage.setItem(STORAGE_KEY, "1");
    setOpen(false);
  };

  const advance = () => {
    if (stepIdx + 1 >= TOUR_STEPS.length) {
      dismiss();
    } else {
      setStepIdx(stepIdx + 1);
    }
  };

  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-end justify-center p-4">
      <div className="pointer-events-auto w-full max-w-md rounded-lg border border-border bg-card p-4 shadow-xl">
        <div className="mb-1 flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">{step.title}</h3>
          <span className="text-[11px] text-muted-foreground">
            {stepIdx + 1} / {TOUR_STEPS.length}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">{step.body}</p>
        <div className="mt-3 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={dismiss}>
            Skip tour
          </Button>
          <Button size="sm" onClick={advance}>
            {stepIdx + 1 >= TOUR_STEPS.length ? "Done" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function resetTour() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}
