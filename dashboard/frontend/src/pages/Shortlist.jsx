import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  Plus,
  RotateCcw,
  Send,
  X,
} from "lucide-react";
import { api } from "@/api";
import AddJobModal from "@/components/AddJobModal.jsx";
import EmailMonitorPanel from "@/components/EmailMonitorPanel.jsx";
import PostingCard from "@/components/PostingCard.jsx";
import MultiSelectDropdown from "@/components/MultiSelectDropdown.jsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
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
import { Skeleton } from "@/components/ui/skeleton";

const PER_PAGE = 20;

const TIER_FILTERS = [
  { id: "all", label: "All" },
  { id: "TOP_TIER", label: "TOP_TIER" },
  { id: "STRONG", label: "STRONG" },
  { id: "EXPLORATORY", label: "EXPLORATORY" },
  { id: "selected", label: "SELECTED" },
];

const TIME_RANGES = [
  { id: "today", label: "Today" },
  { id: "week", label: "Week" },
  { id: "month", label: "Month" },
  { id: "all", label: "All time" },
];

// Sort applies within the current page (the server already orders
// tier → fit → date across the whole set).
const SORTS = {
  fit_desc: { label: "Fit score (high → low)", cmp: (a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0) },
  fit_asc: { label: "Fit score (low → high)", cmp: (a, b) => (a.fit_score ?? 0) - (b.fit_score ?? 0) },
  newest: { label: "Newest first", cmp: (a, b) =>
    String(b.posted_at || "").localeCompare(String(a.posted_at || "")) },
  employer: { label: "Employer A → Z", cmp: (a, b) =>
    String(a.employer || "").localeCompare(String(b.employer || "")) },
};

// Legacy single-slug (?city=gta) -> canonical city for dropdown UX.
// Backend still handles the slug directly via CITY_FILTERS; this map
// only normalizes the in-memory state so the dropdown checkbox lights
// up correctly when the user lands on a legacy bookmark.
const LEGACY_CITY_SLUG_MAP = {
  gta: "Toronto",
  calgary: "Calgary",
  edmonton: "Edmonton",
  remote: "Remote-Canada",
};

const EMPTY_FILTERS = {
  function: [],
  industry: [],
  city: [],
  ai_subtype: [],
  ai_role: "",
  mode: "show",
};

function csvToList(s) {
  if (!s) return [];
  return s.split(",").map((p) => p.trim()).filter(Boolean);
}

function parseUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  let cities = csvToList(params.get("city"));
  cities = cities.map(
    (v) => LEGACY_CITY_SLUG_MAP[v.toLowerCase()] || v,
  );
  return {
    function: csvToList(params.get("function")),
    industry: csvToList(params.get("industry")),
    city: cities,
    ai_subtype: csvToList(params.get("ai_subtype")),
    ai_role: params.get("ai_role") || "",
    mode: params.get("mode") === "hide" ? "hide" : "show",
  };
}

function buildUrlSearch(filters) {
  const params = new URLSearchParams();
  if (filters.function.length) params.set("function", filters.function.join(","));
  if (filters.industry.length) params.set("industry", filters.industry.join(","));
  if (filters.city.length) params.set("city", filters.city.join(","));
  if (filters.ai_subtype.length) params.set("ai_subtype", filters.ai_subtype.join(","));
  if (filters.ai_role) params.set("ai_role", filters.ai_role);
  if (filters.mode === "hide") params.set("mode", "hide");
  const s = params.toString();
  return s ? `?${s}` : "";
}

