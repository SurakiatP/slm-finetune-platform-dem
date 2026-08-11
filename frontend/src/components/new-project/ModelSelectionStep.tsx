import { Badge } from "@/components/ui/badge";
import type { ProjectFormData } from "@/pages/NewProject";
import type { BaseModel } from "@/types";

const models: {
  id: BaseModel;
  name: string;
  params: string;
  speed: string;
  quality: string;
  bestFor: string[];
  size: string;
}[] = [
  {
    id: "qwen2.5-1.5b",
    name: "Qwen 2.5",
    params: "1.5B",
    speed: "Very Fast",
    quality: "Good",
    bestFor: ["Classification", "Extraction"],
    size: "~1.2 GB",
  },
  {
    id: "qwen2.5-3b",
    name: "Qwen 2.5",
    params: "3B",
    speed: "Fast",
    quality: "Very Good",
    bestFor: ["Function Calling", "QA"],
    size: "~2.4 GB",
  },
  {
    id: "gemma-2-2b",
    name: "Gemma 2",
    params: "2B",
    speed: "Fast",
    quality: "Very Good",
    bestFor: ["NER", "Classification", "Multilingual"],
    size: "~1.8 GB",
  },
  {
    id: "phi-3-mini",
    name: "Phi-3 Mini",
    params: "3.8B",
    speed: "Moderate",
    quality: "Excellent",
    bestFor: ["QA", "Reasoning", "Code"],
    size: "~3.2 GB",
  },
  {
    id: "llama-3.2-1b",
    name: "Llama 3.2",
    params: "1B",
    speed: "Very Fast",
    quality: "Good",
    bestFor: ["Ranking", "Classification"],
    size: "~0.9 GB",
  },
  {
    id: "smollm2-1.7b",
    name: "SmolLM2",
    params: "1.7B",
    speed: "Very Fast",
    quality: "Good",
    bestFor: ["Extraction", "Simple Tasks"],
    size: "~1.3 GB",
  },
];

const speedColor: Record<string, string> = {
  "Very Fast": "text-success",
  Fast: "text-primary",
  Moderate: "text-warning",
};

export function ModelSelectionStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Choose Base Model</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Select the pre-trained model to fine-tune. Larger models are more capable but slower to train.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {models.map((model) => {
          const selected = formData.baseModel === model.id;
          return (
            <button
              key={model.id}
              onClick={() => updateForm({ baseModel: model.id })}
              className={`text-left p-4 rounded-lg border-2 transition-all ${
                selected
                  ? "border-primary bg-accent"
                  : "border-border hover:border-primary/40 bg-background"
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <div>
                  <span className="font-semibold text-sm text-foreground">{model.name}</span>
                  <span className="text-xs text-muted-foreground ml-1.5">{model.params}</span>
                </div>
                {selected && <Badge className="text-[10px]">Selected</Badge>}
              </div>

              <div className="grid grid-cols-3 gap-2 text-[11px] mb-2.5">
                <div>
                  <p className="text-muted-foreground">Speed</p>
                  <p className={`font-medium ${speedColor[model.speed] || "text-foreground"}`}>{model.speed}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Quality</p>
                  <p className="font-medium text-foreground">{model.quality}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Size</p>
                  <p className="font-medium text-foreground">{model.size}</p>
                </div>
              </div>

              <div className="flex flex-wrap gap-1">
                {model.bestFor.map((tag) => (
                  <Badge key={tag} variant="outline" className="text-[9px] px-1.5 py-0">
                    {tag}
                  </Badge>
                ))}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
