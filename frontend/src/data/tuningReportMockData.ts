// Hyperparameter Auto-Tuning report mock data
export interface TuningTrial {
  trial: number;
  learningRate: number;
  epochs: number;
  batchSize: number;
  warmupSteps: number;
  weightDecay: number;
  trainLoss: number;
  valLoss: number;
  accuracy: number;
  f1Score: number;
  durationMin: number;
  status: "completed" | "pruned" | "failed";
}

export interface TuningRecommendation {
  param: string;
  baseline: number | string;
  recommended: number | string;
  delta: string;
  reason: string;
  confidence: number;
}

export interface TuningReport {
  status: "completed";
  startedAt: string;
  completedAt: string;
  totalTrials: number;
  prunedTrials: number;
  bestTrial: number;
  searchSpace: { param: string; range: string }[];
  trials: TuningTrial[];
  recommendations: TuningRecommendation[];
  baseline: { accuracy: number; f1Score: number; valLoss: number };
  best: { accuracy: number; f1Score: number; valLoss: number };
  estimatedSavings: { credits: number; minutes: number };
}

export const mockTuningReport: TuningReport = {
  status: "completed",
  startedAt: "2026-04-28T09:14:00Z",
  completedAt: "2026-04-28T11:42:00Z",
  totalTrials: 18,
  prunedTrials: 7,
  bestTrial: 12,
  searchSpace: [
    { param: "Learning rate", range: "1e-5 → 5e-4 (log)" },
    { param: "Epochs", range: "3 → 10" },
    { param: "Batch size", range: "8, 16, 32" },
    { param: "Warmup steps", range: "0 → 500" },
    { param: "Weight decay", range: "0.0 → 0.1" },
  ],
  trials: [
    { trial: 1, learningRate: 5e-4, epochs: 3, batchSize: 32, warmupSteps: 0, weightDecay: 0.0, trainLoss: 0.812, valLoss: 0.94, accuracy: 71.2, f1Score: 68.4, durationMin: 4.8, status: "completed" },
    { trial: 2, learningRate: 1e-4, epochs: 5, batchSize: 16, warmupSteps: 100, weightDecay: 0.01, trainLoss: 0.485, valLoss: 0.512, accuracy: 84.6, f1Score: 82.1, durationMin: 6.2, status: "completed" },
    { trial: 3, learningRate: 3e-4, epochs: 4, batchSize: 16, warmupSteps: 200, weightDecay: 0.05, trainLoss: 0.421, valLoss: 0.498, accuracy: 85.9, f1Score: 83.7, durationMin: 5.9, status: "completed" },
    { trial: 4, learningRate: 5e-5, epochs: 8, batchSize: 8, warmupSteps: 300, weightDecay: 0.02, trainLoss: 0.612, valLoss: 0.687, accuracy: 78.4, f1Score: 75.9, durationMin: 9.1, status: "completed" },
    { trial: 5, learningRate: 4e-4, epochs: 6, batchSize: 32, warmupSteps: 0, weightDecay: 0.0, trainLoss: 1.24, valLoss: 1.41, accuracy: 0, f1Score: 0, durationMin: 1.2, status: "pruned" },
    { trial: 6, learningRate: 2e-4, epochs: 5, batchSize: 16, warmupSteps: 150, weightDecay: 0.01, trainLoss: 0.412, valLoss: 0.467, accuracy: 86.8, f1Score: 84.9, durationMin: 6.5, status: "completed" },
    { trial: 7, learningRate: 2e-4, epochs: 6, batchSize: 16, warmupSteps: 200, weightDecay: 0.02, trainLoss: 0.398, valLoss: 0.451, accuracy: 87.3, f1Score: 85.4, durationMin: 7.1, status: "completed" },
    { trial: 8, learningRate: 1.5e-4, epochs: 7, batchSize: 16, warmupSteps: 250, weightDecay: 0.03, trainLoss: 0.382, valLoss: 0.443, accuracy: 88.1, f1Score: 86.2, durationMin: 8.0, status: "completed" },
    { trial: 9, learningRate: 5e-4, epochs: 4, batchSize: 8, warmupSteps: 0, weightDecay: 0.0, trainLoss: 0.95, valLoss: 1.12, accuracy: 0, f1Score: 0, durationMin: 0.9, status: "pruned" },
    { trial: 10, learningRate: 1e-4, epochs: 8, batchSize: 16, warmupSteps: 300, weightDecay: 0.04, trainLoss: 0.371, valLoss: 0.439, accuracy: 88.4, f1Score: 86.7, durationMin: 8.7, status: "completed" },
    { trial: 11, learningRate: 8e-5, epochs: 8, batchSize: 16, warmupSteps: 350, weightDecay: 0.04, trainLoss: 0.368, valLoss: 0.434, accuracy: 88.7, f1Score: 87.0, durationMin: 8.9, status: "completed" },
    { trial: 12, learningRate: 8e-5, epochs: 8, batchSize: 16, warmupSteps: 300, weightDecay: 0.05, trainLoss: 0.356, valLoss: 0.421, accuracy: 89.4, f1Score: 87.8, durationMin: 8.6, status: "completed" },
    { trial: 13, learningRate: 7e-5, epochs: 9, batchSize: 16, warmupSteps: 400, weightDecay: 0.05, trainLoss: 0.354, valLoss: 0.428, accuracy: 89.1, f1Score: 87.5, durationMin: 9.4, status: "completed" },
    { trial: 14, learningRate: 6e-5, epochs: 10, batchSize: 16, warmupSteps: 500, weightDecay: 0.06, trainLoss: 0.351, valLoss: 0.431, accuracy: 88.9, f1Score: 87.3, durationMin: 10.2, status: "completed" },
    { trial: 15, learningRate: 1e-4, epochs: 6, batchSize: 32, warmupSteps: 200, weightDecay: 0.03, trainLoss: 0.405, valLoss: 0.469, accuracy: 86.4, f1Score: 84.6, durationMin: 5.8, status: "completed" },
    { trial: 16, learningRate: 1e-3, epochs: 5, batchSize: 16, warmupSteps: 0, weightDecay: 0.0, trainLoss: 2.81, valLoss: 3.12, accuracy: 0, f1Score: 0, durationMin: 0.5, status: "failed" },
    { trial: 17, learningRate: 9e-5, epochs: 8, batchSize: 16, warmupSteps: 300, weightDecay: 0.05, trainLoss: 0.362, valLoss: 0.425, accuracy: 89.0, f1Score: 87.4, durationMin: 8.8, status: "completed" },
    { trial: 18, learningRate: 8e-5, epochs: 8, batchSize: 16, warmupSteps: 280, weightDecay: 0.05, trainLoss: 0.359, valLoss: 0.423, accuracy: 89.2, f1Score: 87.6, durationMin: 8.7, status: "completed" },
  ],
  recommendations: [
    {
      param: "Learning rate",
      baseline: "2e-4",
      recommended: "8e-5",
      delta: "-60%",
      reason: "Lower LR with warmup gave smoother convergence and +2.6 accuracy across the top 5 trials.",
      confidence: 92,
    },
    {
      param: "Epochs",
      baseline: 5,
      recommended: 8,
      delta: "+3",
      reason: "Validation loss kept decreasing past epoch 5 in all top trials. No overfitting signal until epoch 9.",
      confidence: 88,
    },
    {
      param: "Batch size",
      baseline: 32,
      recommended: 16,
      delta: "-50%",
      reason: "Smaller batches produced sharper gradients on this dataset size; +1.4 F1 vs batch=32 setups.",
      confidence: 81,
    },
    {
      param: "Warmup steps",
      baseline: 0,
      recommended: 300,
      delta: "+300",
      reason: "Warmup eliminated 4 of 7 prunes. Critical for stability with the new LR.",
      confidence: 95,
    },
    {
      param: "Weight decay",
      baseline: 0.0,
      recommended: 0.05,
      delta: "+0.05",
      reason: "Modest regularization improved val loss without hurting train loss.",
      confidence: 76,
    },
  ],
  baseline: { accuracy: 84.6, f1Score: 82.1, valLoss: 0.512 },
  best: { accuracy: 89.4, f1Score: 87.8, valLoss: 0.421 },
  estimatedSavings: { credits: 38, minutes: 22 },
};
