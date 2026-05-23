import { useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function ScheduleStep({ onContinue, disabled }) {
  const [scheduleTime, setScheduleTime] = useState("02:00");
  const [installResult, setInstallResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const install = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupInstallService(scheduleTime);
      setInstallResult(r);
      if (!r.ok) setError(r.detail || "install-service failed");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const finalise = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.setupComplete();
      onContinue({ schedule_time: scheduleTime, installed: !!installResult?.ok });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-pl text-sm">
        <p className="text-muted-foreground">
          Pick when the daily pipeline should run. If your machine is
          off at that hour, the service starts as soon as you next
          log in (Persistent=true on the systemd / launchd / Task
          Scheduler entries).
        </p>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Daily run time (HH:MM, 24-hour, local)
          </label>
          <Input
            value={scheduleTime}
            onChange={(e) => setScheduleTime(e.target.value)}
            placeholder="02:00"
            disabled={busy}
          />
        </div>

        <div className="space-y-2">
          <Button onClick={install} disabled={busy || disabled}>
            {busy && !installResult ? "Installing…" : "Install services"}
          </Button>
          {installResult && installResult.ok && (
            <div className="text-xs text-success">
              Background services installed. Dashboard auto-starts at
              logon; pipeline runs daily at {scheduleTime}.
            </div>
          )}
          {installResult && !installResult.ok && (
            <div className="text-xs text-destructive">
              Install failed: {installResult.detail}
            </div>
          )}
        </div>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => onContinue({ skipped_install: true })}
            disabled={busy || disabled}
          >
            Skip install + finish
          </Button>
          <Button
            onClick={finalise}
            disabled={busy || disabled || !installResult?.ok}
          >
            Finish setup
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
