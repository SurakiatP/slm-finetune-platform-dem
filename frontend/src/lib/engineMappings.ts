import type { TaskType, BaseModel } from "@/types";
import type { EngineTaskType, ManualTrainingConfig } from "@/lib/engineApi";

// Maps frontend TaskType → Engine task_type (null = not supported by engine)
export const TASK_TYPE_TO_ENGINE: Record<TaskType, EngineTaskType | null> = {
  classification: "classification",
  qa: "qa",
  "function-calling": "tool_calling",
  ner: null,
  extraction: null,
  ranking: null,
};

// Maps frontend BaseModel → Engine Hugging Face model ID
export const BASE_MODEL_TO_ENGINE: Record<BaseModel, string> = {
  "qwen2.5-1.5b": "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
  "qwen2.5-3b": "unsloth/Qwen2.5-3B-Instruct-bnb-4bit",
  "gemma-2-2b": "unsloth/gemma-2-2b-it-bnb-4bit",
  "phi-3-mini": "unsloth/Phi-3-mini-4k-instruct-bnb-4bit",
  "llama-3.2-1b": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "smollm2-1.7b": "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit",
};

export function buildManualConfig(epochs: number, learningRate: number): ManualTrainingConfig {
  return {
    num_train_epochs: epochs,
    per_device_train_batch_size: 2,
    gradient_accumulation_steps: 8,
    learning_rate: learningRate,
    max_seq_length: 2048,
    optim: "adamw_8bit",
    packing: false,
    lora: {
      r: 16,
      alpha: 32,
      dropout: 0.05,
      target_modules: ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    },
  };
}
