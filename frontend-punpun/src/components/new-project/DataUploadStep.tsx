import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { Upload, FileText, X, FileCode, Loader2, CheckCircle2, Info, RefreshCw, Wand2 } from "lucide-react";
import { toErrorDetail, type ProjectFormData } from "@/pages/NewProject";
import {
  useTaskExample,
  useUploadSeedDataset,
  useUploadDataset,
  useGenerateDataset,
  useSdgPipelineModels,
  useDatasets,
  useProjects,
  queryKeys,
} from "@/hooks/queries";
import { getDataset } from "@/api/endpoints/datasets";
import { datasetRoleTag } from "@/lib/labels";
import type { Dataset, JobStatus, ToolDefinition } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";

const fileIcons: Record<string, React.ElementType> = {
  json: FileCode,
  jsonl: FileCode,
};

const isTerminal = (status: JobStatus | null | undefined) =>
  status === "completed" || status === "failed" || status === "cancelled";

// Example placeholder for the tool-definitions JSON editor (no-seed +
// tool_calling). Kept as a plain literal — it's a code sample, not prose,
// so it isn't run through i18n.
const toolsPlaceholder = `[
  {
    "name": "set_oven",
    "description": "Set oven temperature",
    "parameters": {"celsius": {"type": "integer", "required": true}}
  }
]`;

type SdgMode = "with_seed" | "description_only";
type DataMode = SdgMode | "existing";

interface DataUploadStepProps {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
  projectId: string | null;
}

