import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageTransition, FadeIn, StaggerContainer, MotionCard } from "@/components/motion";
import {
  Search,
  Star,
  GitFork,
  Sparkles,
  Rocket,
  Eye,
  Clock,
  Coins,
  Database,
  Zap,
  Loader2,
  Settings as SettingsIcon,
} from "lucide-react";
import { projectTemplates, templateCategories, type ProjectTemplate } from "@/data/templatesMockData";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { createProject } from "@/lib/projectsApi";

type SortKey = "popular" | "rating" | "newest" | "forks";

// Heuristic estimates so users see practical impact before deciding.
function estimateTrainingMinutes(tpl: ProjectTemplate): number {
  // Roughly: epochs * dataset/100 minutes, scaled by model size factor.
  const sizeFactor: Record<string, number> = {
    "smollm2-1.7b": 1,
    "qwen2.5-1.5b": 1,
    "llama-3.2-1b": 0.9,
    "phi-3-mini": 1.1,
    "gemma-2-2b": 1.4,
    "qwen2.5-3b": 1.6,
  };
  const factor = sizeFactor[tpl.baseModel] ?? 1.2;
  return Math.max(3, Math.round((tpl.epochs * tpl.datasetSize) / 600 * factor));
}

function estimateCredits(tpl: ProjectTemplate): number {
  return Math.round(estimateTrainingMinutes(tpl) * 1.5);
}

// Curated example input/output pairs per task type so users can see what
// the fine-tuned model is actually expected to produce.
function getExamplePrompts(tpl: ProjectTemplate): { input: string; output: string }[] {
  const isThai = tpl.tags.includes("thai");
  switch (tpl.taskType) {
    case "classification":
      if (tpl.tags.includes("sentiment")) {
        return [
          { input: isThai ? "สินค้าดีมาก ส่งเร็วทันใจ คุ้มราคา" : "Great product, fast shipping, worth it!", output: "positive" },
          { input: isThai ? "ใช้แล้วพังเลย ไม่แนะนำ" : "Broke after one day. Do not buy.", output: "negative" },
          { input: isThai ? "พอใช้ได้ ราคาตามคุณภาพ" : "It's okay, you get what you pay for.", output: "neutral" },
        ];
      }
      return [
        { input: isThai ? "บัตรเครดิตถูกหักเงินซ้ำสองรอบ" : "My credit card was charged twice this month.", output: "billing" },
        { input: isThai ? "แอปเปิดไม่ได้หลังอัปเดต" : "App keeps crashing after the latest update.", output: "technical" },
        { input: isThai ? "พัสดุยังไม่ถึงเลย รออยู่หนึ่งสัปดาห์" : "Where is my package? It's been a week.", output: "shipping" },
      ];
    case "extraction":
      if (tpl.tags.includes("invoice")) {
        return [
          {
            input: "Invoice #INV-2024-0892\nVendor: Acme Co.\nDate: 2024-03-15\nTotal: $1,240.00\nVAT: $86.80",
            output: '{\n  "vendor": "Acme Co.",\n  "invoice_number": "INV-2024-0892",\n  "date": "2024-03-15",\n  "total": 1240.00,\n  "tax": 86.80\n}',
          },
        ];
      }
      return [
        {
          input: "Jane Doe — Senior Engineer at TechCorp (2019-2024). MSc CS, MIT 2018. Skills: Python, Go, Kubernetes.",
          output: '{\n  "name": "Jane Doe",\n  "experience": [{"role": "Senior Engineer", "company": "TechCorp"}],\n  "education": [{"degree": "MSc CS", "school": "MIT"}],\n  "skills": ["Python", "Go", "Kubernetes"]\n}',
        },
      ];
    case "ner":
      return [
        {
          input: isThai ? "นายสมชาย ใจดี ทำงานที่บริษัท ปตท. กรุงเทพ ได้รับเงิน 50,000 บาท" : "John Smith works at Google in San Francisco and earned $120,000.",
          output: isThai
            ? '[{"text":"สมชาย ใจดี","type":"PERSON"},{"text":"ปตท.","type":"ORG"},{"text":"กรุงเทพ","type":"LOC"},{"text":"50,000 บาท","type":"MONEY"}]'
            : '[{"text":"John Smith","type":"PERSON"},{"text":"Google","type":"ORG"},{"text":"San Francisco","type":"LOC"},{"text":"$120,000","type":"MONEY"}]',
        },
      ];
    case "qa":
      return [
        {
          input: isThai ? "Q: คืนสินค้าได้ภายในกี่วัน?\nDocs: นโยบายคืนสินค้า 30 วันนับจากวันรับ" : "Q: How long is the return window?\nDocs: Returns accepted within 30 days of delivery.",
          output: isThai ? "คืนได้ภายใน 30 วันนับจากวันที่ได้รับสินค้า" : "You have 30 days from delivery to return your item.",
        },
      ];
    case "function-calling":
      return [
        {
          input: "Create a new user named Alice with email alice@example.com",
          output: '{\n  "function": "users.create",\n  "args": {"name": "Alice", "email": "alice@example.com"}\n}',
        },
        {
          input: "Show me orders from last week over $500",
          output: '{\n  "function": "orders.list",\n  "args": {"date_from": "last_week", "min_total": 500}\n}',
        },
      ];
    case "ranking":
      return [
        {
          input: 'Query: "wireless noise-cancelling headphones"\nCandidates:\n1. "Bluetooth speaker portable"\n2. "Sony WH-1000XM5 ANC headphones"\n3. "Wired earbuds 3.5mm"',
          output: "[2, 1, 3]",
        },
      ];
    default:
      return [{ input: tpl.prompt, output: "..." }];
  }
}

