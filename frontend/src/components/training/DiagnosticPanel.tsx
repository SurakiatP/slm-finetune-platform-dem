import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertOctagon, Lightbulb, Wrench, ExternalLink } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

export interface FailureDiagnosis {
  rootCause: string;
  confidence: number;
  symptoms: string[];
  fixes: { title: string; description: string; recommended?: boolean }[];
  similarCases: number;
}

const mockDiagnosis: FailureDiagnosis = {
  rootCause: "Learning rate too high — gradient explosion",
  confidence: 92,
  symptoms: [
    "Loss became NaN at epoch 2, step 145",
    "Gradient norm spiked from 1.2 → 847.3 in 8 steps",
    "Validation loss diverged before train loss",
  ],
  fixes: [
    {
      title: "Reduce learning rate from 5e-4 to 1e-4",
      description: "Most common fix. Smaller LR prevents gradient explosion in early epochs.",
      recommended: true,
    },
    {
      title: "Enable gradient clipping (max_norm=1.0)",
      description: "Caps gradient magnitude to prevent NaN. Recommended for all runs >3 epochs.",
    },
    {
      title: "Use warmup steps (500 steps linear warmup)",
      description: "Gradually increases LR from 0 → target, stabilizing early training.",
    },
  ],
  similarCases: 247,
};

export function DiagnosticPanel({ projectStatus }: { projectStatus: string }) {
  const { t } = useLanguage();

  if (projectStatus !== "failed") return null;

  const d = mockDiagnosis;

  return (
    <Card className="border-destructive/30 bg-destructive/5">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="text-sm flex items-center gap-2 text-destructive">
              <AlertOctagon className="h-4 w-4" />
              {t("diagnostic.title")}
            </CardTitle>
            <CardDescription className="text-xs">{t("diagnostic.subtitle")}</CardDescription>
          </div>
          <Badge variant="outline" className="text-[10px] shrink-0">
            {d.confidence}% {t("diagnostic.confidence")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Alert className="border-destructive/30 bg-background">
          <Lightbulb className="h-4 w-4 text-yellow-500" />
          <AlertDescription>
            <p className="text-xs text-muted-foreground mb-1">{t("diagnostic.rootCause")}</p>
            <p className="text-sm font-semibold text-foreground">{d.rootCause}</p>
          </AlertDescription>
        </Alert>

        <div>
          <p className="text-xs font-semibold text-foreground mb-2">{t("diagnostic.symptoms")}</p>
          <ul className="space-y-1.5">
            {d.symptoms.map((s, i) => (
              <li key={i} className="text-xs text-muted-foreground flex items-start gap-2">
                <span className="text-destructive mt-0.5">•</span>
                <code className="font-mono">{s}</code>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="text-xs font-semibold text-foreground mb-2 flex items-center gap-1.5">
            <Wrench className="h-3.5 w-3.5" /> {t("diagnostic.suggestedFixes")}
          </p>
          <div className="space-y-2">
            {d.fixes.map((fix, i) => (
              <div
                key={i}
                className={`p-3 rounded-lg border bg-background space-y-1 ${
                  fix.recommended ? "border-primary/40" : "border-border"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-medium text-foreground">{fix.title}</p>
                  {fix.recommended && (
                    <Badge className="text-[9px] shrink-0 bg-primary/15 text-primary border-primary/30" variant="outline">
                      {t("diagnostic.recommended")}
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{fix.description}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between pt-2 border-t border-border">
          <p className="text-[11px] text-muted-foreground">
            {t("diagnostic.similarCases").replace("{n}", String(d.similarCases))}
          </p>
          <Button size="sm" variant="outline" className="gap-1.5 text-xs h-7">
            <ExternalLink className="h-3 w-3" /> {t("diagnostic.retryWithFix")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