export function DataUploadStep({ formData, updateForm, projectId }: DataUploadStepProps) {
  const { t } = useLanguage();
  const inputRef = useRef<HTMLInputElement>(null);
  // Whether the current trainingDatasetId came from the "existing" tab is
  // carried on the form itself (`trainingDatasetPicked`), not inferred from
  // "has a trainingDatasetId but no seed" — a description_only SDG run also
  // has no seed, so inferring made a real no-seed generation look "picked"
  // when this step remounts (the wizard unmounts it whenever it leaves step
  // 2), hiding its progress card. Keeping it on the form also survives that
  // remount, which a local useState could not.
  const pickedExisting = formData.trainingDatasetPicked;
  const [mode, setMode] = useState<DataMode>(pickedExisting ? "existing" : "with_seed");
  const [file, setFile] = useState<File | null>(null);
  const [seedName, setSeedName] = useState("");
  const [numSamples, setNumSamples] = useState("200");
  const [holdoutSize, setHoldoutSize] = useState("0");
  const [temperature, setTemperature] = useState("0.9");
  const [datasetName, setDatasetName] = useState("");
  const [holdoutName, setHoldoutName] = useState("");
  // No-seed (description_only) mode only — a single base name, from which
  // `${base}-training` / `${base}-hold-out` are derived at generate time.
  const [baseName, setBaseName] = useState("");
  // No-seed (description_only) mode only — per-task-type generation config.
  const [labelsText, setLabelsText] = useState("");
  const [toolsText, setToolsText] = useState("");
  const [toolsFormatError, setToolsFormatError] = useState<string | null>(null);

  const taskType = formData.taskType;
  const { data: example } = useTaskExample(taskType ?? undefined, !!taskType);
  const { data: pipelineModels } = useSdgPipelineModels();
  const uploadSeed = useUploadSeedDataset();
  const generateDataset = useGenerateDataset();
  const uploadDataset = useUploadDataset();

  // "existing" tab: any completed, non-empty, non-holdout dataset for this
  // task type is pickable — including seed uploads and prior SDG output,
  // but not the internal hold-out splits (those exist only for eval).
  const { data: existingDatasetsPage } = useDatasets(undefined, { limit: 200 });
  const { data: existingProjectsPage } = useProjects({ limit: 200 });
  const pickableDatasets: Dataset[] = (existingDatasetsPage?.items ?? []).filter(
    (d) =>
      d.task_type === taskType &&
      d.status === "completed" &&
      d.num_samples > 0 &&
      !!d.storage_uri &&
      !d.parent_dataset_id,
  );
  const projectNameFor = (d: Dataset): string => {
    const project = existingProjectsPage?.items.find((p) => p.id === d.project_id);
    return project?.name ?? t("dataUpload.existingOrphan");
  };

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
    // Naming convention: SDG-launched datasets default to `${seedName}-training`
    // and `${seedName}-hold-out` so the seed → training/holdout lineage is
    // obvious from the dataset list without opening each one.
    const effectiveSeedName = seedName.trim() || file.name.replace(/\.(jsonl|json|pdf)$/i, "");
    uploadSeed.mutate(
      { project_id: projectId, task_type: taskType, file, name: seedName.trim() || undefined },
      {
        onSuccess: (res) => {
          setDatasetName(`${effectiveSeedName}-training`);
          setHoldoutName(`${effectiveSeedName}-hold-out`);
          updateForm({
            seedDatasetId: res.dataset_id,
            trainingDatasetId: null,
            trainingDatasetStatus: null,
            // Clears any dataset previously chosen on the "existing" tab —
            // this seed upload is now the source of truth for this step.
            trainingDatasetPicked: false,
          });
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
        holdout_name: (Number(holdoutSize) || 0) > 0 ? holdoutName.trim() || undefined : undefined,
        temperature: Number(temperature) || 0.9,
        dataset_name: datasetName.trim() || null,
        sdg_mode: "with_seed",
        seed_dataset_id: formData.seedDatasetId,
      },
      {
        onSuccess: (res) => {
          updateForm({
            trainingDatasetId: res.dataset_id,
            trainingDatasetStatus: res.status,
            trainingDatasetPicked: false,
          });
        },
      },
    );
  };

  // --- No-seed (description_only) mode helpers --------------------------

  const parseLabels = (): string[] =>
    [...new Set(labelsText.split(",").map((l) => l.trim()).filter(Boolean))];

  const parseTools = (): ToolDefinition[] | null => {
    if (!toolsText.trim()) return null;
    try {
      const parsed = JSON.parse(toolsText) as unknown;
      if (!Array.isArray(parsed) || parsed.length === 0) return null;
      const tools = parsed as ToolDefinition[];
      const names = tools.map((tool) => tool.name);
      if (names.some((n) => typeof n !== "string" || !n.trim())) return null;
      if (new Set(names).size !== names.length) return null;
      return tools;
    } catch {
      return null;
    }
  };

  // Pretty-print the tool definitions JSON. Tolerates the common copy-paste
  // failure where line wrapping injects raw newlines/tabs inside string
  // literals ("Bad control character") by collapsing control whitespace to a
  // single space before parsing — then re-indents the result.
  const formatToolsJson = () => {
    if (!toolsText.trim()) return;
    const tryParse = (s: string) => JSON.parse(s) as unknown;
    try {
      setToolsText(JSON.stringify(tryParse(toolsText), null, 2));
      setToolsFormatError(null);
    } catch {
      try {
        setToolsText(JSON.stringify(tryParse(toolsText.replace(/[\r\n\t]+/g, " ")), null, 2));
        setToolsFormatError(null);
      } catch {
        setToolsFormatError(t("sdgNoSeed.toolsError"));
      }
    }
  };

  // `datasetName`/`holdoutName` (with-seed) and `baseName` (no-seed) are
  // separate naming schemes: with-seed prefills `${seed}-training` /
  // `${seed}-hold-out` on upload, while no-seed mode derives the same
  // suffixes from a single user-typed base name. Switching tabs therefore
  // parks the current mode's own name state and restores the incoming
  // mode's own (empty until typed/uploaded) instead of letting one mode's
  // values leak into the other's fields.
  const namesByMode = useRef<{
    with_seed: { dataset: string; holdout: string };
    description_only: { base: string };
  }>({
    with_seed: { dataset: "", holdout: "" },
    description_only: { base: "" },
  });

  const handleModeChange = (next: DataMode) => {
    if (next === mode) return;
    // The name-parking dance only applies between the two SDG modes —
    // "existing" has no dataset/holdout name fields of its own.
    if (mode === "with_seed") {
      namesByMode.current.with_seed = { dataset: datasetName, holdout: holdoutName };
    } else if (mode === "description_only") {
      namesByMode.current.description_only = { base: baseName };
    }
    if (next === "with_seed") {
      setDatasetName(namesByMode.current.with_seed.dataset);
      setHoldoutName(namesByMode.current.with_seed.holdout);
    } else if (next === "description_only") {
      setBaseName(namesByMode.current.description_only.base);
    }
    setMode(next);
  };

  // --- "existing" mode helpers --------------------------------------------

  const handlePickExisting = (datasetId: string) => {
    const picked = pickableDatasets.find((d) => d.id === datasetId);
    if (!picked) return;
    updateForm({
      trainingDatasetId: picked.id,
      trainingDatasetStatus: "completed",
      trainingDatasetPicked: true,
    });
  };

  const handleUploadExisting = () => {
    if (!file || !projectId || !taskType) return;
    // No dedicated name field on this tab (unlike the with-seed upload
    // above) — the backend derives a name from the filename when omitted.
    uploadDataset.mutate(
      { project_id: projectId, task_type: taskType, file },
      {
        onSuccess: (res) => {
          updateForm({
            trainingDatasetId: res.dataset_id,
            trainingDatasetStatus: "completed",
            trainingDatasetPicked: true,
          });
          setFile(null);
        },
      },
    );
  };

  const holdoutRequired = (Number(holdoutSize) || 0) > 0;

  // No-seed mode names are derived from a single base name rather than
  // typed separately: `${base}-training` always, `${base}-hold-out` only
  // when holdout rows > 0 (mirrors the with-seed upload prefill convention).
  const deriveNoSeedNames = (base: string) => ({
    dataset: `${base}-training`,
    holdout: holdoutRequired ? `${base}-hold-out` : undefined,
  });

  // Renders `t("sdgNoSeed.derivedNamesPreview")` (a "{train} and {holdout}"
  // style template) with the derived names filled in. When there's no
  // holdout split, the `{holdout}` placeholder — along with whatever single
  // connector word/particle precedes it (e.g. "and" / "และ") — is dropped
  // so the sentence still reads naturally instead of trailing off.
  const noSeedNamesPreview = (): string | null => {
    const base = baseName.trim();
    if (!base) return null;
    const { dataset, holdout } = deriveNoSeedNames(base);
    const template = t("sdgNoSeed.derivedNamesPreview");
    if (holdout) {
      return template.replace("{train}", dataset).replace("{holdout}", holdout);
    }
    const idx = template.indexOf("{holdout}");
    if (idx === -1) return template.replace("{train}", dataset);
    const trimmedPrefix = template.slice(0, idx).replace(/\s*\S+\s*$/, " ");
    const suffix = template.slice(idx + "{holdout}".length);
    return (trimmedPrefix + suffix).replace("{train}", dataset).replace(/\s+/g, " ").trim();
  };

  const canGenerateNoSeed = (): boolean => {
    if (!projectId || !taskType) return false;
    if (!baseName.trim()) return false;
    if (taskType === "classification") return parseLabels().length >= 2;
    if (taskType === "tool_calling") return parseTools() !== null;
    return true; // qa needs only the task description already gathered in step 1.
  };

  const handleGenerateNoSeed = () => {
    if (!projectId || !taskType || !canGenerateNoSeed()) return;
    const labels = taskType === "classification" ? parseLabels() : null;
    const tools = taskType === "tool_calling" ? parseTools() : null;
    const { dataset, holdout } = deriveNoSeedNames(baseName.trim());
    generateDataset.mutate(
      {
        project_id: projectId,
        task_type: taskType,
        task_description: formData.taskPrompt,
        num_samples: Number(numSamples) || 200,
        holdout_size: Number(holdoutSize) || 0,
        holdout_name: holdout,
        temperature: Number(temperature) || 0.9,
        dataset_name: dataset,
        sdg_mode: "description_only",
        classification_config: labels ? { labels } : undefined,
        tool_calling_config: tools ? { tool_definitions: tools } : undefined,
      },
      {
        onSuccess: (res) => {
          updateForm({
            trainingDatasetId: res.dataset_id,
            trainingDatasetStatus: res.status,
            trainingDatasetPicked: false,
          });
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

  const pipelineInfoBox = (
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
  );

  const seedUploaded = !!formData.seedDatasetId;
  const status = formData.trainingDatasetStatus;
  // A dataset picked/uploaded on the "existing" tab also sets
  // trainingDatasetId, but it never went through SDG generation — gating the
  // tab strip and the SDG progress card on trainingDatasetId alone would hide
  // the tabs and show a bogus "Synthetic data generation" card for it.
  const generationStarted = !!formData.trainingDatasetId && !pickedExisting;

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Upload Training Data</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {mode === "with_seed"
            ? "Upload a seed file, then generate a full synthetic training set from it."
            : mode === "existing"
              ? t("dataUpload.existingHint")
              : t("sdgNoSeed.subtitle")}
        </p>
      </div>

      {!projectId && (
        <p className="text-xs text-muted-foreground">Waiting for the project to be created…</p>
      )}

      {!generationStarted && (
        <Tabs value={mode} onValueChange={(v) => handleModeChange(v as DataMode)}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="with_seed">{t("sdgNoSeed.modeWithSeed")}</TabsTrigger>
            <TabsTrigger value="description_only">{t("sdgNoSeed.modeNoSeed")}</TabsTrigger>
            <TabsTrigger value="existing">{t("dataUpload.modeExisting")}</TabsTrigger>
          </TabsList>
        </Tabs>
      )}

      {mode === "with_seed" && !generationStarted && (
        <>
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

          {seedUploaded && (
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

              {holdoutRequired && (
                <div>
                  <Label className="text-xs">Holdout dataset name (optional)</Label>
                  <Input className="mt-1" value={holdoutName} onChange={(e) => setHoldoutName(e.target.value)} />
                </div>
              )}

              {pipelineInfoBox}

              <Button type="button" onClick={handleGenerate} disabled={generateDataset.isPending} className="gap-2">
                {generateDataset.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Generate training dataset
              </Button>

              {generateDataset.isError && <ErrorDetail error={toErrorDetail(generateDataset.error)} />}
            </div>
          )}
        </>
      )}

      {mode === "description_only" && !generationStarted && (
        <div className="space-y-4">
          {taskType === "qa" && (
            <p className="text-xs text-muted-foreground rounded-md border border-border bg-secondary/30 p-3">
              {t("sdgNoSeed.qaNote")}
            </p>
          )}

          <div className="rounded-md border border-border bg-secondary/30 p-3">
            <p className="text-xs font-medium text-foreground">{t("sdgNoSeed.usingTaskPrompt")}</p>
            <p className="text-xs text-muted-foreground mt-1 line-clamp-3">{formData.taskPrompt}</p>
          </div>

          {taskType === "classification" && (
            <div>
              <Label className="text-xs">{t("sdgNoSeed.labelsLabel")}</Label>
              <Input
                className="mt-1"
                value={labelsText}
                onChange={(e) => setLabelsText(e.target.value)}
                placeholder="billing, technical, general"
              />
              <p className="text-[10px] text-muted-foreground mt-1">{t("sdgNoSeed.labelsHint")}</p>
            </div>
          )}

          {taskType === "tool_calling" && (
            <div className="space-y-2">
              <Label className="text-xs">{t("sdgNoSeed.toolsLabel")}</Label>
              <Textarea
                value={toolsText}
                onChange={(e) => setToolsText(e.target.value)}
                placeholder={toolsPlaceholder}
                className="min-h-36 font-mono text-xs"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] text-muted-foreground">{t("sdgNoSeed.toolsHint")}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={formatToolsJson}
                  disabled={!toolsText.trim()}
                >
                  {t("sdgNoSeed.formatJson")}
                </Button>
              </div>
              {toolsFormatError && (
                <p role="alert" className="text-xs text-destructive">
                  {toolsFormatError}
                </p>
              )}
            </div>
          )}

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
            <Label className="text-xs">
              {t("sdgNoSeed.baseNameLabel")} <span className="text-destructive">*</span>
            </Label>
            <Input className="mt-1" value={baseName} onChange={(e) => setBaseName(e.target.value)} required />
            <p className="text-[10px] text-muted-foreground mt-1">{t("sdgNoSeed.baseNameHint")}</p>
            {noSeedNamesPreview() && (
              <p className="text-[10px] text-muted-foreground mt-1">{noSeedNamesPreview()}</p>
            )}
          </div>

          {pipelineInfoBox}

          <Button
            type="button"
            onClick={handleGenerateNoSeed}
            disabled={!canGenerateNoSeed() || generateDataset.isPending}
            className="gap-2"
          >
            {generateDataset.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {t("sdgNoSeed.generateButton")}
          </Button>

          {generateDataset.isError && <ErrorDetail error={toErrorDetail(generateDataset.error)} />}
        </div>
      )}

      {mode === "existing" && !generationStarted && (
        <div className="space-y-4">
          {pickableDatasets.length === 0 ? (
            <p className="text-xs text-muted-foreground rounded-md border border-border bg-secondary/30 p-3">
              {t("dataUpload.existingEmpty")}
            </p>
          ) : (
            <RadioGroup
              value={pickedExisting ? formData.trainingDatasetId ?? undefined : undefined}
              onValueChange={handlePickExisting}
              className="gap-2"
            >
              {pickableDatasets.map((d) => (
                <label
                  key={d.id}
                  htmlFor={`existing-dataset-${d.id}`}
                  className="flex items-center gap-3 p-2.5 rounded-md border border-border cursor-pointer hover:bg-accent/50 transition-colors"
                >
                  <RadioGroupItem value={d.id} id={`existing-dataset-${d.id}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px] uppercase shrink-0">
                        {datasetRoleTag(d)}
                      </Badge>
                      <span className="text-sm font-medium text-foreground truncate">{d.name}</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      {t("dataUpload.existingRows").replace("{n}", d.num_samples.toLocaleString())} · {projectNameFor(d)}
                    </p>
                  </div>
                </label>
              ))}
            </RadioGroup>
          )}

          <div className="relative flex items-center gap-3">
            <Separator className="flex-1" />
            <span className="text-[10px] text-muted-foreground shrink-0">{t("dataUpload.orSeparator")}</span>
            <Separator className="flex-1" />
          </div>

          <div>
            <p className="text-sm font-semibold text-foreground">{t("dataUpload.uploadNewTitle")}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{t("dataUpload.uploadNewHint")}</p>
          </div>

          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
            onDrop={(e) => { e.preventDefault(); e.stopPropagation(); handleFiles(e.dataTransfer.files); }}
            className="border-2 border-dashed border-border rounded-lg p-8 text-center cursor-pointer hover:border-primary/50 hover:bg-accent/50 transition-colors"
          >
            <Upload className="h-6 w-6 text-muted-foreground mx-auto mb-2" />
            <p className="text-sm font-medium text-foreground">{t("dataUpload.uploadNewButton")}</p>
            <p className="text-xs text-muted-foreground mt-1">JSON/JSONL</p>
            <input
              ref={inputRef}
              type="file"
              accept=".json,.jsonl"
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

              <Button
                type="button"
                onClick={handleUploadExisting}
                disabled={!projectId || uploadDataset.isPending}
                className="gap-2"
              >
                {uploadDataset.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {t("dataUpload.uploadNewButton")}
              </Button>
            </div>
          )}

          {uploadDataset.isError && (
            <ErrorDetail
              error={{
                detail: toErrorDetail(uploadDataset.error).detail || t("dataUpload.uploadNewError"),
                code: toErrorDetail(uploadDataset.error).code,
              }}
            />
          )}
        </div>
      )}

      {generationStarted && (
        <div className="rounded-lg border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-foreground">Synthetic data generation</p>
            <Badge variant={status === "completed" ? "default" : status === "failed" ? "destructive" : "outline"}>
              {status ?? "pending"}
            </Badge>
          </div>
          {!isTerminal(status) && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {mode === "with_seed"
                ? "Generating rows from your seed dataset…"
                : t("sdgNoSeed.generatingFromDescription")}
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
          hard-coded classification snippet. Only relevant to the with_seed
          upload flow (it documents the seed file's expected shape). */}
      {mode === "with_seed" && example && (
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
