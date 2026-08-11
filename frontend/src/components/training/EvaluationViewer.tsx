import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Legend } from "recharts";
import type { ComparisonResult } from "@/data/trainingMockData";

const metricsData = [
  { metric: "Accuracy", student: 91.5, teacher: 96.8 },
  { metric: "F1 Score", student: 90.2, teacher: 95.4 },
  { metric: "Precision", student: 92.1, teacher: 96.0 },
  { metric: "Recall", student: 88.3, teacher: 94.8 },
  { metric: "Coherence", student: 89.0, teacher: 97.2 },
];

const latencyData = [
  { label: "Student SLM", avg: 39, p95: 52, p99: 68 },
  { label: "Teacher LLM", avg: 401, p95: 520, p99: 680 },
];

export function EvaluationViewer({ comparisons }: { comparisons: ComparisonResult[] }) {
  return (
    <div className="space-y-6">
      {/* Metrics Overview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Radar Chart */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Performance Comparison</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={metricsData}>
                  <PolarGrid className="stroke-border" />
                  <PolarAngleAxis dataKey="metric" tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }} />
                  <PolarRadiusAxis angle={90} domain={[0, 100]} tick={{ fontSize: 10 }} />
                  <Radar name="Student SLM" dataKey="student" stroke="hsl(243,75%,59%)" fill="hsl(243,75%,59%)" fillOpacity={0.2} strokeWidth={2} />
                  <Radar name="Teacher LLM" dataKey="teacher" stroke="hsl(142,71%,45%)" fill="hsl(142,71%,45%)" fillOpacity={0.1} strokeWidth={2} strokeDasharray="4 4" />
                  <Legend iconType="line" />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Latency Comparison */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Latency (ms)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={latencyData} layout="vertical" barGap={4}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis type="number" tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }} />
                  <YAxis dataKey="label" type="category" tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }} width={90} />
                  <Tooltip contentStyle={{ backgroundColor: "hsl(0,0%,100%)", border: "1px solid hsl(220,13%,91%)", borderRadius: "8px", fontSize: 12 }} />
                  <Bar dataKey="avg" name="Avg" fill="hsl(243,75%,59%)" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="p95" name="P95" fill="hsl(38,92%,50%)" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="p99" name="P99" fill="hsl(0,84%,60%)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 flex items-center justify-center gap-4 text-xs text-muted-foreground">
              <span>Student is <span className="font-bold text-success">~10x faster</span> than Teacher</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: "Avg Student Score", value: "91.5%", sub: "accuracy" },
          { label: "Avg Teacher Score", value: "96.8%", sub: "accuracy" },
          { label: "Performance Ratio", value: "94.5%", sub: "student/teacher" },
          { label: "Speed Improvement", value: "10.3x", sub: "faster inference" },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="p-4 text-center">
              <p className="text-xl font-bold text-foreground">{s.value}</p>
              <p className="text-[10px] text-muted-foreground">{s.label}</p>
              <p className="text-[9px] text-muted-foreground/60 mt-0.5">{s.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Side-by-Side Comparisons */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Side-by-Side Output Comparison</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {comparisons.map((cmp) => (
            <div key={cmp.id} className="border border-border rounded-lg overflow-hidden">
              {/* Input */}
              <div className="bg-secondary/50 px-4 py-2.5 border-b border-border">
                <p className="text-[10px] font-semibold text-muted-foreground mb-0.5">INPUT</p>
                <p className="text-sm text-foreground">{cmp.input}</p>
              </div>
              {/* Outputs */}
              <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border">
                <div className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <Badge className="text-[10px]">Student SLM</Badge>
                    <div className="flex gap-2 text-[10px] text-muted-foreground">
                      <span>{cmp.studentLatencyMs}ms</span>
                      <span className="font-medium text-foreground">{cmp.studentScore}%</span>
                    </div>
                  </div>
                  <p className="text-xs font-mono text-foreground bg-accent/50 rounded-md px-2.5 py-2">{cmp.studentOutput}</p>
                </div>
                <div className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <Badge variant="outline" className="text-[10px]">Teacher LLM</Badge>
                    <div className="flex gap-2 text-[10px] text-muted-foreground">
                      <span>{cmp.teacherLatencyMs}ms</span>
                      <span className="font-medium text-foreground">{cmp.teacherScore}%</span>
                    </div>
                  </div>
                  <p className="text-xs font-mono text-foreground bg-accent/50 rounded-md px-2.5 py-2">{cmp.teacherOutput}</p>
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
