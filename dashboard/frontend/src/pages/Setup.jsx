import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import FirstRunProgress from "@/pages/setup/FirstRunProgress.jsx";
import GmailStep from "@/pages/setup/GmailStep.jsx";
import HardwareStep from "@/pages/setup/HardwareStep.jsx";
import InventoryStep from "@/pages/setup/InventoryStep.jsx";
import LLMConfigStep from "@/pages/setup/LLMConfigStep.jsx";
import ProfileStep from "@/pages/setup/ProfileStep.jsx";
import ScheduleStep from "@/pages/setup/ScheduleStep.jsx";
import SourcesStep from "@/pages/setup/SourcesStep.jsx";
import TargetMarketStep from "@/pages/setup/TargetMarketStep.jsx";

const STEP_TITLES = [
  "Welcome",
  "LLM configuration",
  "Profile",
  "Career inventory",
  "Target market",
  "Sources",
  "Gmail (optional)",
  "Schedule",
];

function StepStub({ stepNumber, onContinue }) {
  // Placeholder used for any step not yet implemented. Lets the
  // wizard skeleton advance end-to-end through the spec build
  // order even before every step component lands.
  return (
    <Card>
      <CardContent className="space-y-3 p-6 text-sm">
        <p className="font-semibold">Step {stepNumber} not yet built.</p>
        <p className="text-muted-foreground">
          Tasks 2–8 of Spec 1 land each step component in turn.
          Clicking <strong>Continue</strong> records the step as
          done so you can navigate through the wizard skeleton.
        </p>
        <div className="flex justify-end">
          <Button onClick={() => onContinue({})}>Continue</Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Setup({ onComplete }) {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(null);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setError(null);
    try {
      const [s, c] = await Promise.all([
        api.setupStatus(),
        api.setupConfig(),
      ]);
      setStatus(s);
      setConfig(c);
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const submitStep = async (stepNumber, payload) => {
    setSubmitting(true);
    try {
      const result = await api.setupSaveStep(stepNumber, payload);
      // We do NOT short-circuit out of Setup on completed=true.
      // The FirstRunProgress hand-off (rendered below when
      // status.completed) needs to mount, kick off the discovery
      // pass, and only THEN call onComplete to flip the app into
      // the dashboard view.
      setStatus({
        ...status,
        current_step: result.current_step,
        completed: result.completed,
      });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (status === null) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-6">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const stepIndex = Math.max(1, status.current_step);
  const total = status.total_steps || 8;
  const title = STEP_TITLES[stepIndex - 1] || `Step ${stepIndex}`;

  const hardwareTier = config?.steps?.["1"]?.tier;

  let StepComponent;
  if (stepIndex === 1) {
    StepComponent = (
      <HardwareStep
        onContinue={(payload) => submitStep(1, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 2) {
    StepComponent = (
      <LLMConfigStep
        hardwareTier={hardwareTier}
        onContinue={(payload) => submitStep(2, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 3) {
    StepComponent = (
      <ProfileStep
        onContinue={(payload) => submitStep(3, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 4) {
    StepComponent = (
      <InventoryStep
        onContinue={(payload) => submitStep(4, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 5) {
    StepComponent = (
      <TargetMarketStep
        onContinue={(payload) => submitStep(5, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 6) {
    StepComponent = (
      <SourcesStep
        onContinue={(payload) => submitStep(6, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 7) {
    StepComponent = (
      <GmailStep
        onContinue={(payload) => submitStep(7, payload)}
        disabled={submitting}
      />
    );
  } else if (stepIndex === 8) {
    StepComponent = (
      <ScheduleStep
        onContinue={(payload) => submitStep(8, payload)}
        disabled={submitting}
      />
    );
  } else {
    StepComponent = (
      <StepStub
        stepNumber={stepIndex}
        onContinue={(payload) => submitStep(stepIndex, payload)}
      />
    );
  }

  if (status.completed) {
    // After Step 8 marks completed=true the wizard cursor is at
    // total_steps but state.completed is also true. We hand off
    // to FirstRunProgress for the discovery run; once it finishes
    // the parent App.jsx flips into the normal dashboard.
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6">
        <h1 className="text-xl font-semibold">First run</h1>
        <FirstRunProgress onFinish={() => onComplete?.()} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">{title}</h1>
        <span className="text-xs text-muted-foreground">
          Step {stepIndex} of {total}
        </span>
      </div>

      {error && (
        <Card className="border-destructive/40 bg-destructive/10 p-3 text-sm">
          {error}
        </Card>
      )}

      {StepComponent}
    </div>
  );
}
