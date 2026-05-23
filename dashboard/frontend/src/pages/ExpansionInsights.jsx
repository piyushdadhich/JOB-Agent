import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  Check,
  Eye,
  EyeOff,
  PlayCircle,
  RefreshCw,
  Sparkles,
  Target,
  X,
} from "lucide-react";
import { api } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

function formatTimestamp(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function ConfidenceBar({ value }) {
  const pct = Math.max(0, Math.min(100, Math.round((value || 0) * 100)));
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-32 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-accent"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

function StatusBadge({ status }) {
  if (status === "confirmed") {
    return <Badge variant="success">Confirmed</Badge>;
  }
  if (status === "rejected") {
    return <Badge variant="muted">Rejected</Badge>;
  }
  return <Badge variant="warning">Pending</Badge>;
}

export default function ExpansionInsights() {
  const [summary, setSummary] = useState(null);
  const [clusters, setClusters] = useState(null);
  const [targets, setTargets] = useState(null);
  const [gaps, setGaps] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);

  const loadAll = useCallback(async () => {
    setError(null);
    try {
      const [s, c, t, g] = await Promise.all([
        api.expansionSummary(),
        api.expansionTitleClusters(),
        api.expansionDeepTargets(),
        api.expansionSkillGaps(),
      ]);
      setSummary(s);
      setClusters(c);
      setTargets(t);
      setGaps(g);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const runNow = async () => {
    setRunning(true);
    setError(null);
    try {
      await api.expansionRun();
      await loadAll();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setRunning(false);
    }
  };

  const confirmCluster = async (id) => {
    try {
      const updated = await api.expansionConfirmCluster(id);
      setClusters((prev) =>
        (prev || []).map((c) => (c.id === id ? updated : c)),
      );
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const rejectCluster = async (id) => {
    try {
      const updated = await api.expansionRejectCluster(id);
      setClusters((prev) =>
        (prev || []).map((c) => (c.id === id ? updated : c)),
      );
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const toggleWatch = async (target) => {
    try {
      const fn = target.is_watched
        ? api.expansionUnwatchTarget
        : api.expansionWatchTarget;
      const updated = await fn(target.company_id);
      setTargets((prev) =>
        (prev || []).map((t) =>
          t.company_id === target.company_id ? updated : t,
        ),
      );
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const setGapAction = async (skillId, action) => {
    try {
      const updated = await api.expansionGapAction(skillId, action);
      setGaps((prev) =>
        (prev || []).map((g) =>
          g.skill_id === skillId ? updated : g,
        ),
      );
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  return (
    <div className="space-y-6">
      {error && (
        <Card className="flex items-center gap-2 border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive-foreground">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </Card>
      )}

      {/* Section 1: Summary */}
      <Card>
        <CardContent className="flex items-center justify-between gap-4 p-4">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-1 h-5 w-5 text-accent" />
            <div>
              <h2 className="text-sm font-semibold">
                Discovery Expansion
              </h2>
              {summary ? (
                <p className="text-xs text-muted-foreground">
                  Last analysis:{" "}
                  {formatTimestamp(summary.generated_at)} ·{" "}
                  {summary.title_clusters_count} role clusters ·{" "}
                  {summary.deep_targets_count} go-deep targets ·{" "}
                  {summary.skill_gaps_count} skill gaps
                </p>
              ) : (
                <Skeleton className="h-4 w-72" />
              )}
            </div>
          </div>
          <Button onClick={runNow} disabled={running} size="sm">
            {running ? (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <PlayCircle className="mr-2 h-4 w-4" />
            )}
            {running ? "Running…" : "Run Now"}
          </Button>
        </CardContent>
      </Card>

      {/* Section 2: Title clusters */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Title clusters</h3>
        {clusters === null && (
          <Skeleton className="h-20 w-full" />
        )}
        {clusters && clusters.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No clusters of 3+ similar postings in the lookback window.
          </p>
        )}
        {clusters &&
          clusters.map((c) => (
            <Card key={c.id}>
              <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-base font-medium">
                      {c.modal_title}
                    </span>
                    <StatusBadge status={c.status} />
                    {c.already_in_target && (
                      <Badge variant="accent">Already tracking</Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Found in {c.posting_count} postings at:{" "}
                    {(c.example_employers || []).join(", ") || "—"}
                  </p>
                  <ConfidenceBar value={c.confidence} />
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant={
                      c.status === "confirmed" ? "secondary" : "default"
                    }
                    onClick={() => confirmCluster(c.id)}
                    disabled={c.status === "confirmed"}
                  >
                    <Check className="mr-1 h-4 w-4" />
                    Confirm
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => rejectCluster(c.id)}
                    disabled={c.status === "rejected"}
                  >
                    <X className="mr-1 h-4 w-4" />
                    Reject
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
      </section>

      <Separator />

      {/* Section 3: Go-deep targets */}
      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Target className="h-4 w-4" /> Go-deep employer targets
        </h3>
        {targets === null && <Skeleton className="h-20 w-full" />}
        {targets && targets.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No employers with 2+ STRONG/TOP_TIER postings recently.
          </p>
        )}
        {targets && targets.length > 0 && (
          <Card>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-border text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2">Company</th>
                    <th className="px-4 py-2">Strong</th>
                    <th className="px-4 py-2">Top tier</th>
                    <th className="px-4 py-2">ATS</th>
                    <th className="px-4 py-2 text-right">Watch</th>
                  </tr>
                </thead>
                <tbody>
                  {targets.map((t) => (
                    <tr
                      key={t.company_id}
                      className="border-b border-border last:border-0"
                    >
                      <td className="px-4 py-2 font-medium">
                        {t.company_name}
                      </td>
                      <td className="px-4 py-2">{t.strong_count}</td>
                      <td className="px-4 py-2">{t.top_tier_count}</td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">
                        {t.ats_platform
                          ? `${t.ats_platform}${
                              t.ats_slug ? "/" + t.ats_slug : ""
                            }`
                          : "—"}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <Button
                          size="sm"
                          variant={t.is_watched ? "secondary" : "outline"}
                          onClick={() => toggleWatch(t)}
                        >
                          {t.is_watched ? (
                            <>
                              <Eye className="mr-1 h-4 w-4" />
                              Watching
                            </>
                          ) : (
                            <>
                              <EyeOff className="mr-1 h-4 w-4" />
                              Watch
                            </>
                          )}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}
      </section>

      <Separator />

      {/* Section 4: Skill gaps */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Inventory skill gaps</h3>
        {gaps === null && <Skeleton className="h-20 w-full" />}
        {gaps && gaps.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No recurring skill gaps from EXPLORATORY postings.
          </p>
        )}
        {gaps &&
          gaps.map((g) => (
            <Card key={g.skill_id}>
              <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">
                      {g.skill_label}
                    </span>
                    <Badge variant="muted">
                      {g.occurrence_count} postings
                    </Badge>
                    {g.action === "in_inventory" && (
                      <Badge variant="success">
                        <Check className="mr-1 h-3 w-3" />
                        In inventory
                      </Badge>
                    )}
                    {g.action === "to_learn" && (
                      <Badge variant="accent">
                        <Check className="mr-1 h-3 w-3" />
                        To learn
                      </Badge>
                    )}
                  </div>
                  {g.example_postings && g.example_postings.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      Examples: {g.example_postings.slice(0, 3).join("; ")}
                    </p>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant={
                      g.action === "in_inventory" ? "secondary" : "default"
                    }
                    onClick={() => setGapAction(g.skill_id, "in_inventory")}
                  >
                    I have this skill
                  </Button>
                  <Button
                    size="sm"
                    variant={
                      g.action === "to_learn" ? "secondary" : "outline"
                    }
                    onClick={() => setGapAction(g.skill_id, "to_learn")}
                  >
                    Want to learn
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
      </section>
    </div>
  );
}
