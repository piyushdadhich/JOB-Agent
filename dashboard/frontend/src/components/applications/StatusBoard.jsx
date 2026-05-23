import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, CheckCircle2, Loader2, Send } from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Compact table view of every application. Sortable columns + bulk
// checkboxes; clicking a row jumps to the Process Queue with that
// item selected.

const COLUMN_ORDER = [
  "selected", "docs_ready", "applied",
  "interview", "offer", "rejected",
];

const STATUS_LABEL = {
  selected: "Pending",
  docs_ready: "Ready",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
};

const STATUS_CLASS = {
  selected: "text-warning",
  docs_ready: "text-success",
  applied: "text-accent",
  interview: "text-strong",
  offer: "text-success",
  rejected: "text-muted-foreground",
};

// Sort comparators keyed by column id.
const SORTERS = {
  employer: (a, b) =>
    String(a.employer || "").localeCompare(String(b.employer || "")),
  title: (a, b) =>
    String(a.title || "").localeCompare(String(b.title || "")),
  fit: (a, b) => (a.fit_score ?? -1) - (b.fit_score ?? -1),
  status: (a, b) =>
    COLUMN_ORDER.indexOf(a.status) - COLUMN_ORDER.indexOf(b.status),
};

export default function StatusBoard({
  board, onOpenItem, onReload, onError, flashToast,
}) {
  const [sortKey, setSortKey] = useState("status");
  const [sortDir, setSortDir] = useState("asc");
  const [checked, setChecked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);

  const rows = useMemo(() => {
    const flat = COLUMN_ORDER.flatMap((c) => board.columns[c] || []);
    const sorted = flat.slice().sort(SORTERS[sortKey]);
    if (sortDir === "desc") sorted.reverse();
    return sorted;
  }, [board, sortKey, sortDir]);

  const s = board.stats;

  function toggleSort(key) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function toggleRow(oppId) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(oppId)) next.delete(oppId);
      else next.add(oppId);
      return next;
    });
  }

  function selectAllReady() {
    const ready = rows
      .filter((r) => r.status === "docs_ready")
      .map((r) => r.opportunity_id);
    setChecked(new Set(ready));
  }

  function clearChecked() {
    setChecked(new Set());
  }

  async function applySelected() {
    if (checked.size === 0) return;
    const ok = window.confirm(
      `Apply to ${checked.size} position(s)? Playwright opens each ` +
      "in sequence — review each form before submitting.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      for (const oppId of checked) {
        try {
          await api.applyStart(oppId);
        } catch (e) {
          onError?.(`opp ${oppId}: ${e.message || e}`);
          break;
        }
      }
      clearChecked();
      await onReload?.();
    } finally {
      setBusy(false);
    }
  }

  async function markSelectedApplied() {
    if (checked.size === 0) return;
    const ok = window.confirm(
      `Mark ${checked.size} position(s) as applied?`,
    );
    if (!ok) return;
    setBusy(true);
    try {
      for (const oppId of checked) {
        try {
          await api.applicationsUpdateStatusByOpp(oppId, "applied");
        } catch (e) {
          onError?.(`opp ${oppId}: ${e.message || e}`);
        }
      }
      flashToast?.(`Marked ${checked.size} applied.`);
      clearChecked();
      await onReload?.();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap gap-4 p-3 text-xs">
        <Stat label="Selected" value={s.selected} />
        <Stat label="Docs Ready" value={s.docs_ready} />
        <Stat label="Applied" value={s.applied} />
        <Stat label="Interview" value={s.interview} />
        <Stat label="Offer" value={s.offer} />
        <Stat label="Rejected" value={s.rejected} />
      </Card>

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="w-8 px-3 py-2" />
              <SortHeader
                label="Company" col="employer"
                sortKey={sortKey} sortDir={sortDir} onSort={toggleSort}
              />
              <SortHeader
                label="Title" col="title"
                sortKey={sortKey} sortDir={sortDir} onSort={toggleSort}
              />
              <SortHeader
                label="FIT" col="fit"
                sortKey={sortKey} sortDir={sortDir} onSort={toggleSort}
              />
              <SortHeader
                label="Status" col="status"
                sortKey={sortKey} sortDir={sortDir} onSort={toggleSort}
              />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-3 py-6 text-center text-xs text-muted-foreground"
                >
                  No applications yet.
                </td>
              </tr>
            )}
            {rows.map((card) => (
              <tr
                key={card.opportunity_id}
                className="border-t border-border hover:bg-muted/40"
              >
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={checked.has(card.opportunity_id)}
                    onChange={() => toggleRow(card.opportunity_id)}
                    className="h-4 w-4 cursor-pointer"
                  />
                </td>
                <td
                  className="cursor-pointer px-3 py-2 font-medium"
                  onClick={() => onOpenItem(card.opportunity_id)}
                >
                  {card.employer}
                </td>
                <td
                  className="cursor-pointer px-3 py-2 text-muted-foreground"
                  onClick={() => onOpenItem(card.opportunity_id)}
                >
                  {card.title}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums">
                  {card.fit_score ?? "—"}
                </td>
                <td
                  className={cn(
                    "px-3 py-2 font-medium",
                    STATUS_CLASS[card.status],
                  )}
                >
                  {STATUS_LABEL[card.status] || card.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card className="flex flex-wrap items-center gap-2 p-3">
        <Button size="sm" variant="outline" onClick={selectAllReady}>
          Select All Ready ({s.docs_ready})
        </Button>
        {checked.size > 0 && (
          <>
            <Badge variant="accent">{checked.size} selected</Badge>
            <Button size="sm" variant="ghost" onClick={clearChecked}>
              Clear
            </Button>
            <Button
              size="sm"
              variant="accent"
              onClick={applySelected}
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Send className="h-3 w-3" />
              )}
              Apply Selected ({checked.size})
            </Button>
            <Button
              size="sm"
              variant="success"
              onClick={markSelectedApplied}
              disabled={busy}
            >
              <CheckCircle2 className="h-3 w-3" />
              Mark Selected Applied
            </Button>
          </>
        )}
      </Card>
    </div>
  );
}

function SortHeader({ label, col, sortKey, sortDir, onSort }) {
  const active = sortKey === col;
  return (
    <th
      className="cursor-pointer select-none px-3 py-2 text-left hover:text-foreground"
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active &&
          (sortDir === "asc" ? (
            <ArrowUp className="h-3 w-3" />
          ) : (
            <ArrowDown className="h-3 w-3" />
          ))}
      </span>
    </th>
  );
}

function Stat({ label, value }) {
  return (
    <span className="text-muted-foreground">
      <span className="font-mono font-semibold text-foreground">
        {value}
      </span>{" "}
      {label}
    </span>
  );
}