// Derive how the dataset will be split & prepared during training.
function getDatasetConfig(tpl: ProjectTemplate) {
  const total = tpl.datasetSize;
  const train = Math.round(total * 0.8);
  const val = Math.round(total * 0.1);
  const test = total - train - val;
  return {
    total,
    train,
    val,
    test,
    format:
      tpl.taskType === "extraction" || tpl.taskType === "function-calling"
        ? "JSONL (instruction → JSON)"
        : "JSONL (instruction → response)",
    augmentation: tpl.tags.includes("thai")
      ? "Back-translation + synonym swap (TH)"
      : "Paraphrase + token dropout",
    minTokens: 32,
    maxTokens: tpl.taskType === "extraction" || tpl.taskType === "qa" ? 2048 : 1024,
  };
}

// Derive production-grade hyperparameters from template config so users
// see the complete training recipe before deploying.
function deriveFullHyperparams(tpl: ProjectTemplate) {
  const sizeBatch: Record<string, number> = {
    "smollm2-1.7b": 16,
    "qwen2.5-1.5b": 16,
    "llama-3.2-1b": 32,
    "phi-3-mini": 16,
    "gemma-2-2b": 8,
    "qwen2.5-3b": 8,
  };
  const batchSize = sizeBatch[tpl.baseModel] ?? 16;
  const gradAccum = tpl.datasetSize > 3000 ? 4 : 2;
  return {
    batchSize,
    gradAccum,
    warmupRatio: 0.03,
    weightDecay: 0.01,
    scheduler: "cosine",
    optimizer: "adamw_torch",
    maxSeqLen: tpl.taskType === "extraction" || tpl.taskType === "qa" ? 2048 : 1024,
    precision: "bf16",
  };
}

// Normalize Thai/Latin text for fuzzy search:
// - lowercase, strip diacritics, collapse whitespace
// - remove Thai tone marks & vowel marks for loose matching
function normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[\u0e30-\u0e3a\u0e47-\u0e4e]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Lightweight fuzzy match: token-based subsequence + substring scoring.
function fuzzyMatch(haystack: string, needle: string): boolean {
  if (!needle) return true;
  const h = normalize(haystack);
  const tokens = normalize(needle).split(" ").filter(Boolean);
  return tokens.every((tok) => {
    if (h.includes(tok)) return true;
    // subsequence fallback for typos/near-spellings
    let i = 0;
    for (const ch of h) {
      if (ch === tok[i]) i++;
      if (i === tok.length) return true;
    }
    return false;
  });
}