export default function Shortlist({ goToTab }) {
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({
    all: 0, top_tier: 0, strong: 0, exploratory: 0, selected: 0,
  });
  const [tier, setTier] = useState("all");
  const [dateRange, setDateRange] = useState("all");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("fit_desc");
  const [statusByPosting, setStatusByPosting] = useState({});
  const [busyByPosting, setBusyByPosting] = useState({});
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filterOptions, setFilterOptions] = useState({
    functions: [],
    industries: [],
    cities: [],
    ai_subtypes: [],
  });
  const [filtersExpanded, setFiltersExpanded] = useState(true);
  const [filters, setFilters] = useState(() => parseUrlFilters());
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [batchModal, setBatchModal] = useState(null);
  const [copyFlash, setCopyFlash] = useState(false);
  const [flagModal, setFlagModal] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addToast, setAddToast] = useState(null);

  useEffect(() => {
    api
      .listShortlistFilterOptions()
      .then(setFilterOptions)
      .catch((e) => setError(String(e)));
  }, []);

  const loadCounts = useCallback(() => {
    api
      .shortlistCounts({ ...filters, date_range: dateRange })
      .then(setCounts)
      .catch((e) => setError(String(e)));
  }, [filters, dateRange]);

  // Counts depend on filters + the time range (not tier / page).
  useEffect(() => {
    loadCounts();
  }, [loadCounts]);

  // Items depend on filters + tier tab + time range + page.
  useEffect(() => {
    setLoading(true);
    api
      .listShortlist({
        ...filters,
        tier,
        date_range: dateRange,
        page,
        per_page: PER_PAGE,
      })
      .then((rows) => {
        setItems(rows);
        // Seed in-memory status from persisted opportunities.status so
        // the Deselect button is available after a page reload (not
        // only for postings selected in the current session).
        setStatusByPosting((prev) => {
          const next = { ...prev };
          for (const r of rows) {
            if (r.status === "shortlisted" && next[r.opportunity_id] === undefined) {
              next[r.opportunity_id] = "selected";
            }
          }
          return next;
        });
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [filters, tier, dateRange, page]);

  const updateFilters = useCallback((next) => {
    setFilters(next);
    setPage(1);
    const search = buildUrlSearch(next);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
  }, []);

  function setAxis(axis, vals) {
    updateFilters({ ...filters, [axis]: vals });
  }
  function setMode(m) {
    updateFilters({ ...filters, mode: m });
  }
  function resetFilters() {
    updateFilters({ ...EMPTY_FILTERS });
  }
  function changeTier(t) {
    setTier(t);
    setPage(1);
  }
  function changeDateRange(next) {
    setDateRange(next);
    setPage(1);
  }

  const setBusy = (id, v) =>
    setBusyByPosting((s) => ({ ...s, [id]: v }));
  const setStatus = (id, v) =>
    setStatusByPosting((s) => ({ ...s, [id]: v }));

  // Once a posting is selected or skipped it leaves the shortlist
  // queue (the server excludes actioned statuses from non-selected
  // tabs). Drop the card immediately for snappy feedback, clear any
  // checkbox-bulk-select state for that card, then refresh counts.
  function removeCard(id) {
    setItems((prev) => {
      const next = prev.filter((i) => i.opportunity_id !== id);
      if (next.length === 0 && page > 1) setPage((p) => p - 1);
      return next;
    });
    setSelectedIds((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    loadCounts();
  }

  async function onSelect(id) {
    setBusy(id, true);
    try {
      await api.selectPosting(id);
      setStatus(id, "selected");
      // Outside the "SELECTED" tab the card leaves the queue; on the
      // SELECTED tab it stays put and just flips badge state.
      if (tier !== "selected") {
        removeCard(id);
      } else {
        loadCounts();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(id, false);
    }
  }
  async function onSkip(id) {
    setBusy(id, true);
    try {
      await api.skipPosting(id);
      setStatus(id, "skipped");
      removeCard(id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(id, false);
    }
  }
  async function onUnskip(id) {
    setBusy(id, true);
    try {
      await api.unskipPosting(id);
      setStatus(id, undefined);
      loadCounts();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(id, false);
    }
  }
  async function onUnselect(id) {
    setBusy(id, true);
    try {
      await api.unselectPosting(id);
      setStatus(id, undefined);
      // From the SELECTED tab the card now disappears; elsewhere it
      // simply flips back to its tier badge.
      if (tier === "selected") {
        removeCard(id);
      } else {
        loadCounts();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(id, false);
    }
  }

  function toggleSelected(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  async function onGeneratePromptsForSelected() {
    if (!selectedIds.size) return;
    try {
      const data = await api.batchPromptsPreview([...selectedIds]);
      setBatchModal({ text: data.batch_prompt, count: data.count });
    } catch (e) {
      setError(String(e));
    }
  }

  async function onApplyToSelected() {
    if (!selectedIds.size) return;
    const ok = window.confirm(
      `About to apply to ${selectedIds.size} postings. You will ` +
      "approve each one individually before submission. Proceed?",
    );
    if (!ok) return;
    try {
      const r = await api.batchApplyStart([...selectedIds]);
      const params = new URLSearchParams(window.location.search);
      params.set("batch_id", r.batch_id);
      window.history.replaceState(
        null, "",
        `${window.location.pathname}?${params.toString()}`,
      );
      clearSelection();
      if (goToTab) goToTab("apply");
    } catch (e) {
      setError(String(e));
    }
  }

  async function copyBatchToClipboard() {
    if (!batchModal) return;
    try {
      await navigator.clipboard.writeText(batchModal.text);
      setCopyFlash(true);
      setTimeout(() => setCopyFlash(false), 1500);
    } catch (e) {
      setError(`Clipboard write failed: ${e.message}`);
    }
  }

  function onJobAdded(result) {
    setAddToast(`Added: ${result.title} at ${result.employer}`);
    setTimeout(() => setAddToast(null), 6000);
    // A freshly added job lands on the shortlist queue — refresh
    // counts so the tab badges reflect it.
    loadCounts();
  }

  function openFlagModal(item) {
    setFlagModal({
      item,
      createRule: false,
      rule: {
        employer_pattern: item.employer || "",
        title_pattern: "",
        industry_pattern: item.industry_normalized || "",
        function_pattern: item.function || "",
        ai_subtype_pattern: item.ai_subtype || "",
      },
    });
  }

  async function submitFlag(withRule) {
    if (!flagModal) return;
    const { item, rule } = flagModal;
    const payload = withRule
      ? {
          createRule: true,
          rule: {
            employer_pattern: rule.employer_pattern || null,
            title_pattern: rule.title_pattern || null,
            industry_pattern: rule.industry_pattern || null,
            function_pattern: rule.function_pattern || null,
            ai_subtype_pattern: rule.ai_subtype_pattern || null,
          },
        }
      : { createRule: false, rule: null };
    try {
      await api.flagPosting(item.opportunity_id, payload);
      setFlagModal(null);
      removeCard(item.opportunity_id);
    } catch (e) {
      setError(String(e));
    }
  }

  function setFlagRule(field, value) {
    setFlagModal((m) =>
      m ? { ...m, rule: { ...m.rule, [field]: value } } : m,
    );
  }

  // Server already filters by tier + date_range; we only re-sort
  // within the current page.
  const filtered = useMemo(
    () => [...items].sort(SORTS[sort].cmp),
    [items, sort],
  );

  const tabCount = (id) =>
    id === "TOP_TIER"
      ? counts.top_tier
      : id === "STRONG"
        ? counts.strong
        : id === "EXPLORATORY"
          ? counts.exploratory
          : id === "selected"
            ? counts.selected
            : counts.all;

  const activeCount = tabCount(tier);
  const totalPages = Math.max(1, Math.ceil(activeCount / PER_PAGE));
  const rangeStart = activeCount === 0 ? 0 : (page - 1) * PER_PAGE + 1;
  const rangeEnd = Math.min(page * PER_PAGE, activeCount);

  // In-session count of postings the user has selected via the
  // per-card checkbox (separate from server-side 'shortlisted').
  const inSessionSelected = selectedIds.size;

  // Select All on this page: persist a "Select to apply" for every
  // posting currently on screen. Outside the SELECTED tab those cards
  // leave the queue; counts get refreshed afterwards.
  async function selectAllOnPage() {
    const ids = items.map((i) => i.opportunity_id);
    if (ids.length === 0) return;
    ids.forEach((id) => setBusy(id, true));
    try {
      await Promise.allSettled(
        ids.map(async (id) => {
          try {
            await api.selectPosting(id);
            setStatus(id, "selected");
          } catch (e) {
            setError(String(e));
          }
        }),
      );
      if (tier !== "selected") {
        setItems([]);
        if (page > 1) setPage((p) => p - 1);
      }
      loadCounts();
    } finally {
      ids.forEach((id) => setBusy(id, false));
    }
  }

  // Deselect every visible posting that is currently marked selected
  // (used on the SELECTED tab to wipe the queue in one click).
  async function deselectAllOnPage() {
    const targetIds = items
      .filter((i) => statusByPosting[i.opportunity_id] === "selected"
        || i.status === "shortlisted")
      .map((i) => i.opportunity_id);
    if (!targetIds.length) return;
    const ok = window.confirm(
      `Deselect all ${targetIds.length} selected postings on this page?`,
    );
    if (!ok) return;
    targetIds.forEach((id) => setBusy(id, true));
    try {
      await Promise.allSettled(
        targetIds.map(async (id) => {
          try {
            await api.unselectPosting(id);
            setStatus(id, undefined);
          } catch (e) {
            setError(String(e));
          }
        }),
      );
      if (tier === "selected") {
        setItems([]);
        if (page > 1) setPage((p) => p - 1);
      }
      loadCounts();
    } finally {
      targetIds.forEach((id) => setBusy(id, false));
    }
  }

  function selectAllVisible() {
    setSelectedIds(new Set(filtered.map((i) => i.opportunity_id)));
  }

  const filterCount =
    filters.function.length +
    filters.industry.length +
    filters.city.length +
    filters.ai_subtype.length +
    (filters.ai_role ? 1 : 0);

  const hasActiveFilter = filterCount > 0 || filters.mode === "hide";

  return (
    <div className="space-y-4">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      {addToast && (
        <Card className="flex items-center gap-2 border-success/40 bg-success/10 p-3 text-sm">
          <Check className="h-4 w-4 text-success" />
          <span>{addToast}</span>
          {goToTab && (
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              onClick={() => goToTab("applications")}
            >
              View in Applications
              <ArrowRight className="h-3 w-3" />
            </Button>
          )}
        </Card>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">Shortlist</span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="accent"
            onClick={() => setAddOpen(true)}
          >
            <Plus className="h-3 w-3" />
            Add Job
          </Button>
          <a
            href={api.shortlistExportUrl(false)}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted/40"
            download
          >
            Export CSV
          </a>
        </div>
      </div>

      <EmailMonitorPanel />

      <Card className="p-3">
        <button
          type="button"
          onClick={() => setFiltersExpanded((v) => !v)}
          className="flex w-full items-center justify-between text-left"
          aria-expanded={filtersExpanded}
        >
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">Filters</span>
            {filterCount > 0 && (
              <Badge variant="accent">{filterCount} applied</Badge>
            )}
            {filters.mode === "hide" && (
              <Badge variant="warning">HIDE mode</Badge>
            )}
          </div>
          {filtersExpanded ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>

        {filtersExpanded && (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <MultiSelectDropdown
                label="Function"
                options={filterOptions.functions}
                selected={filters.function}
                onChange={(v) => setAxis("function", v)}
              />
              <MultiSelectDropdown
                label="Industry"
                options={filterOptions.industries}
                selected={filters.industry}
                onChange={(v) => setAxis("industry", v)}
              />
              <MultiSelectDropdown
                label="City"
                options={filterOptions.cities}
                selected={filters.city}
                onChange={(v) => setAxis("city", v)}
              />
              <Select
                value={filters.ai_role || "_all"}
                onValueChange={(v) =>
                  setAxis("ai_role", v === "_all" ? "" : v)
                }
              >
                <SelectTrigger className="h-9 w-44 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_all">All roles</SelectItem>
                  <SelectItem value="ai_only">AI roles only</SelectItem>
                  <SelectItem value="non_ai_only">Non-AI roles only</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <Separator />

            <div className="flex flex-wrap items-center gap-3">
              <ToggleGroup
                type="single"
                value={filters.mode}
                onValueChange={(v) => v && setMode(v)}
                aria-label="Filter mode"
              >
                <ToggleGroupItem value="show">Show</ToggleGroupItem>
                <ToggleGroupItem value="hide">Hide</ToggleGroupItem>
              </ToggleGroup>
              <Button
                size="sm"
                variant="outline"
                onClick={resetFilters}
                disabled={!hasActiveFilter}
              >
                <RotateCcw className="h-3 w-3" />
                Reset all
              </Button>
            </div>
          </div>
        )}
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          Posted:
        </span>
        <ToggleGroup
          type="single"
          value={dateRange}
          onValueChange={(v) => v && changeDateRange(v)}
          aria-label="Time range"
        >
          {TIME_RANGES.map((r) => (
            <ToggleGroupItem key={r.id} value={r.id}>
              {r.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <ToggleGroup
          type="single"
          value={tier}
          onValueChange={(v) => v && changeTier(v)}
          aria-label="Tier filter"
        >
          {TIER_FILTERS.map((f) => (
            <ToggleGroupItem key={f.id} value={f.id}>
              {f.label} ({tabCount(f.id)})
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="w-[200px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(SORTS).map(([key, { label }]) => (
              <SelectItem key={key} value={key}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {tier !== "selected" && (
          <Button
            size="sm"
            variant="outline"
            onClick={selectAllOnPage}
            disabled={items.length === 0}
            title="Select every posting on this page"
          >
            <Check className="h-3 w-3" />
            Select All
          </Button>
        )}
        {tier === "selected" && (
          <Button
            size="sm"
            variant="outline"
            onClick={deselectAllOnPage}
            disabled={items.length === 0}
            className="border-destructive/40 text-destructive hover:bg-destructive/10"
            title="Deselect every selected posting on this page"
          >
            <X className="h-3 w-3" />
            Deselect All
          </Button>
        )}

        <span className="ml-auto text-xs text-muted-foreground">
          {activeCount === 0
            ? "No postings"
            : `Showing ${rangeStart}-${rangeEnd} of ${activeCount}`}
        </span>
        {inSessionSelected > 0 && goToTab && (
          <Button
            variant="accent"
            size="sm"
            onClick={() => goToTab("prompts")}
          >
            Generate prompts
            <ArrowRight className="h-3 w-3" />
          </Button>
        )}
      </div>

      {loading && (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-[140px] w-full" />
          ))}
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <Card className="border-dashed p-10 text-center text-sm text-muted-foreground">
          No postings match the current filters.
        </Card>
      )}

      {inSessionSelected > 0 && (
        <Card className="flex flex-wrap items-center gap-2 border-accent/40 bg-accent/5 p-3">
          <span className="text-sm font-semibold">
            {inSessionSelected} selected
          </span>
          <Button variant="ghost" size="sm" onClick={selectAllVisible}>
            Select all visible ({filtered.length})
          </Button>
          <Button variant="ghost" size="sm" onClick={clearSelection}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="accent"
              onClick={onGeneratePromptsForSelected}
            >
              <Copy className="h-3 w-3" />
              Generate prompts ({inSessionSelected})
            </Button>
            <Button
              size="sm"
              variant="success"
              onClick={onApplyToSelected}
            >
              <Send className="h-3 w-3" />
              Apply to selected ({inSessionSelected})
            </Button>
          </div>
        </Card>
      )}

      {!loading && filtered.length > 0 && (
        <div className="space-y-3">
          {filtered.map((item) => (
            <PostingCard
              key={item.opportunity_id}
              item={item}
              status={
                tier === "selected"
                  ? "selected"
                  : statusByPosting[item.opportunity_id]
              }
              busy={!!busyByPosting[item.opportunity_id]}
              onSelect={onSelect}
              onUnselect={onUnselect}
              onSkip={onSkip}
              onUnskip={onUnskip}
              onFlag={openFlagModal}
            />
          ))}
        </div>
      )}

      {!loading && activeCount > PER_PAGE && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="h-3 w-3" />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next
            <ChevronRight className="h-3 w-3" />
          </Button>
        </div>
      )}

      {flagModal && (
        <Dialog open={true} onOpenChange={(o) => !o && setFlagModal(null)}>
          <DialogContent className="max-h-[86vh] max-w-2xl overflow-y-auto p-0">
            <DialogHeader className="border-b border-border p-4">
              <DialogTitle>Flag bad match</DialogTitle>
              <div className="mt-1 text-sm text-muted-foreground">
                {flagModal.item.employer} — {flagModal.item.title}
              </div>
            </DialogHeader>
            <div className="space-y-4 p-4 text-sm">
              <div className="rounded-md border border-border bg-muted/30 p-3">
                This will be removed from your shortlist and used as a
                negative training example for future evaluations.
              </div>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={flagModal.createRule}
                  onChange={(e) =>
                    setFlagModal((m) => ({
                      ...m, createRule: e.target.checked,
                    }))
                  }
                  className="h-4 w-4 cursor-pointer"
                />
                <span>
                  Also create a permanent rule to filter similar postings
                </span>
              </label>
              {flagModal.createRule && (
                <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
                  <div className="text-xs text-muted-foreground">
                    A rule matches if every non-empty field matches.
                    Leave a field blank to make it a wildcard. Employer
                    and Title use *, ? wildcards; the others are exact
                    match.
                  </div>
                  {[
                    ["Employer (wildcard)", "employer_pattern"],
                    ["Title contains (wildcard)", "title_pattern"],
                    ["Industry (exact)", "industry_pattern"],
                    ["Function (exact)", "function_pattern"],
                    ["AI subtype (exact)", "ai_subtype_pattern"],
                  ].map(([label, field]) => (
                    <div key={field} className="flex items-center gap-2">
                      <label className="w-44 text-xs font-medium">
                        {label}
                      </label>
                      <input
                        type="text"
                        value={flagModal.rule[field] || ""}
                        onChange={(e) =>
                          setFlagRule(field, e.target.value)
                        }
                        className="flex-1 rounded border border-input bg-background px-2 py-1 text-xs"
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2 border-t border-border p-3">
              <Button variant="outline" onClick={() => setFlagModal(null)}>
                Cancel
              </Button>
              <Button
                variant="warning"
                onClick={() => submitFlag(false)}
              >
                <AlertTriangle className="h-3 w-3" />
                Flag without rule
              </Button>
              <Button
                variant="destructive"
                disabled={!flagModal.createRule}
                onClick={() => submitFlag(true)}
              >
                <AlertTriangle className="h-3 w-3" />
                Flag and create rule
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {batchModal && (
        <Dialog open={true} onOpenChange={(o) => !o && setBatchModal(null)}>
          <DialogContent className="max-h-[86vh] max-w-3xl overflow-hidden p-0">
            <DialogHeader className="border-b border-border p-4">
              <DialogTitle>
                Batch prompts — {batchModal.count} posting
                {batchModal.count === 1 ? "" : "s"}
              </DialogTitle>
            </DialogHeader>
            <pre className="m-0 max-h-[60vh] overflow-auto p-4 font-mono text-xs leading-relaxed">
              {batchModal.text}
            </pre>
            <div className="flex justify-end gap-2 border-t border-border p-3">
              <Button
                variant="outline"
                onClick={() => setBatchModal(null)}
              >
                Close
              </Button>
              <Button variant="accent" onClick={copyBatchToClipboard}>
                <Copy className="h-3 w-3" />
                {copyFlash ? "Copied!" : "Copy to clipboard"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}

      <AddJobModal
        open={addOpen}
        onOpenChange={setAddOpen}
        onAdded={onJobAdded}
      />
    </div>
  );
}
