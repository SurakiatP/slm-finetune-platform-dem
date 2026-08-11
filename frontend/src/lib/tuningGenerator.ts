// Deterministic per-project Auto-Tuning data generator.
// Replaces shared mock data with results derived from each project's
// id + configuration so different projects show different (but stable) runs.
//
// When real backend is wired up, swap callers from these helpers to API calls.

import type { TuningReport, TuningTrial, TuningRecommendation } from "@/data/tuningReportMockData";
import type { Project } from "@/types";

export interface TuningRunSummary {
  runId: string;
  projectId: string;
  startedAt: string;
  completedAt: string;
  totalTrials: number;
  bestTrial: number;
  baselineAccuracy: number;
  bestAccuracy: number;
  baselineF1: number;
  bestF1: number;
  applied: boolean;
  label: string;
}

export interface TuningRun extends TuningRunSummary {
  report: TuningReport;
}

// --- deterministic PRNG (mulberry32) ---
function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(rand: () => number, arr: T[]): T {
  return arr[Math.floor(rand() * arr.length)];
}

function round(n: number, d = 1): number {
  const f = Math.pow(10, d);
  return Math.round(n * f) / f;
}

// Build a single TuningReport seeded by projectId + runIndex.
function buildReport(project: Project, runIndex: number, daysAgo: number): TuningReport {
  const seed = hashStr(`${project.id}::${runIndex}`);
  const rand = mulberry32(seed);

  // Baseline taken from project's current config
  const baselineLR = project.learningRate;
  const baselineEpochs = project.epochs;
  const baselineBatch = 32;

  // Baseline metrics — anchored on project's "true" capability with run-level noise
  const baseAcc = 78 + rand() * 8;
  const baseF1 = baseAcc - (1.5 + rand() * 1.5);
  const baseValLoss = 0.45 + rand() * 0.15;

  // Generate trials
  const totalTrials = 14 + Math.floor(rand() * 8); // 14-21
  const trials: TuningTrial[] = [];
  let bestAcc = 0;
  let bestTrial = 1;

  for (let i = 1; i <= totalTrials; i++) {
    const lrChoices = [5e-5, 7e-5, 8e-5, 1e-4, 1.5e-4, 2e-4, 3e-4, 4e-4, 5e-4, 1e-3];
    const lr = pick(rand, lrChoices);
    const epochs = 3 + Math.floor(rand() * 8); // 3-10
    const batchSize = pick(rand, [8, 16, 32]);
    const warmup = pick(rand, [0, 100, 200, 300, 400, 500]);
    const wd = round(rand() * 0.1, 2);

    // Quality scoring — favors moderate LR, longer epochs, batch=16, some warmup
    const lrPenalty = Math.abs(Math.log10(lr) - Math.log10(1e-4)) * 4;
    const epochsBonus = Math.min((epochs - 3) * 0.6, 4);
    const batchBonus = batchSize === 16 ? 1.8 : batchSize === 8 ? 0.4 : 0;
    const warmupBonus = warmup >= 200 && warmup <= 400 ? 1.2 : 0;
    const noise = (rand() - 0.5) * 2.5;

    const acc = baseAcc + epochsBonus + batchBonus + warmupBonus - lrPenalty + noise;
    const f1 = acc - (1.2 + rand() * 1.4);
    const valLoss = baseValLoss - (acc - baseAcc) * 0.018 + (rand() - 0.5) * 0.03;
    const trainLoss = valLoss - 0.04 - rand() * 0.06;
    const duration = round(3 + epochs * 0.7 + (batchSize === 8 ? 1.5 : 0), 1);

    // Status: bad LR or LR too high → prune/fail
    let status: TuningTrial["status"] = "completed";
    if (lr >= 5e-4 && rand() > 0.5) status = "pruned";
    if (lr >= 1e-3) status = rand() > 0.5 ? "failed" : "pruned";

    const accFinal = status === "completed" ? Math.max(60, Math.min(94, round(acc, 1))) : 0;
    const f1Final = status === "completed" ? Math.max(58, Math.min(92, round(f1, 1))) : 0;

    trials.push({
      trial: i,
      learningRate: lr,
      epochs,
      batchSize,
      warmupSteps: warmup,
      weightDecay: wd,
      trainLoss: round(Math.max(0.2, trainLoss), 3),
      valLoss: round(Math.max(0.25, valLoss), 3),
      accuracy: accFinal,
      f1Score: f1Final,
      durationMin: duration,
      status,
    });

    if (status === "completed" && accFinal > bestAcc) {
      bestAcc = accFinal;
      bestTrial = i;
    }
  }

  const prunedTrials = trials.filter((t) => t.status !== "completed").length;
  const best = trials.find((t) => t.trial === bestTrial)!;

  // Build recommendations from the best trial
  const recommendations: TuningRecommendation[] = [
    {
      param: "Learning rate",
      baseline: baselineLR.toExponential(0),
      recommended: best.learningRate.toExponential(0),
      delta: `${best.learningRate > baselineLR ? "+" : ""}${round(((best.learningRate - baselineLR) / baselineLR) * 100, 0)}%`,
      reason: best.learningRate < baselineLR
        ? "Lower LR with warmup gave smoother convergence and stronger top trials."
        : "Slightly higher LR sped up convergence without instability.",
      confidence: 80 + Math.floor(rand() * 15),
    },
    {
      param: "Epochs",
      baseline: baselineEpochs,
      recommended: best.epochs,
      delta: `${best.epochs >= baselineEpochs ? "+" : ""}${best.epochs - baselineEpochs}`,
      reason: best.epochs > baselineEpochs
        ? "Validation loss kept decreasing past the baseline epoch count with no overfitting."
        : "Best trial converged earlier — fewer epochs cuts cost without quality loss.",
      confidence: 78 + Math.floor(rand() * 14),
    },
    {
      param: "Batch size",
      baseline: baselineBatch,
      recommended: best.batchSize,
      delta: `${best.batchSize === baselineBatch ? "no change" : best.batchSize < baselineBatch ? "-" : "+"}${best.batchSize !== baselineBatch ? Math.abs(best.batchSize - baselineBatch) : ""}`.trim(),
      reason: best.batchSize === 16
        ? "Batch=16 produced sharper gradients and the best F1 across top trials."
        : best.batchSize === 8
        ? "Smaller batches gave more updates per epoch on this dataset size."
        : "Larger batches were faster without hurting accuracy.",
      confidence: 72 + Math.floor(rand() * 16),
    },
    {
      param: "Warmup steps",
      baseline: 0,
      recommended: best.warmupSteps,
      delta: `+${best.warmupSteps}`,
      reason: best.warmupSteps > 0
        ? "Warmup eliminated several early prunes and stabilized the new LR."
        : "No warmup needed for this LR range.",
      confidence: 85 + Math.floor(rand() * 10),
    },
    {
      param: "Weight decay",
      baseline: 0.0,
      recommended: best.weightDecay,
      delta: `+${best.weightDecay}`,
      reason: "Modest regularization improved val loss without hurting train loss.",
      confidence: 70 + Math.floor(rand() * 14),
    },
  ];

  const startedAt = new Date(Date.now() - daysAgo * 86400000 - 2 * 3600000).toISOString();
  const completedAt = new Date(Date.now() - daysAgo * 86400000).toISOString();

  return {
    status: "completed",
    startedAt,
    completedAt,
    totalTrials,
    prunedTrials,
    bestTrial,
    searchSpace: [
      { param: "Learning rate", range: "5e-5 → 1e-3 (log)" },
      { param: "Epochs", range: "3 → 10" },
      { param: "Batch size", range: "8, 16, 32" },
      { param: "Warmup steps", range: "0 → 500" },
      { param: "Weight decay", range: "0.0 → 0.1" },
    ],
    trials,
    recommendations,
    baseline: {
      accuracy: round(baseAcc, 1),
      f1Score: round(baseF1, 1),
      valLoss: round(baseValLoss, 3),
    },
    best: {
      accuracy: best.accuracy,
      f1Score: best.f1Score,
      valLoss: best.valLoss,
    },
    estimatedSavings: {
      credits: 20 + Math.floor(rand() * 40),
      minutes: 12 + Math.floor(rand() * 30),
    },
  };
}

