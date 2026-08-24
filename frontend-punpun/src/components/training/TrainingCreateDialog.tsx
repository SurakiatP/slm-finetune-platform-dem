import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/client";
import type {
  Dataset,
  HPOSearchSpace,
  ManualTrainingConfig,
  TrainingRequest,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBaseModels, useDatasets, useProjects, useStartTraining } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

interface TrainingCreateDialogProps {
  projectId: string;
  datasetId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onStarted?: (trainingId: string) => void;
}

// Matches api/schemas/training.py TRAINING_NAME_PATTERN exactly — the name
// doubles as the MLflow run name and the derived Ollama model tag.
const NAME_PATTERN = /^[a-z0-9][a-z0-9._-]{0,62}$/;

const PLATFORM_DEFAULT = "__platform_default__";

// --- Manual hyperparameters — defaults mirror ManualTrainingConfig ----------

interface ManualFormState {
  learning_rate: string;
  num_train_epochs: string;
  per_device_train_batch_size: string;
  gradient_accumulation_steps: string;
  warmup_ratio: string;
  weight_decay: string;
  lr_scheduler_type: "linear" | "cosine" | "constant";
  max_seq_length: string;
  seed: string;
  lora_r: string;
  lora_alpha: string;
  lora_dropout: string;
}

const manualDefaults: ManualFormState = {
  learning_rate: "0.0002",
  num_train_epochs: "3",
  per_device_train_batch_size: "2",
  gradient_accumulation_steps: "8",
  warmup_ratio: "0.03",
  weight_decay: "0.01",
  lr_scheduler_type: "cosine",
  max_seq_length: "2048",
  seed: "42",
  lora_r: "16",
  lora_alpha: "32",
  lora_dropout: "0.05",
};

function buildManualConfig(form: ManualFormState): ManualTrainingConfig {
  return {
    learning_rate: Number(form.learning_rate),
    num_train_epochs: Number(form.num_train_epochs),
    per_device_train_batch_size: Number(form.per_device_train_batch_size),
    gradient_accumulation_steps: Number(form.gradient_accumulation_steps),
    warmup_ratio: Number(form.warmup_ratio),
    weight_decay: Number(form.weight_decay),
    lr_scheduler_type: form.lr_scheduler_type,
    max_seq_length: Number(form.max_seq_length),
    seed: Number(form.seed),
    lora: {
      r: Number(form.lora_r),
      alpha: Number(form.lora_alpha),
      dropout: Number(form.lora_dropout),
    },
  };
}

// --- HPO search-space builder ------------------------------------------------

type SpaceKey = keyof HPOSearchSpace;

interface SpaceRowDef {
  key: SpaceKey;
  label: string;
  kind: "float" | "int" | "categorical";
  defaultEnabled: boolean;
  defaults: { low?: string; high?: string; log?: boolean; choices?: string };
}

// Ranges stay within the backend bounds in api/schemas/training.py. Only
// learning_rate (the highest-impact knob per QLoRA guidance) is enabled by
// default; users can toggle the rest on.
const spaceRows: SpaceRowDef[] = [
  { key: "learning_rate", label: "learning_rate", kind: "float", defaultEnabled: true, defaults: { low: "0.00001", high: "0.001", log: true } },
  { key: "num_train_epochs", label: "num_train_epochs", kind: "int", defaultEnabled: false, defaults: { low: "2", high: "5" } },
  { key: "per_device_train_batch_size", label: "batch_size", kind: "categorical", defaultEnabled: false, defaults: { choices: "1, 2, 4" } },
  { key: "gradient_accumulation_steps", label: "grad_accum", kind: "categorical", defaultEnabled: false, defaults: { choices: "2, 4, 8" } },
  { key: "warmup_ratio", label: "warmup_ratio", kind: "float", defaultEnabled: false, defaults: { low: "0", high: "0.1" } },
  { key: "weight_decay", label: "weight_decay", kind: "float", defaultEnabled: false, defaults: { low: "0", high: "0.1" } },
  { key: "lr_scheduler_type", label: "lr_scheduler", kind: "categorical", defaultEnabled: false, defaults: { choices: "linear, cosine" } },
  { key: "lora_r", label: "lora_r", kind: "categorical", defaultEnabled: false, defaults: { choices: "8, 16, 32" } },
  { key: "lora_alpha", label: "lora_alpha", kind: "categorical", defaultEnabled: false, defaults: { choices: "16, 32, 64" } },
  { key: "lora_dropout", label: "lora_dropout", kind: "float", defaultEnabled: false, defaults: { low: "0", high: "0.2" } },
];

