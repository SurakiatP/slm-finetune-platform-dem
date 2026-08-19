import { useState, useEffect } from "react";
import { PageTransition, FadeIn } from "@/components/motion";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Play, CheckCircle2, AlertCircle, RefreshCw, BarChart2, XCircle } from "lucide-react";
import {
  engineListEvaluations,
  engineStartEvaluation,
  engineCancelEvaluation,
  engineCompareEvaluations,
  type EngineEvaluation,
  type EngineEvaluationComparison,
} from "@/lib/engineApi";
import { useToast } from "@/hooks/use-toast";

export default function Evaluations() {
  const [evaluations, setEvaluations] = useState<EngineEvaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [modelArtifactId, setModelArtifactId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [evalName, setEvalName] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);
  const [comparison, setComparison] = useState<EngineEvaluationComparison | null>(null);
  const { toast } = useToast();

  const fetchEvaluations = async () => {
    setLoading(true);
    try {
      const page = await engineListEvaluations();
      setEvaluations(page.items);
    } catch (err) {
      toast({ title: "Failed to load evaluations", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvaluations();
  }, []);

  const handleStartEval = async () => {
    if (!modelArtifactId || !datasetId) {
      toast({ title: "Validation error", description: "Model artifact ID and Dataset ID are required", variant: "destructive" });
      return;
    }
    try {
      await engineStartEvaluation({
        model_artifact_id: modelArtifactId,
        dataset_id: datasetId,
        eval_name: evalName || undefined,
      });
      toast({ title: "Evaluation started" });
      setDialogOpen(false);
      fetchEvaluations();
    } catch (err) {
      toast({ title: "Error starting evaluation", description: (err as Error).message, variant: "destructive" });
    }
  };

  const handleCancelEval = async (id: string) => {
    try {
      await engineCancelEvaluation(id);
      toast({ title: "Evaluation cancelled" });
      fetchEvaluations();
    } catch (err) {
      toast({ title: "Error cancelling evaluation", description: (err as Error).message, variant: "destructive" });
    }
  };

  const handleCompare = async () => {
    if (selectedForCompare.length < 2) {
      toast({ title: "Select at least 2 evaluations to compare" });
      return;
    }
    try {
      const result = await engineCompareEvaluations(selectedForCompare);
      setComparison(result);
    } catch (err) {
      toast({ title: "Error comparing evaluations", description: (err as Error).message, variant: "destructive" });
    }
  };

  const toggleSelectCompare = (id: string) => {
    setSelectedForCompare((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  return (
    <PageTransition>
      <div className="space-y-6">
        <FadeIn>
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold">Model Evaluations</h1>
              <p className="text-muted-foreground text-sm">Run task-specific metrics and LLM-as-a-judge scoring on fine-tuned models</p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={fetchEvaluations}>
                <RefreshCw className="h-4 w-4 mr-2" /> Refresh
              </Button>
              {selectedForCompare.length >= 2 && (
                <Button variant="secondary" size="sm" onClick={handleCompare}>
                  <BarChart2 className="h-4 w-4 mr-2" /> Compare ({selectedForCompare.length})
                </Button>
              )}
              <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogTrigger asChild>
                  <Button size="sm"><Play className="h-4 w-4 mr-2" /> New Evaluation</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Run Model Evaluation</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-4 py-2">
                    <div>
                      <label className="text-xs font-semibold">Evaluation Name (Optional)</label>
                      <Input placeholder="e.g. Classification Benchmark v1" value={evalName} onChange={(e) => setEvalName(e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs font-semibold">Model Artifact ID</label>
                      <Input placeholder="UUID of trained model artifact" value={modelArtifactId} onChange={(e) => setModelArtifactId(e.target.value)} />
                    </div>
                    <div>
                      <label className="text-xs font-semibold">Dataset ID (Holdout / Test)</label>
                      <Input placeholder="UUID of test dataset" value={datasetId} onChange={(e) => setDatasetId(e.target.value)} />
                    </div>
                    <Button className="w-full" onClick={handleStartEval}>Start Evaluation Job</Button>
                  </div>
                </DialogContent>
              </Dialog>
            </div>
          </div>
        </FadeIn>

        {comparison && (
          <FadeIn>
            <Card className="border-primary/40 bg-primary/5">
              <CardHeader className="flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-sm">Evaluation Comparison</CardTitle>
                  <CardDescription className="text-xs">Metrics side-by-side</CardDescription>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setComparison(null)}>Close</Button>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Metric</TableHead>
                      {comparison.evaluations.map((ev) => (
                        <TableHead key={ev.id}>{ev.eval_name || ev.id.slice(0, 8)}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.keys(comparison.comparison).map((metricKey) => (
                      <TableRow key={metricKey}>
                        <TableCell className="font-semibold text-xs">{metricKey}</TableCell>
                        {comparison.evaluations.map((ev) => (
                          <TableCell key={ev.id} className="text-xs font-mono">
                            {comparison.comparison[metricKey]?.[ev.id] ?? "—"}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </FadeIn>
        )}

        <FadeIn delay={0.1}>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Recent Evaluation Runs</CardTitle>
            </CardHeader>
            <CardContent>
              {loading ? (
                <p className="text-xs text-muted-foreground py-4 text-center">Loading evaluation runs...</p>
              ) : evaluations.length === 0 ? (
                <p className="text-xs text-muted-foreground py-4 text-center">No evaluations found. Start your first evaluation job!</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">Select</TableHead>
                      <TableHead>Eval Name / ID</TableHead>
                      <TableHead>Model Artifact</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Metrics</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {evaluations.map((ev) => (
                      <TableRow key={ev.id}>
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={selectedForCompare.includes(ev.id)}
                            onChange={() => toggleSelectCompare(ev.id)}
                          />
                        </TableCell>
                        <TableCell className="font-medium text-xs">
                          {ev.eval_name || ev.id.slice(0, 8)}
                        </TableCell>
                        <TableCell className="text-xs font-mono text-muted-foreground">
                          {ev.model_artifact_id.slice(0, 8)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={ev.status === "completed" ? "default" : ev.status === "failed" ? "destructive" : "secondary"}>
                            {ev.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs font-mono">
                          {ev.metrics ? JSON.stringify(ev.metrics) : "—"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(ev.created_at).toLocaleDateString()}
                        </TableCell>
                        <TableCell className="text-right">
                          {(ev.status === "pending" || ev.status === "running") && (
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => handleCancelEval(ev.id)} title="Cancel">
                              <XCircle className="h-4 w-4" />
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
