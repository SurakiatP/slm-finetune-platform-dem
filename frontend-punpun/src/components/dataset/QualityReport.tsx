import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertTriangle, CheckCircle2, Info, Sparkles, Copy, Trash2 } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { computeQualityReport } from "@/lib/qualityCalculator";
import { useLanguage } from "@/i18n/LanguageContext";

const severityColor = {
  critical: "text-destructive border-destructive/30 bg-destructive/5",
  warning: "text-yellow-700 dark:text-yellow-400 border-yellow-500/30 bg-yellow-500/5",
  info: "text-blue-700 dark:text-blue-400 border-blue-500/30 bg-blue-500/5",
};

const severityIcon = {
  critical: AlertTriangle,
  warning: AlertTriangle,
  info: Info,
};

export function QualityReport({ datasetId }: { datasetId: string }) {
  const report = useMemo(() => computeQualityReport(datasetId), [datasetId]);
  const { t } = useLanguage();

  const scoreColor =
    report.overallScore >= 85
      ? "text-emerald-500"
      : report.overallScore >= 65
      ? "text-yellow-500"
      : "text-destructive";

  const distColors = [
    "hsl(var(--primary))",
    "hsl(var(--primary)/0.85)",
    "hsl(var(--primary)/0.7)",
    "hsl(var(--primary)/0.55)",
    "hsl(var(--primary)/0.4)",
    "hsl(var(--destructive)/0.7)",
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="md:col-span-1">
          <CardContent className="p-4 text-center space-y-2">
            <p className="text-xs text-muted-foreground">{t("quality.overallScore")}</p>
            <div className={`text-4xl font-bold ${scoreColor}`}>{report.overallScore}</div>
            <Progress value={report.overallScore} className="h-2" />
            <p className="text-[10px] text-muted-foreground">
              {report.overallScore >= 85
                ? t("quality.excellent")
                : report.overallScore >= 65
                ? t("quality.needsWork")
                : t("quality.poor")}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {report.totalRows.toLocaleString()} {t("quality.analyzedRows")}
            </p>
          </CardContent>
        </Card>

        {[
          { label: t("quality.duplicates"), value: report.duplicateRows, icon: Copy },
          { label: t("quality.missingLabels"), value: report.missingLabels, icon: Trash2 },
          { label: t("quality.outliers"), value: report.outliers, icon: AlertTriangle },
        ].map((item) => (
          <Card key={item.label}>
            <CardContent className="p-4 flex items-center gap-3">
              <div className="p-2 rounded-lg bg-accent">
                <item.icon className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold text-foreground">{item.value}</p>
                <p className="text-xs text-muted-foreground">{item.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {report.classDistribution.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("quality.classDistribution")}</CardTitle>
              <CardDescription className="text-xs">{t("quality.classDistDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={report.classDistribution} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis type="number" tick={{ fontSize: 10 }} />
                    <YAxis type="category" dataKey="label" tick={{ fontSize: 10 }} width={80} />
                    <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                      {report.classDistribution.map((_, i) => (
                        <Cell key={i} fill={distColors[i % distColors.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        )}

        <Card className={report.classDistribution.length > 0 ? "" : "lg:col-span-2"}>
          <CardHeader>
            <CardTitle className="text-sm">{t("quality.lengthDistribution")}</CardTitle>
            <CardDescription className="text-xs">{t("quality.lengthDistDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={report.lengthDistribution}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="count" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" /> {t("quality.aiSuggestions")}
          </CardTitle>
          <CardDescription className="text-xs">{t("quality.aiSuggestionsDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {report.issues.length === 0 ? (
            <Alert className="border-emerald-500/30 bg-emerald-500/5">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <AlertDescription className="text-sm text-foreground">
                {t("quality.noIssues")}
              </AlertDescription>
            </Alert>
          ) : (
            report.issues.map((issue) => {
              const Icon = severityIcon[issue.severity];
              return (
                <Alert key={issue.id} className={severityColor[issue.severity]}>
                  <Icon className="h-4 w-4" />
                  <AlertDescription>
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <span className="font-semibold text-sm text-foreground">{issue.title}</span>
                      <Badge variant="outline" className="text-[9px] shrink-0">
                        {issue.severity}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground mb-2">{issue.description}</p>
                    <div className="flex items-start gap-1.5 text-xs">
                      <CheckCircle2 className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                      <span className="text-foreground">{issue.suggestion}</span>
                    </div>
                  </AlertDescription>
                </Alert>
              );
            })
          )}
        </CardContent>
      </Card>
    </div>
  );
}