export default function Templates() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<typeof templateCategories[number]>("All");
  const [sort, setSort] = useState<SortKey>("popular");
  const [preview, setPreview] = useState<ProjectTemplate | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();

  const filtered = useMemo(() => {
    const list = projectTemplates.filter((tpl) => {
      const haystack = [
        tpl.name,
        tpl.description,
        tpl.longDescription,
        tpl.category,
        tpl.taskType,
        tpl.baseModel,
        tpl.author,
        ...tpl.tags,
      ].join(" ");
      const matchSearch = fuzzyMatch(haystack, search);
      const matchCat =
        category === "All" ||
        (category === "Featured" ? tpl.featured : tpl.category === category);
      return matchSearch && matchCat;
    });

    const sorted = [...list];
    switch (sort) {
      case "rating":
        sorted.sort((a, b) => b.rating - a.rating);
        break;
      case "forks":
        sorted.sort((a, b) => b.forks - a.forks);
        break;
      case "newest":
        sorted.sort((a, b) => b.id.localeCompare(a.id));
        break;
      default:
        sorted.sort(
          (a, b) =>
            (b.featured ? 1 : 0) - (a.featured ? 1 : 0) ||
            b.rating * 100 + Math.log10(b.forks + 1) -
              (a.rating * 100 + Math.log10(a.forks + 1))
        );
    }
    return sorted;
  }, [search, category, sort]);

  const handleCustomize = (tpl: ProjectTemplate) => {
    sessionStorage.setItem("template-prefill", JSON.stringify(tpl));
    toast({ title: t("templates.openingWizard"), description: tpl.name });
    navigate("/projects/new");
  };

  const handleQuickDeploy = async (tpl: ProjectTemplate) => {
    setDeploying(tpl.id);
    try {
      const created = await createProject({
        name: tpl.name,
        description: tpl.prompt,
        taskType: tpl.taskType,
        baseModel: tpl.baseModel,
        epochs: tpl.epochs,
        learningRate: tpl.learningRate,
        datasetSize: tpl.datasetSize,
        tags: tpl.tags,
      });
      toast({
        title: t("templates.deployed"),
        description: created.name,
      });
      setPreview(null);
      navigate(`/projects/${created.id}`);
    } catch (e) {
      toast({
        title: t("templates.deployFailed"),
        description: (e as Error).message,
        variant: "destructive",
      });
    } finally {
      setDeploying(null);
    }
  };

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Sparkles className="h-6 w-6 text-primary" />
                {t("templates.title")}
              </h1>
              <p className="text-sm text-muted-foreground mt-1">{t("templates.subtitle")}</p>
            </div>
            <Badge variant="outline" className="text-xs shrink-0">
              {projectTemplates.length} {t("templates.templates")}
            </Badge>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="space-y-3">
            <div className="flex flex-col sm:flex-row gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder={t("templates.searchPlaceholder")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9"
                />
              </div>
              <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
                <SelectTrigger className="w-full sm:w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="popular">{t("templates.sortPopular")}</SelectItem>
                  <SelectItem value="rating">{t("templates.sortRating")}</SelectItem>
                  <SelectItem value="forks">{t("templates.sortForks")}</SelectItem>
                  <SelectItem value="newest">{t("templates.sortNewest")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-wrap gap-2">
              {templateCategories.map((cat) => (
                <Button
                  key={cat}
                  variant={category === cat ? "default" : "outline"}
                  size="sm"
                  className="text-xs h-7"
                  onClick={() => setCategory(cat)}
                >
                  {cat}
                </Button>
              ))}
            </div>
          </div>
        </FadeIn>

        <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((tpl) => {
            const minutes = estimateTrainingMinutes(tpl);
            const credits = estimateCredits(tpl);
            return (
              <MotionCard key={tpl.id}>
                <Card className="h-full hover:shadow-lg hover:border-primary/40 transition-all flex flex-col">
                  <CardContent className="p-4 space-y-3 flex-1 flex flex-col">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold text-foreground leading-tight">
                        {tpl.name}
                      </h3>
                      {tpl.featured && (
                        <Badge
                          className="text-[9px] shrink-0 bg-primary/15 text-primary border-primary/30"
                          variant="outline"
                        >
                          ★ {t("templates.featured")}
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2 flex-1">
                      {tpl.description}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="secondary" className="text-[10px]">{tpl.category}</Badge>
                      <Badge variant="outline" className="text-[10px]">{tpl.taskType}</Badge>
                      <Badge variant="outline" className="text-[10px]">{tpl.baseModel}</Badge>
                    </div>

                    <div className="grid grid-cols-3 gap-2 text-[10px] text-muted-foreground pt-2 border-t border-border">
                      <div className="flex flex-col items-center">
                        <Clock className="h-3 w-3 mb-0.5" />
                        <span className="font-medium text-foreground">~{minutes}m</span>
                      </div>
                      <div className="flex flex-col items-center">
                        <Coins className="h-3 w-3 mb-0.5" />
                        <span className="font-medium text-foreground">{credits}</span>
                      </div>
                      <div className="flex flex-col items-center">
                        <Database className="h-3 w-3 mb-0.5" />
                        <span className="font-medium text-foreground">
                          {(tpl.datasetSize / 1000).toFixed(1)}k
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                      <span className="flex items-center gap-3">
                        <span className="flex items-center gap-1">
                          <Star className="h-3 w-3 fill-yellow-500 text-yellow-500" />
                          {tpl.rating}
                        </span>
                        <span className="flex items-center gap-1">
                          <GitFork className="h-3 w-3" />
                          {tpl.forks.toLocaleString()}
                        </span>
                      </span>
                      <span className="truncate max-w-[100px]">{tpl.author}</span>
                    </div>

                    <div className="flex gap-1.5">
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 gap-1.5"
                        onClick={() => setPreview(tpl)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                        {t("templates.preview")}
                      </Button>
                      <Button
                        size="sm"
                        className="flex-1 gap-1.5"
                        onClick={() => handleQuickDeploy(tpl)}
                        disabled={deploying === tpl.id}
                      >
                        {deploying === tpl.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Zap className="h-3.5 w-3.5" />
                        )}
                        {t("templates.quickDeploy")}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </MotionCard>
            );
          })}
        </StaggerContainer>

        {filtered.length === 0 && (
          <FadeIn>
            <div className="text-center py-12 text-muted-foreground">
              <p className="text-sm">{t("templates.noResults")}</p>
            </div>
          </FadeIn>
        )}

        <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
            {preview && (
              <>
                <DialogHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <DialogTitle className="flex items-center gap-2">
                        {preview.name}
                        {preview.featured && (
                          <Badge
                            variant="outline"
                            className="text-[9px] bg-primary/15 text-primary border-primary/30"
                          >
                            ★ {t("templates.featured")}
                          </Badge>
                        )}
                      </DialogTitle>
                      <DialogDescription className="mt-1">
                        {t("templates.by")} {preview.author}
                      </DialogDescription>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0">
                      <span className="flex items-center gap-1">
                        <Star className="h-3 w-3 fill-yellow-500 text-yellow-500" />
                        {preview.rating}
                      </span>
                      <span className="flex items-center gap-1">
                        <GitFork className="h-3 w-3" />
                        {preview.forks.toLocaleString()}
                      </span>
                    </div>
                  </div>
                </DialogHeader>

                <div className="space-y-4">
                  <p className="text-sm text-foreground leading-relaxed">
                    {preview.longDescription}
                  </p>

                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="secondary" className="text-[10px]">{preview.category}</Badge>
                    <Badge variant="outline" className="text-[10px]">{preview.taskType}</Badge>
                    <Badge variant="outline" className="text-[10px]">{preview.baseModel}</Badge>
                    {preview.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-[10px]">
                        #{tag}
                      </Badge>
                    ))}
                  </div>

                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1.5">
                      {t("templates.taskPrompt")}
                    </p>
                    <div className="p-3 rounded-md bg-muted/50 border border-border text-xs font-mono text-foreground leading-relaxed">
                      {preview.prompt}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div className="p-3 rounded-md border border-border">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                        {t("templates.epochs")}
                      </p>
                      <p className="text-sm font-semibold text-foreground mt-0.5">
                        {preview.epochs}
                      </p>
                    </div>
                    <div className="p-3 rounded-md border border-border">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                        {t("templates.learningRate")}
                      </p>
                      <p className="text-sm font-semibold text-foreground mt-0.5">
                        {preview.learningRate.toExponential(0)}
                      </p>
                    </div>
                    <div className="p-3 rounded-md border border-border">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                        {t("templates.datasetSize")}
                      </p>
                      <p className="text-sm font-semibold text-foreground mt-0.5">
                        {preview.datasetSize.toLocaleString()}
                      </p>
                    </div>
                    <div className="p-3 rounded-md border border-border">
                      <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                        {t("templates.estTime")}
                      </p>
                      <p className="text-sm font-semibold text-foreground mt-0.5">
                        ~{estimateTrainingMinutes(preview)}m
                      </p>
                    </div>
                  </div>

                  {(() => {
                    const hp = deriveFullHyperparams(preview);
                    const rows: { label: string; value: React.ReactNode }[] = [
                      { label: t("templates.epochs"), value: preview.epochs },
                      { label: t("templates.learningRate"), value: preview.learningRate.toExponential(1) },
                      { label: t("templates.batchSize"), value: hp.batchSize },
                      { label: t("templates.gradAccum"), value: hp.gradAccum },
                      { label: t("templates.warmupRatio"), value: hp.warmupRatio },
                      { label: t("templates.weightDecay"), value: hp.weightDecay },
                      { label: t("templates.scheduler"), value: hp.scheduler },
                      { label: t("templates.optimizer"), value: hp.optimizer },
                      { label: t("templates.maxSeqLen"), value: hp.maxSeqLen },
                      { label: t("templates.precision"), value: hp.precision },
                      { label: t("templates.datasetSize"), value: preview.datasetSize.toLocaleString() },
                      { label: t("templates.estTime"), value: `~${estimateTrainingMinutes(preview)}m` },
                    ];
                    return (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1.5">
                          {t("templates.fullHyperparams")}
                        </p>
                        <div className="rounded-md border border-border divide-y divide-border overflow-hidden">
                          {rows.map((r) => (
                            <div
                              key={r.label}
                              className="flex items-center justify-between px-3 py-1.5 text-xs"
                            >
                              <span className="text-muted-foreground">{r.label}</span>
                              <span className="font-mono font-medium text-foreground">{r.value}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {(() => {
                    const examples = getExamplePrompts(preview);
                    return (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1">
                          {t("templates.examplePrompts")}
                        </p>
                        <p className="text-[10px] text-muted-foreground mb-2">
                          {t("templates.examplePromptsHint")}
                        </p>
                        <div className="space-y-2">
                          {examples.map((ex, i) => (
                            <div
                              key={i}
                              className="rounded-md border border-border overflow-hidden"
                            >
                              <div className="px-3 py-1.5 bg-muted/50 border-b border-border flex items-center gap-2">
                                <Badge variant="outline" className="text-[9px] h-4 px-1.5">
                                  {t("templates.exampleInput")}
                                </Badge>
                                <span className="text-[10px] text-muted-foreground">#{i + 1}</span>
                              </div>
                              <pre className="px-3 py-2 text-xs font-mono text-foreground whitespace-pre-wrap break-words">
{ex.input}
                              </pre>
                              <div className="px-3 py-1.5 bg-primary/5 border-y border-border">
                                <Badge
                                  variant="outline"
                                  className="text-[9px] h-4 px-1.5 bg-primary/10 text-primary border-primary/30"
                                >
                                  {t("templates.exampleOutput")}
                                </Badge>
                              </div>
                              <pre className="px-3 py-2 text-xs font-mono text-foreground whitespace-pre-wrap break-words">
{ex.output}
                              </pre>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {(() => {
                    const dc = getDatasetConfig(preview);
                    const pct = (n: number) => `${Math.round((n / dc.total) * 100)}%`;
                    return (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground mb-1.5">
                          {t("templates.datasetConfig")}
                        </p>
                        <div className="rounded-md border border-border p-3 space-y-3">
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">
                              {t("templates.totalSamples")}
                            </span>
                            <span className="font-mono font-semibold text-foreground">
                              {dc.total.toLocaleString()}
                            </span>
                          </div>
                          <div>
                            <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className="bg-primary"
                                style={{ width: pct(dc.train) }}
                              />
                              <div
                                className="bg-primary/60"
                                style={{ width: pct(dc.val) }}
                              />
                              <div
                                className="bg-primary/30"
                                style={{ width: pct(dc.test) }}
                              />
                            </div>
                            <div className="grid grid-cols-3 gap-2 mt-2 text-[11px]">
                              <div>
                                <div className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-primary" />
                                  <span className="text-muted-foreground">
                                    {t("templates.trainSplit")}
                                  </span>
                                </div>
                                <p className="font-mono font-medium text-foreground mt-0.5">
                                  {dc.train.toLocaleString()} ({pct(dc.train)})
                                </p>
                              </div>
                              <div>
                                <div className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-primary/60" />
                                  <span className="text-muted-foreground">
                                    {t("templates.valSplit")}
                                  </span>
                                </div>
                                <p className="font-mono font-medium text-foreground mt-0.5">
                                  {dc.val.toLocaleString()} ({pct(dc.val)})
                                </p>
                              </div>
                              <div>
                                <div className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-primary/30" />
                                  <span className="text-muted-foreground">
                                    {t("templates.testSplit")}
                                  </span>
                                </div>
                                <p className="font-mono font-medium text-foreground mt-0.5">
                                  {dc.test.toLocaleString()} ({pct(dc.test)})
                                </p>
                              </div>
                            </div>
                          </div>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 border-t border-border text-xs">
                            <div>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                                {t("templates.dataFormat")}
                              </p>
                              <p className="font-mono text-foreground mt-0.5">{dc.format}</p>
                            </div>
                            <div>
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                                {t("templates.tokenRange")}
                              </p>
                              <p className="font-mono text-foreground mt-0.5">
                                {dc.minTokens} – {dc.maxTokens}
                              </p>
                            </div>
                            <div className="sm:col-span-2">
                              <p className="text-[10px] text-muted-foreground uppercase tracking-wide">
                                {t("templates.augmentation")}
                              </p>
                              <p className="font-mono text-foreground mt-0.5">
                                {dc.augmentation}
                              </p>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })()}

                  <div className="p-3 rounded-md bg-primary/5 border border-primary/20 flex items-start gap-2">
                    <Coins className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                    <div className="text-xs">
                      <p className="font-medium text-foreground">
                        {t("templates.estCost")}: ~{estimateCredits(preview)} {t("nav.credits").toLowerCase()}
                      </p>
                      <p className="text-muted-foreground mt-0.5">
                        {t("templates.estCostHint")}
                      </p>
                    </div>
                  </div>
                </div>

                <DialogFooter className="gap-2 sm:gap-2">
                  <Button
                    variant="outline"
                    className="gap-1.5"
                    onClick={() => handleCustomize(preview)}
                    disabled={deploying === preview.id}
                  >
                    <SettingsIcon className="h-3.5 w-3.5" />
                    {t("templates.customize")}
                  </Button>
                  <Button
                    className="gap-1.5"
                    onClick={() => handleQuickDeploy(preview)}
                    disabled={deploying === preview.id}
                  >
                    {deploying === preview.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Rocket className="h-3.5 w-3.5" />
                    )}
                    {t("templates.quickDeploy")}
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </PageTransition>
  );
}
