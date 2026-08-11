import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tag, MapPin, HelpCircle, Zap, FileText, ArrowUpDown } from "lucide-react";
import type { TaskType, BaseModel } from "@/types";

const iconMap: Record<TaskType, React.ElementType> = {
  classification: Tag,
  ner: MapPin,
  qa: HelpCircle,
  "function-calling": Zap,
  extraction: FileText,
  ranking: ArrowUpDown,
};

const templates: {
  name: string;
  prompt: string;
  taskType: TaskType;
  baseModel: BaseModel;
  description: string;
}[] = [
  {
    name: "Support Ticket Classifier",
    prompt: "Classify incoming customer support tickets into categories: billing, technical, account, and general inquiry. The model should handle both English and Thai inputs.",
    taskType: "classification",
    baseModel: "qwen2.5-1.5b",
    description: "Multi-language support ticket routing",
  },
  {
    name: "Thai NER Pipeline",
    prompt: "Extract named entities (PERSON, ORGANIZATION, LOCATION, DATE, MONEY) from Thai news articles and social media posts.",
    taskType: "ner",
    baseModel: "gemma-2-2b",
    description: "Thai language entity extraction",
  },
  {
    name: "Product FAQ Bot",
    prompt: "Answer customer questions about products using the provided product documentation and FAQ database. Responses should be concise and helpful.",
    taskType: "qa",
    baseModel: "phi-3-mini",
    description: "Documentation-based Q&A",
  },
  {
    name: "API Router Agent",
    prompt: "Parse natural language user requests and map them to the correct API endpoint with extracted parameters. Support CRUD operations for users, orders, and products.",
    taskType: "function-calling",
    baseModel: "qwen2.5-3b",
    description: "Natural language to API call mapping",
  },
  {
    name: "Invoice Parser",
    prompt: "Extract structured fields (vendor name, invoice number, date, line items with quantities and prices, total amount, tax) from invoice text.",
    taskType: "extraction",
    baseModel: "smollm2-1.7b",
    description: "Structured data from invoices",
  },
  {
    name: "Search Re-Ranker",
    prompt: "Re-rank e-commerce search results based on semantic relevance to the user query, considering product title, description, and category.",
    taskType: "ranking",
    baseModel: "llama-3.2-1b",
    description: "E-commerce search relevance",
  },
];

export function TemplateLibrary({
  onSelect,
}: {
  onSelect: (t: { name: string; prompt: string; taskType: TaskType; baseModel: BaseModel }) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm font-semibold text-foreground">Template Library</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {templates.map((t) => {
          const Icon = iconMap[t.taskType];
          return (
            <Card
              key={t.name}
              className="cursor-pointer hover:shadow-md hover:border-primary/40 transition-all"
              onClick={() => onSelect(t)}
            >
              <CardContent className="p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <div className="p-1.5 rounded-md bg-accent text-accent-foreground">
                    <Icon className="h-3.5 w-3.5" />
                  </div>
                  <span className="text-sm font-semibold text-foreground">{t.name}</span>
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2">{t.description}</p>
                <div className="flex gap-1.5">
                  <Badge variant="outline" className="text-[9px]">{t.taskType}</Badge>
                  <Badge variant="outline" className="text-[9px]">{t.baseModel}</Badge>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