const STORAGE_KEY = "slm.appliedTuningRun"; // map<projectId, runId>

function readApplied(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export function getAppliedRunId(projectId: string): string | null {
  return readApplied()[projectId] ?? null;
}

export function setAppliedRun(projectId: string, runId: string): void {
  const map = readApplied();
  map[projectId] = runId;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}

// Public: get all tuning runs for a project (latest first).
export function getTuningRunsForProject(project: Project): TuningRun[] {
  // Number of past runs derived deterministically from project — gives history
  const runCount = 1 + (hashStr(project.id) % 4); // 1-4 runs
  const applied = getAppliedRunId(project.id);
  const runs: TuningRun[] = [];

  for (let i = 0; i < runCount; i++) {
    const daysAgo = i * 7 + (hashStr(`${project.id}-day-${i}`) % 5);
    const report = buildReport(project, i, daysAgo);
    const runId = `${project.id}-run-${i}`;
    runs.push({
      runId,
      projectId: project.id,
      startedAt: report.startedAt,
      completedAt: report.completedAt,
      totalTrials: report.totalTrials,
      bestTrial: report.bestTrial,
      baselineAccuracy: report.baseline.accuracy,
      bestAccuracy: report.best.accuracy,
      baselineF1: report.baseline.f1Score,
      bestF1: report.best.f1Score,
      applied: applied ? applied === runId : i === 0,
      label: i === 0 ? "Latest" : `Run #${runCount - i}`,
      report,
    });
  }

  return runs.sort((a, b) => +new Date(b.completedAt) - +new Date(a.completedAt));
}

export function getLatestTuningRun(project: Project): TuningRun | null {
  const runs = getTuningRunsForProject(project);
  return runs[0] ?? null;
}

export function getTuningRun(project: Project, runId: string): TuningRun | null {
  return getTuningRunsForProject(project).find((r) => r.runId === runId) ?? null;
}