interface SpaceRowState {
  enabled: boolean;
  low: string;
  high: string;
  log: boolean;
  choices: string;
}

function initialSpaceState(): Record<SpaceKey, SpaceRowState> {
  return Object.fromEntries(
    spaceRows.map((row) => [
      row.key,
      {
        enabled: row.defaultEnabled,
        low: row.defaults.low ?? "",
        high: row.defaults.high ?? "",
        log: row.defaults.log ?? false,
        choices: row.defaults.choices ?? "",
      },
    ]),
  ) as Record<SpaceKey, SpaceRowState>;
}

function parseChoices(text: string): (string | number)[] {
  return text
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
    .map((c) => (c !== "" && !Number.isNaN(Number(c)) ? Number(c) : c));
}

function buildSearchSpace(state: Record<SpaceKey, SpaceRowState>): { space: HPOSearchSpace; error?: string } {
  const space: HPOSearchSpace = {};
  for (const row of spaceRows) {
    const s = state[row.key];
    if (!s.enabled) continue;
    if (row.kind === "categorical") {
      const choices = parseChoices(s.choices);
      if (choices.length === 0) return { space, error: `${row.label}: provide at least one choice` };
      space[row.key] = { type: "categorical", choices } as never;
    } else {
      const low = Number(s.low);
      const high = Number(s.high);
      if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) {
        return { space, error: `${row.label}: high must be greater than low` };
      }
      if (s.log && low <= 0) return { space, error: `${row.label}: log scale requires low > 0` };
      space[row.key] =
        row.kind === "float"
          ? ({ type: "float", low, high, log: s.log } as never)
          : ({ type: "int", low, high, log: s.log } as never);
    }
  }
  if (Object.keys(space).length === 0) return { space, error: "Enable at least one parameter to search" };
  return { space };
}

// --- Dialog -------------------------------------------------------------------

