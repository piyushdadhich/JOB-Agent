import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Camera,
  ExternalLink,
  FileText,
  Search,
} from "lucide-react";
import { api } from "@/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const STATUS_OPTIONS = [
  "drafted",
  "ready_to_submit",
  "submitted",
  "confirmed_received",
  "responded",
  "interviewing",
  "offered",
  "rejected",
  "ghosted",
  "silent_rejected",
  "withdrawn",
];

const STATUS_VARIANT = {
  drafted: "muted",
  ready_to_submit: "warning",
  submitted: "accent",
  confirmed_received: "accent",
  responded: "accent",
  interviewing: "top_tier",
  offered: "success",
  rejected: "destructive",
  ghosted: "muted",
  silent_rejected: "destructive",
  withdrawn: "muted",
};

const FILTERS = [
  { id: "all", label: "All" },
  { id: "this_week", label: "This week" },
  { id: "submitted", label: "Submitted" },
  { id: "interviewing", label: "Interview" },
  { id: "rejected", label: "Rejected" },
  { id: "flagged", label: "🚩 Flagged" },
];

function withinThisWeek(iso) {
  if (!iso) return false;
  return Date.now() - Date.parse(iso) < 7 * 24 * 3600 * 1000;
}

export default function History() {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [error, setError] = useState(null);
  const [modal, setModal] = useState(null); // { row, kind }

  async function refresh() {
    try {
      const r = await api.listApplications();
      setRows(r);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const filtered = useMemo(() => {
    if (rows === null) return null;
    let r = rows;
    if (filter === "this_week") {
      r = r.filter((x) => withinThisWeek(x.status_updated_at));
    } else if (filter === "flagged") {
      r = r.filter((x) => x.flagged_at);
    } else if (filter !== "all") {
      r = r.filter((x) => x.status === filter);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      r = r.filter(
        (x) =>
          (x.employer || "").toLowerCase().includes(q) ||
          (x.opportunity_title || "").toLowerCase().includes(q),
      );
    }
    return r;
  }, [rows, filter, search]);

  async function updateStatus(id, status) {
    try {
      await api.updateStatus(id, status);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (rows === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-[42px] w-full" />
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-[80px] w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            placeholder="Search employer or title…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        <ToggleGroup
          type="single"
          value={filter}
          onValueChange={(v) => v && setFilter(v)}
        >
          {FILTERS.map((f) => (
            <ToggleGroupItem key={f.id} value={f.id}>
              {f.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length} of {rows.length}
        </span>
      </div>

      {filtered.length === 0 && (
        <Card className="border-dashed p-6 text-center text-xs text-muted-foreground">
          No applications match.
        </Card>
      )}

      <div className="space-y-2">
        {filtered.map((row) => (
          <HistoryRow
            key={row.id}
            row={row}
            onUpdateStatus={updateStatus}
            onView={(kind) => setModal({ row, kind })}
          />
        ))}
      </div>

      {modal && (
        <TextModal
          row={modal.row}
          field={modal.kind}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}

function HistoryRow({ row, onUpdateStatus, onView }) {
  const variant = STATUS_VARIANT[row.status] || "muted";
  const dateLabel = row.status_updated_at
    ? new Date(row.status_updated_at).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">
            {row.opportunity_title}
            <span className="font-normal text-muted-foreground">
              {" "}— {row.employer}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant={variant}>{row.status}</Badge>
            {row.flagged_at && (
              <Badge variant="warning" title={`Flagged at ${row.flagged_at}`}>
                🚩 Flagged
              </Badge>
            )}
            <span>{dateLabel}</span>
            {row.ats_platform && <span>· {row.ats_platform}</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {row.resume_text && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onView("resume_text")}
            >
              <FileText className="h-3 w-3" />
              Resume
            </Button>
          )}
          {row.cover_letter_text && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onView("cover_letter_text")}
            >
              <FileText className="h-3 w-3" />
              CL
            </Button>
          )}
          {row.screenshot_path && (
            <Button asChild size="sm" variant="outline">
              <a
                href={`/api/screenshots/${row.id}.png`}
                target="_blank"
                rel="noreferrer"
              >
                <Camera className="h-3 w-3" />
                Screenshot
              </a>
            </Button>
          )}
          {row.submitted_url && (
            <Button asChild size="sm" variant="ghost">
              <a
                href={row.submitted_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-3 w-3" />
              </a>
            </Button>
          )}
          <Select
            value={row.status}
            onValueChange={(v) => onUpdateStatus(row.id, v)}
          >
            <SelectTrigger className="h-8 w-[160px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  );
}

function TextModal({ row, field, onClose }) {
  const text = row[field] || "";
  const label = field === "resume_text" ? "Resume" : "Cover letter";
  return (
    <Dialog open={true} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[86vh] max-w-3xl overflow-hidden p-0">
        <DialogHeader className="border-b border-border p-4">
          <DialogTitle>
            {label} — {row.employer}, {row.opportunity_title}
          </DialogTitle>
        </DialogHeader>
        <pre className="m-0 max-h-[70vh] overflow-auto p-4 font-mono text-xs leading-relaxed">
          {text}
        </pre>
      </DialogContent>
    </Dialog>
  );
}
