import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { X } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

const STORAGE_KEY = "onboarding-completed";

const steps = [
  { key: "welcome", target: null },
  { key: "sidebar", target: "[data-sidebar]" },
  { key: "newProject", target: null },
  { key: "models", target: null },
  { key: "playground", target: null },
  { key: "analytics", target: null },
  { key: "done", target: null },
];

export function OnboardingTour() {
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);
  const { t } = useLanguage();

  useEffect(() => {
    if (!localStorage.getItem(STORAGE_KEY)) {
      const timer = setTimeout(() => setActive(true), 1000);
      return () => clearTimeout(timer);
    }
  }, []);

  const handleClose = () => {
    setActive(false);
    localStorage.setItem(STORAGE_KEY, "true");
  };

  const handleNext = () => {
    if (step < steps.length - 1) setStep(step + 1);
    else handleClose();
  };

  const handleSkip = () => handleClose();

  if (!active) return null;

  const currentStep = steps[step];

  return (
    <div className="fixed inset-0 z-[100]">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={handleSkip} />
      <div className="absolute inset-0 flex items-center justify-center p-4">
        <Card className="relative w-full max-w-md shadow-2xl border-primary/20 animate-in fade-in zoom-in-95 duration-300">
          <Button variant="ghost" size="icon" className="absolute top-2 right-2 h-7 w-7" onClick={handleClose}>
            <X className="h-4 w-4" />
          </Button>
          <CardContent className="p-6 space-y-4">
            <div className="text-center space-y-2">
              <p className="text-3xl">{getStepEmoji(currentStep.key)}</p>
              <h3 className="text-lg font-bold text-foreground">{t(`onboarding.${currentStep.key}.title`)}</h3>
              <p className="text-sm text-muted-foreground">{t(`onboarding.${currentStep.key}.desc`)}</p>
            </div>

            <div className="flex justify-center gap-1.5">
              {steps.map((_, i) => (
                <div
                  key={i}
                  className={`h-1.5 rounded-full transition-all ${
                    i === step ? "w-6 bg-primary" : i < step ? "w-1.5 bg-primary/50" : "w-1.5 bg-muted"
                  }`}
                />
              ))}
            </div>

            <div className="flex justify-between">
              <Button variant="ghost" size="sm" onClick={handleSkip}>
                {t("onboarding.skip")}
              </Button>
              <Button size="sm" onClick={handleNext}>
                {step === steps.length - 1 ? t("onboarding.getStarted") : t("onboarding.next")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function getStepEmoji(key: string): string {
  const map: Record<string, string> = {
    welcome: "👋",
    sidebar: "📋",
    newProject: "🚀",
    models: "🤖",
    playground: "🎮",
    analytics: "📊",
    done: "🎉",
  };
  return map[key] || "✨";
}

export function restartOnboarding() {
  localStorage.removeItem(STORAGE_KEY);
  window.location.reload();
}
