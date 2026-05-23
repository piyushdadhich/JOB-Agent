import { useEffect, useState } from "react";
import { api } from "@/api";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const PROVIDER_LABELS = {
  anthropic_sonnet:  "Claude Sonnet",
  anthropic_opus:    "Claude Opus",
  openai_gpt4o:      "GPT-4o",
  openai_gpt4o_mini: "GPT-4o mini",
  gemini_pro:        "Gemini Pro",
  gemini_flash:      "Gemini Flash",
  local:             "Local (Ollama)",
};

export default function ResumeCostCard() {
  const [appsPerWeek, setAppsPerWeek] = useState(5);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    api
      .resumeCostEstimate(appsPerWeek)
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [appsPerWeek]);

  return (
    <Card>
      <CardContent className="space-y-3 p-4 text-sm">
        <div className="flex items-baseline justify-between">
          <div className="font-semibold">Resume-generation cost</div>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Apps/week:</span>
            <Input
              type="number"
              min={0}
              max={200}
              value={appsPerWeek}
              onChange={(e) =>
                setAppsPerWeek(Number(e.target.value) || 0)
              }
              className="w-20"
            />
          </div>
        </div>

        {error && (
          <div className="text-xs text-destructive">{error}</div>
        )}

        {data === null && !error && <Skeleton className="h-32 w-full" />}

        {data && (
          <table className="w-full text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="text-left">Provider</th>
                <th className="text-right">Per app</th>
                <th className="text-right">Monthly</th>
                <th className="text-right">Annual</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.provider}>
                  <td>{PROVIDER_LABELS[r.provider] || r.provider}</td>
                  <td className="text-right font-mono">
                    {r.per_app_usd === 0
                      ? "—"
                      : `$${r.per_app_usd.toFixed(3)}`}
                  </td>
                  <td className="text-right font-mono">
                    {r.monthly_usd === 0
                      ? "—"
                      : `$${r.monthly_usd.toFixed(2)}`}
                  </td>
                  <td className="text-right font-mono">
                    {r.annual_usd === 0
                      ? "—"
                      : `$${r.annual_usd.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="text-[11px] text-muted-foreground">
          Assumes ~3K input + 1K output tokens per resume.
          Rates per Appendix C of the GitHub Release spec; refresh
          when providers re-price.
        </div>
      </CardContent>
    </Card>
  );
}
