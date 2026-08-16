import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { Upload, FileText, X, FileCode, Loader2, CheckCircle2, Info, RefreshCw } from "lucide-react";
import { toErrorDetail, type ProjectFormData } from "@/pages/NewProject";
import { useTaskExample, useUploadSeedDataset, useGenerateDataset, useSdgPipelineModels, queryKeys } from "@/hooks/queries";
import { getDataset } from "@/api/endpoints/datasets";
import type { JobStatus } from "@/api/types";

const fileIcons: Record<string, React.ElementType> = {
  json: FileCode,
  jsonl: FileCode,
};

const isTerminal = (status: JobStatus | null | undefined) =>
  status === "completed" || status === "failed" || status === "cancelled";

interface DataUploadStepProps {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
  projectId: string | null;
}

export function DataUploadStep({ formData, updateForm, projectId }: DataUploadStepProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [seedName, setSeedName] = useState("");
  const [numSamples, setNumSamples] = useState("200");
  const [holdoutSize, setHoldoutSize] = useState("0");
  const [temperature, setTemperature] = useState("0.9");
  const [datasetName, setDatasetName] = useState("");

  const taskType = formData.taskType;
  const { data: example } = useTaskExample(taskType ?? undefined, !!taskType);
  const { data: pipelineModels } = useSdgPipelineModels();
  const uploadSeed = useUploadSeedDataset();
  const generateDataset = useGenerateDataset();

  // Poll the SDG target dataset until it leaves pending/running — this is
  // what unblocks "Next" (canProceed requires status === 'completed').
  const trainingDatasetId = formData.trainingDatasetId;
  const { data: trainingDataset } = useQuery({
    queryKey: trainingDatasetId ? queryKeys.dataset(trainingDatasetId) : ["datasets", "detail", "__none__"],
    queryFn: () => getDataset(trainingDatasetId!),
    enabled: !!trainingDatasetId && !isTerminal(formData.trainingDatasetStatus),
    refetchInterval: 2000,
  });

  useEffect(() => {
    if (trainingDataset && trainingDataset.status !== formData.trainingDatasetStatus) {
      updateForm({ trainingDatasetStatus: trainingDataset.status });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trainingDataset]);

  const handleFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    const f = fileList.item(0);
    if (f) setFile(f);
  };

  const getIcon = (name: string) => {
    const ext = name.split(".").pop()?.toLowerCase() || "";
    const Icon = fileIcons[ext] || FileText;
    return <Icon className="h-4 w-4" />;
  };

  const handleUpload = () => {
    if (!file || !projectId || !taskType) return;
    uploadSeed.mutate(
      { project_id: projectId, task_type: taskType, file, name: seedName.trim() || undefined },
      {
        onSuccess: (res) => {
          updateForm({ seedDatasetId: res.dataset_id, trainingDatasetId: null, trainingDatasetStatus: null });
        },
      },
    );
  };

  const handleGenerate = () => {
    if (!projectId || !taskType || !formData.seedDatasetId) return;
    generateDataset.mutate(
      {
        project_id: projectId,
        task_type: taskType,
        task_description: formData.taskPrompt,
        num_samples: Number(numSamples) || 200,
        holdout_size: Number(holdoutSize) || 0,
        temperature: Number(temperature) || 0.9,
        dataset_name: datasetName.trim() || null,
        sdg_mode: "with_seed",
        seed_dataset_id: formData.seedDatasetId,
      },
      {
        onSuccess: (res) => {
          updateForm({ trainingDatasetId: res.dataset_id, trainingDatasetStatus: res.status });
        },
      },
    );
  };

  const resetGeneration = () => {
    updateForm({ trainingDatasetId: null, trainingDatasetStatus: null });
  };

  const removeFile = () => setFile(null);

  const pipelineRows = [
    { role: "Generator", model: pipelineModels?.generator },
    { role: "Judge", model: pipelineModels?.judge },
    { role: "Diversity rules", model: pipelineModels?.diversity_rules },
  ];

  const seedUploaded = !!formData.seedDatasetId;
  const status = formData.trainingDatasetStatus;

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Upload Training Data</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Upload a seed file, then generate a full synthetic training set from it.
        </p>
      </div>

      {!projectId && (
        <p className="text-xs text-muted-foreground">Waiting for the project to be created…</p>
      )}

      {!seedUploaded && (
        <>
          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onDrop={(e) => { e.preventDefault(); e.stopPropagation(); handleFiles(e.dataTransfer.files); }}
            className="border-2 border-dashed border-border rounded-lg p-10 text-center cursor-pointer hover:border-primary/50 hover:bg-accent/50 transition-colors"
          >
            <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
            <p className="text-sm font-medium text-foreground">Click to upload or drag & drop</p>
            <p className="text-xs text-muted-foreground mt-1">
              {taskType === "qa" ? "JSON/JSONL up to 10MB · QA PDF up to 25MB" : "JSON/JSONL up to 10MB"}
            </p>
            <input
              ref={inputRef}
              type="file"
              accept={taskType === "qa" ? ".json,.jsonl,.pdf" : ".json,.jsonl"}
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>

          {file && (
            <div className="space-y-2">
              <div className="flex items-center gap-3 p-2.5 rounded-md bg-secondary/50 border border-border">
                <div className="text-muted-foreground">{getIcon(file.name)}</div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{file.name}</p>
                  <p className="text-[10px] text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
                </div>
                <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={removeFile}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>

              <div>
                <Label className="text-xs">Seed dataset name (optional)</Label>
                <Input
                  className="mt-1"
                  placeholder={file.name.replace(/\.(jsonl|json|pdf)$/i, "")}
                  value={seedName}
                  onChange={(e) => setSeedName(e.target.value)}
                />
              </div>

              <Button
                type="button"
                onClick={handleUpload}
                disabled={!projectId || uploadSeed.isPending}
                className="gap-2"
              >
                {uploadSeed.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Upload seed file
              </Button>
            </div>
          )}

          {uploadSeed.isError && <ErrorDetail error={toErrorDetail(uploadSeed.error)} />}
        </>
      )}

      {seedUploaded && !formData.trainingDatasetId && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-foreground">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            Seed dataset uploaded
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <Label className="text-xs">Samples</Label>
              <Input
                className="mt-1"
                type="number"
                min={1}
                max={10000}
                value={numSamples}
                onChange={(e) => setNumSamples(e.target.value)}
              />
            </div>
            <div>
              <Label className="text-xs">Holdout rows</Label>
              <Input
                className="mt-1"
                type="number"
                min={0}
                max={2000}
                value={holdoutSize}
                onChange={(e) => setHoldoutSize(e.target.value)}
              />
            </div>
            <div>
              <Label className="text-xs">Temperature</Label>
              <Input
                className="mt-1"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
              />
            </div>
          </div>

          <div>
            <Label className="text-xs">Training dataset name (optional)</Label>
            <Input className="mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
          </div>

          <div className="flex items-start gap-2 rounded-md border border-border bg-secondary/30 p-3">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <div className="text-xs text-muted-foreground">
              <p className="font-medium text-foreground">Pipeline models (fixed by the platform)</p>
              <dl className="mt-1 space-y-0.5 font-mono text-[11px]">
                {pipelineRows.map((m) => (
                  <div key={m.role} className="flex gap-2">
                    <dt className="w-28 shrink-0">{m.role}:</dt>
                    <dd className="text-muted-foreground/80">{m.model ?? "—"}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>

          <Button type="button" onClick={handleGenerate} disabled={generateDataset.isPending} className="gap-2">
            {generateDataset.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            Generate training dataset
          </Button>

          {generateDataset.isError && <ErrorDetail error={toErrorDetail(generateDataset.error)} />}
        </div>
      )}

      {formData.trainingDatasetId && (
        <div className="rounded-lg border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-foreground">Synthetic data generation</p>
            <Badge variant={status === "completed" ? "default" : status === "failed" ? "destructive" : "outline"}>
              {status ?? "pending"}
            </Badge>
          </div>
          {!isTerminal(status) && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Generating rows from your seed dataset…
            </div>
          )}
          {status === "completed" && trainingDataset && (
            <p className="text-xs text-muted-foreground">
              {trainingDataset.num_samples.toLocaleString()} rows ready for training.
            </p>
          )}
          {status === "failed" && (
            <>
              <ErrorDetail error={{ detail: trainingDataset?.error_message ?? "Dataset generation failed." }} />
              <Button type="button" variant="outline" size="sm" className="gap-2" onClick={resetGeneration}>
                <RefreshCw className="h-3.5 w-3.5" /> Try again
              </Button>
            </>
          )}
        </div>
      )}

      {/* Format guide — the sample now comes from the Engine's per-task
          example (GET /api/v1/tasks/{task_type}/example) instead of a
          hard-coded classification snippet. */}
      {example && (
        <div className="bg-accent/50 rounded-lg p-4 space-y-2">
          <p className="text-xs font-semibold text-foreground">Expected Format</p>
          <div className="bg-background rounded-md p-3 font-mono text-[11px] text-muted-foreground overflow-x-auto">
            <pre>{JSON.stringify(example, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
