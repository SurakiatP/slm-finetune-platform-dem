import { Badge } from "@/components/ui/badge";
import { Tag, MapPin, HelpCircle, Zap, FileText, ArrowUpDown } from "lucide-react";
import type { ProjectFormData } from "@/pages/NewProject";
import type { TaskType } from "@/types";

const tasks: { type: TaskType; label: string; description: string; example: string; icon: React.ElementType }[] = [
  {
    type: "classification",
    label: "Classification",
    description: "Categorize text into predefined labels or classes",
    example: "Input: 'My internet is not working' → Output: 'Technical Support'",
    icon: Tag,
  },
  {
    type: "ner",
    label: "Named Entity Recognition",
    description: "Extract entities like names, locations, dates from text",
    example: "Input: 'John lives in Bangkok' → Output: [John: PERSON, Bangkok: LOCATION]",
    icon: MapPin,
  },
  {
    type: "qa",
    label: "Question Answering",
    description: "Answer questions based on given context or documents",
    example: "Context: '...' Question: 'What is the return policy?' → Answer: '30 days...'",
    icon: HelpCircle,
  },
  {
    type: "function-calling",
    label: "Function Calling",
    description: "Map natural language to API calls with correct parameters",
    example: "Input: 'Book a flight to Tokyo' → Call: book_flight(destination='Tokyo')",
    icon: Zap,
  },
  {
    type: "extraction",
    label: "Data Extraction",
    description: "Extract structured data from unstructured text",
    example: "Input: 'Invoice #123, $500, Jan 5' → {id: 123, amount: 500, date: '2026-01-05'}",
    icon: FileText,
  },
  {
    type: "ranking",
    label: "Ranking",
    description: "Score and rank items by relevance or preference",
    example: "Query: 'running shoes' → Rank: [Item A: 0.95, Item B: 0.82, Item C: 0.71]",
    icon: ArrowUpDown,
  },
];

export function TaskSelectionStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Select Task Type</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Choose the type of task that best matches your use case
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {tasks.map((task) => {
          const selected = formData.taskType === task.type;
          return (
            <button
              key={task.type}
              onClick={() => updateForm({ taskType: task.type })}
              className={`text-left p-4 rounded-lg border-2 transition-all ${
                selected
                  ? "border-primary bg-accent"
                  : "border-border hover:border-primary/40 bg-background"
              }`}
            >
              <div className="flex items-center gap-2.5 mb-2">
                <div className={`p-1.5 rounded-md ${selected ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground"}`}>
                  <task.icon className="h-4 w-4" />
                </div>
                <span className="font-semibold text-sm text-foreground">{task.label}</span>
                {selected && <Badge className="ml-auto text-[10px]">Selected</Badge>}
              </div>
              <p className="text-xs text-muted-foreground mb-2">{task.description}</p>
              <div className="bg-secondary/50 rounded-md px-2.5 py-1.5">
                <p className="text-[10px] font-mono text-muted-foreground">{task.example}</p>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
