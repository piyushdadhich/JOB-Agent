import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function ThresholdBlock({ thresholds }) {
  if (!thresholds) return null;
  return (
    <div className="grid grid-cols-3 gap-2 text-xs">
      <div>
        <div className="text-muted-foreground">TOP_TIER ≥</div>
        <div className="text-base font-semibold">
          {thresholds.top.toFixed(2)}
        </div>
      </div>
      <div>
        <div className="text-muted-foreground">STRONG ≥</div>
        <div className="text-base font-semibold">
          {thresholds.strong.toFixed(2)}
        </div>
      </div>
      <div>
        <div className="text-muted-foreground">EXPLORATORY ≥</div>
        <div className="text-base font-semibold">
          {thresholds.exploratory.toFixed(2)}
        </div>
      </div>
    </div>
  );
}

function SampleList({ title, items, onConfirm, busy }) {
  if (items === null) return <Skeleton className="h-32 w-full" />;
  return (
    <Card>
      <CardContent className="space-y-2 p-4 text-sm">
        <div className="font-semibold">{title}</div>
        {items.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            No scored postings in this band yet.
          </div>
        ) : (
          <ul className="space-y-1 text-xs">
            {items.map((p) => (
              <li key={p.opportunity_id} className="flex justify-between gap-2">
                <span className="min-w-0 truncate">
                  <strong>{p.employer}</strong> · {p.title}
                </span>
                <span className="shrink-0 text-muted-foreground">
                  {p.tier} · {p.fit_score?.toFixed(2) ?? "?"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex gap-2 pt-1">
          <Button size="sm" onClick={() => onConfirm(true)} disabled={busy}>
            Yes — looks right
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onConfirm(false)}
            disabled={busy}
          >
            No — adjust
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Calibration() {
  const [auto, setAuto] = useState(null);
  const [thresholds, setThresholds] = useState(null);
  const [sample, setSample] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [applyResult, setApplyResult] = useState(null);
  // Tracks user feedback within a single tune round; we apply
  // guided_tune only when both top + bottom have been answered.
  const [pendingTop, setPendingTop] = useState(null);
  const [pendingBottom, setPendingBottom] = useState(null);

  const loadAuto = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.calibrateAuto();
      setAuto(r);
      setThresholds(r.thresholds);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const loadSample = async () => {
    setBusy(true);
    setError(null);
    try {
      setSample(await api.calibrateSample());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    (async () => {
      await loadAuto();
      await loadSample();
    })();
  }, []);

  const confirm = async (topYes, bottomYes) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.calibrateConfirm({
        current: thresholds,
        user_confirms_top: topYes,
        user_confirms_bottom: bottomYes,
      });
      setThresholds(r.thresholds);
      setPendingTop(null);
      setPendingBottom(null);
      // Refresh the sample list against the new bucket boundaries.
      await loadSample();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  // When both top + bottom have feedback, run guided_tune.
  useEffect(() => {
    if (pendingTop !== null && pendingBottom !== null) {
      confirm(pendingTop, pendingBottom);
    }
  }, [pendingTop, pendingBottom]);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.calibrateApply(thresholds);
      setApplyResult(r);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 p-4 text-sm">
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-base font-semibold">Recommended thresholds</h2>
            <Button size="sm" variant="outline" onClick={loadAuto} disabled={busy}>
              Re-auto
            </Button>
          </div>
          {auto === null ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <>
              <ThresholdBlock thresholds={thresholds} />
              <div className="text-xs text-muted-foreground">
                Inventory: {auto.inventory_skill_count} skills · Scored:{" "}
                {auto.distribution.total} postings
                {" · "}
                TOP {auto.distribution.top_tier_count}
                {" · "}STRONG {auto.distribution.strong_count}
                {" · "}EXPL {auto.distribution.exploratory_count}
                {" · "}SKIP {auto.distribution.skip_count}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <SampleList
          title="Top 10 — do these look like jobs you'd apply to?"
          items={sample?.top ?? null}
          busy={busy}
          onConfirm={(yes) => setPendingTop(yes)}
        />
        <SampleList
          title="Bottom 10 — are these genuinely bad fits?"
          items={sample?.bottom ?? null}
          busy={busy}
          onConfirm={(yes) => setPendingBottom(yes)}
        />
      </div>

      {error && (
        <Card className="border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
          {error}
        </Card>
      )}

      <Card>
        <CardContent className="space-y-2 p-4">
          <div className="flex items-baseline justify-between">
            <div>
              <h2 className="text-base font-semibold">Apply</h2>
              <p className="text-xs text-muted-foreground">
                Writes thresholds to profile YAML and re-buckets every
                scored posting in place (overall scores don't change).
              </p>
            </div>
            <Button onClick={apply} disabled={busy || !thresholds}>
              Apply + rebucket
            </Button>
          </div>
          {applyResult && (
            <div className="text-xs text-success">
              Wrote {applyResult.profile_yaml}; rebucketed{" "}
              {applyResult.rebucketed} posting(s).
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