export default function TrainingCreateDialog({
  projectId,
  datasetId,
  open,
  onOpenChange,
  onStarted,
}: TrainingCreateDialogProps) {
  const { t } = useLanguage();

  const [selectedDatasetId, setSelectedDatasetId] = useState(datasetId);
  const [mode, setMode] = useState<"manual" | "hpo">("manual");
  const [trainingName, setTrainingName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [baseModel, setBaseModel] = useState(PLATFORM_DEFAULT);
  const [manual, setManual] = useState<ManualFormState>(manualDefaults);
  const [spaceState, setSpaceState] = useState(() => initialSpaceState());
  const [nTrials, setNTrials] = useState("6");
  const [objectiveMetric, setObjectiveMetric] = useState("eval_loss");
  const [direction, setDirection] = useState<"minimize" | "maximize">("minimize");
  const [sampler, setSampler] = useState<"tpe" | "random">("tpe");
  const [pruner, setPruner] = useState<"median" | "none">("median");
  const [formError, setFormError] = useState<string | null>(null);

  const { data: baseModels, isLoading: baseModelsLoading } = useBaseModels();
  const { data: datasetsPage } = useDatasets(undefined, { limit: 200 });
  const { data: projectsPage } = useProjects({ limit: 200 });
  const startTraining = useStartTraining();

  // Reopening the dialog against a different dataset (e.g. a different
  // pipeline card) must re-seed the picker instead of sticking on whatever
  // was last selected.
  useEffect(() => {
    if (open) setSelectedDatasetId(datasetId);
  }, [open, datasetId]);

  const eligibleDatasets: Dataset[] = useMemo(
    () =>
      (datasetsPage?.items ?? []).filter(
        (d) => d.status === "completed" && d.num_samples > 0 && !!d.storage_uri && !d.parent_dataset_id,
      ),
    [datasetsPage],
  );

  const projectNameById = useMemo(
    () => new Map((projectsPage?.items ?? []).map((p) => [p.id, p.name])),
    [projectsPage],
  );

  const reset = () => {
    setSelectedDatasetId(datasetId);
    setMode("manual");
    setTrainingName("");
    setNameError(null);
    setBaseModel(PLATFORM_DEFAULT);
    setManual(manualDefaults);
    setSpaceState(initialSpaceState());
    setNTrials("6");
    setObjectiveMetric("eval_loss");
    setDirection("minimize");
    setSampler("tpe");
    setPruner("median");
    setFormError(null);
    startTraining.reset();
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const submit = () => {
    setFormError(null);
    setNameError(null);

    const name = trainingName.trim();
    if (!name) {
      setNameError(t("trainCreate.nameRequired"));
      return;
    }
    if (!NAME_PATTERN.test(name)) {
      setNameError(t("trainCreate.namePatternError"));
      return;
    }

    const base = {
      project_id: projectId,
      dataset_id: selectedDatasetId,
      base_model: baseModel === PLATFORM_DEFAULT ? null : baseModel,
      training_name: name,
      auto_export: true,
      auto_evaluate: true,
    };

    let body: TrainingRequest;
    if (mode === "manual") {
      body = { ...base, mode: "manual", manual_config: buildManualConfig(manual) };
    } else {
      const { space, error } = buildSearchSpace(spaceState);
      if (error) {
        setFormError(error);
        return;
      }
      const trials = Number(nTrials);
      if (!Number.isFinite(trials) || trials < 2 || trials > 20) {
        setFormError(t("trainCreate.trialsRangeError"));
        return;
      }
      body = {
        ...base,
        mode: "hpo",
        hpo_config: {
          n_trials: trials,
          objective_metric: objectiveMetric.trim() || "eval_loss",
          direction,
          sampler,
          pruner,
          search_space: space,
          fixed_config: buildManualConfig(manual),
        },
      };
    }

    startTraining.mutate(body, {
      onSuccess: (res) => {
        onStarted?.(res.training_id);
        handleOpenChange(false);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 409) {
          setNameError(err.message);
          return;
        }
        setFormError(err instanceof Error ? err.message : String(err));
      },
    });
  };

  const numField = (label: string, key: keyof ManualFormState, hint?: string, step?: number) => (
    <div key={key} className="space-y-1.5">
      <Label htmlFor={`manual-${key}`}>{label}</Label>
      <Input
        id={`manual-${key}`}
        type="number"
        step={step ?? "any"}
        value={manual[key]}
        onChange={(e) => setManual((m) => ({ ...m, [key]: e.target.value }))}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );

  const apiError = startTraining.error instanceof ApiError ? startTraining.error : null;
  const genericError = apiError && apiError.status !== 409 ? apiError : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("trainCreate.title")}</DialogTitle>
          <DialogDescription>{t("trainCreate.description")}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="training-name">
                {t("trainCreate.nameLabel")} <span className="text-destructive">*</span>
              </Label>
              <Input
                id="training-name"
                value={trainingName}
                placeholder={t("trainCreate.namePlaceholder")}
                onChange={(e) => {
                  setTrainingName(e.target.value);
                  if (nameError) setNameError(null);
                }}
                aria-invalid={!!nameError}
                required
              />
              {nameError ? (
                <p role="alert" className="text-xs text-destructive">
                  {nameError}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">{t("trainCreate.nameHint")}</p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="base-model">{t("trainCreate.baseModelLabel")}</Label>
              <Select value={baseModel} onValueChange={setBaseModel} disabled={baseModelsLoading}>
                <SelectTrigger id="base-model">
                  <SelectValue placeholder={baseModelsLoading ? t("common.loading") : undefined} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={PLATFORM_DEFAULT}>{t("trainCreate.baseModelDefault")}</SelectItem>
                  {(baseModels ?? []).map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.display_name} ({m.params_billions}B)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">{t("trainCreate.baseModelHint")}</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="training-dataset">{t("trainCreate.datasetLabel")}</Label>
            <Select value={selectedDatasetId} onValueChange={setSelectedDatasetId}>
              <SelectTrigger id="training-dataset">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {eligibleDatasets.length === 0 ? (
                  <div className="px-2 py-1.5 text-xs text-muted-foreground">{t("trainCreate.datasetEmpty")}</div>
                ) : (
                  eligibleDatasets.map((d) => {
                    const projectLabel = d.project_id ? (projectNameById.get(d.project_id) ?? d.project_id) : "orphaned";
                    return (
                      <SelectItem key={d.id} value={d.id}>
                        {d.name} · {d.num_samples} rows · {projectLabel}
                      </SelectItem>
                    );
                  })
                )}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{t("trainCreate.datasetHint")}</p>
          </div>

          <div className="space-y-1.5">
            <Label>{t("trainCreate.modeLabel")}</Label>
            <Tabs value={mode} onValueChange={(v) => setMode(v as "manual" | "hpo")}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="manual">{t("trainCreate.modeManual")}</TabsTrigger>
                <TabsTrigger value="hpo">{t("trainCreate.modeHpo")}</TabsTrigger>
              </TabsList>
            </Tabs>
            <p className="text-xs text-muted-foreground">
              {mode === "manual" ? t("trainCreate.modeManualDesc") : t("trainCreate.modeHpoDesc")}
            </p>
          </div>

          {mode === "hpo" && (
            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("trainCreate.hpoSearchSpaceTitle")}
              </legend>
              <div className="grid gap-4 sm:grid-cols-5">
                <div className="space-y-1.5">
                  <Label htmlFor="hpo-trials">{t("trainCreate.hpoTrials")}</Label>
                  <Input
                    id="hpo-trials"
                    type="number"
                    min={2}
                    max={20}
                    value={nTrials}
                    onChange={(e) => setNTrials(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="hpo-objective">{t("trainCreate.hpoObjective")}</Label>
                  <Input
                    id="hpo-objective"
                    value={objectiveMetric}
                    onChange={(e) => setObjectiveMetric(e.target.value)}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="hpo-direction">{t("trainCreate.hpoDirection")}</Label>
                  <Select value={direction} onValueChange={(v) => setDirection(v as typeof direction)}>
                    <SelectTrigger id="hpo-direction">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="minimize">minimize</SelectItem>
                      <SelectItem value="maximize">maximize</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="hpo-sampler">{t("trainCreate.hpoSampler")}</Label>
                  <Select value={sampler} onValueChange={(v) => setSampler(v as typeof sampler)}>
                    <SelectTrigger id="hpo-sampler">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="tpe">tpe</SelectItem>
                      <SelectItem value="random">random</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="hpo-pruner">{t("trainCreate.hpoPruner")}</Label>
                  <Select value={pruner} onValueChange={(v) => setPruner(v as typeof pruner)}>
                    <SelectTrigger id="hpo-pruner">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="median">median</SelectItem>
                      <SelectItem value="none">none</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-2">
                {spaceRows.map((row) => {
                  const s = spaceState[row.key];
                  return (
                    <div
                      key={row.key}
                      className={`flex flex-wrap items-center gap-3 rounded-md border p-2.5 transition-colors ${
                        s.enabled ? "border-primary/30 bg-accent/40" : "border-border/60 bg-background"
                      }`}
                    >
                      <label className="flex w-40 cursor-pointer items-center gap-2 font-mono text-xs">
                        <input
                          type="checkbox"
                          checked={s.enabled}
                          onChange={(e) =>
                            setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, enabled: e.target.checked } }))
                          }
                          className="h-3.5 w-3.5"
                        />
                        {row.label}
                      </label>
                      {s.enabled &&
                        (row.kind === "categorical" ? (
                          <input
                            aria-label={`${row.label} choices`}
                            value={s.choices}
                            onChange={(e) =>
                              setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, choices: e.target.value } }))
                            }
                            placeholder={t("trainCreate.choicesPlaceholder")}
                            className="h-8 flex-1 rounded border border-input bg-background px-2 font-mono text-xs"
                          />
                        ) : (
                          <span className="flex flex-1 items-center gap-2">
                            <input
                              aria-label={`${row.label} low`}
                              type="number"
                              step="any"
                              value={s.low}
                              onChange={(e) =>
                                setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, low: e.target.value } }))
                              }
                              className="h-8 w-24 rounded border border-input bg-background px-2 font-mono text-xs"
                            />
                            <span className="text-xs text-muted-foreground">{t("trainCreate.toLabel")}</span>
                            <input
                              aria-label={`${row.label} high`}
                              type="number"
                              step="any"
                              value={s.high}
                              onChange={(e) =>
                                setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, high: e.target.value } }))
                              }
                              className="h-8 w-24 rounded border border-input bg-background px-2 font-mono text-xs"
                            />
                            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                              <input
                                type="checkbox"
                                checked={s.log}
                                onChange={(e) =>
                                  setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, log: e.target.checked } }))
                                }
                                className="h-3 w-3"
                              />
                              {t("trainCreate.logLabel")}
                            </label>
                          </span>
                        ))}
                    </div>
                  );
                })}
              </div>
            </fieldset>
          )}

          <fieldset className="rounded-lg border border-border p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {mode === "manual" ? t("trainCreate.hyperparamsTitle") : t("trainCreate.hpoFixedTitle")}
            </legend>
            <div className="grid gap-4 sm:grid-cols-3">
              {numField(t("trainCreate.fieldLearningRate"), "learning_rate")}
              {numField(t("trainCreate.fieldEpochs"), "num_train_epochs", "1–20", 1)}
              {numField(t("trainCreate.fieldBatchSize"), "per_device_train_batch_size", undefined, 1)}
              {numField(t("trainCreate.fieldGradAccum"), "gradient_accumulation_steps", undefined, 1)}
              {numField(t("trainCreate.fieldWarmup"), "warmup_ratio")}
              {numField(t("trainCreate.fieldWeightDecay"), "weight_decay")}
              <div className="space-y-1.5">
                <Label htmlFor="lr-scheduler">{t("trainCreate.fieldScheduler")}</Label>
                <Select
                  value={manual.lr_scheduler_type}
                  onValueChange={(v) =>
                    setManual((m) => ({ ...m, lr_scheduler_type: v as ManualFormState["lr_scheduler_type"] }))
                  }
                >
                  <SelectTrigger id="lr-scheduler">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="linear">linear</SelectItem>
                    <SelectItem value="cosine">cosine</SelectItem>
                    <SelectItem value="constant">constant</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {numField(t("trainCreate.fieldMaxSeq"), "max_seq_length", "128–8192", 1)}
              {numField(t("trainCreate.fieldSeed"), "seed", undefined, 1)}
              {numField(t("trainCreate.fieldLoraR"), "lora_r", undefined, 1)}
              {numField(t("trainCreate.fieldLoraAlpha"), "lora_alpha", undefined, 1)}
              {numField(t("trainCreate.fieldLoraDropout"), "lora_dropout", "0–0.5")}
            </div>
          </fieldset>

          {formError && (
            <p role="alert" className="text-xs text-destructive">
              {formError}
            </p>
          )}
          {genericError && <ErrorDetail error={{ detail: genericError.message, code: genericError.code }} />}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={startTraining.isPending}>
              {startTraining.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("trainCreate.submitCta")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
