import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card, CardContent } from "@/components/ui/card";

export default function SkillGapHint({ goToTab }) {
  const [gaps, setGaps] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api
      .gapsTop(5)
      .then((r) => {
        if (!cancelled) setGaps(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error || gaps === null || gaps.length === 0) return null;

  return (
    <Card>
      <CardContent className="space-y-2 p-4 text-sm">
        <div className="flex items-baseline justify-between">
          <div className="font-semibold">Top skill gaps</div>
          {goToTab && (
            <button
              type="button"
              onClick={() => goToTab("expansion")}
              className="text-xs text-accent underline"
            >
              See all in Expansion →
            </button>
          )}
        </div>
        <ul className="space-y-1 text-xs">
          {gaps.map((g) => (
            <li key={g.skill_id} className="flex justify-between gap-2">
              <span>{g.skill_label}</span>
              <span className="text-muted-foreground">
                {g.occurrence_count} posting(s)
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
