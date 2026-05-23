import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const TIER_BLURB = {
  minimum:
    "Run on the API-LLM path. The local pipeline will rely on hosted " +
    "providers (Claude, OpenAI, Gemini) for evaluation and resume " +
    "drafting.",
  recommended:
    "Mid-tier hardware. Local evaluator runs comfortably; complex " +
    "tasks (resume generation) fall back to a hosted LLM.",
  full:
    "Plenty of headroom. The full local pipeline runs without API " +
    "calls — including resume + cover-letter generation.",
};

export default function HardwareStep({ onContinue, disabled }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await api.setupHardware();
        if (!cancelled) setInfo(h);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <Card className="border-destructive/40 bg-destructive/10 p-3 text-sm">
        Hardware detection failed: {error}
      </Card>
    );
  }
  if (info === null) {
    return <Skeleton className="h-40 w-full" />;
  }

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-sm">
        <p className="text-muted-foreground">
          Detected hardware on this machine. The recommended tier
          drives the default LLM-routing choices in step 2.
        </p>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="text-xs text-muted-foreground">OS</div>
            <div className="font-medium">
              {info.os_name} {info.os_version}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">CPU</div>
            <div className="font-medium">
              {info.cpu_model}{" "}
              <span className="text-xs text-muted-foreground">
                ({info.cpu_cores} cores)
              </span>
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">RAM</div>
            <div className="font-medium">
              {info.ram_total_gb.toFixed(1)} GB total
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">GPU</div>
            <div className="font-medium">
              {info.gpu_model ?? "None detected"}
              {info.gpu_vram_gb !== null && info.gpu_vram_gb > 0 ? (
                <span className="text-xs text-muted-foreground">
                  {" "}
                  ({info.gpu_vram_gb.toFixed(1)} GB VRAM)
                </span>
              ) : info.gpu_type === "apple_silicon" ? (
                <span className="text-xs text-muted-foreground">
                  {" "}
                  (unified memory)
                </span>
              ) : null}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Disk free</div>
            <div className="font-medium">
              {info.disk_free_gb.toFixed(0)} GB
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">
              Recommended tier
            </div>
            <div className="font-medium capitalize">{info.tier}</div>
          </div>
        </div>

        <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
          {TIER_BLURB[info.tier] ?? "Unknown tier."}
        </div>

        <div className="flex justify-end">
          <Button
            onClick={() =>
              onContinue({
                tier: info.tier,
                gpu_type: info.gpu_type,
                ram_total_gb: info.ram_total_gb,
              })
            }
            disabled={disabled}
          >
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
