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
import { PageTransition, FadeIn } from "@/components/motion";
import {
  Search,
  Star,
  GitFork,
  Sparkles,
  Rocket,
  Eye,
  Clock,
  Layers,
  Zap,
} from "lucide-react";
import { projectTemplates, templateCategories, type ProjectTemplate } from "@/data/templatesMockData";
import { useToast } from "@/hooks/use-toast";

type SortKey = "popular" | "rating" | "forks";

export default function Templates() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<typeof templateCategories[number]>("All");
  const [sort, setSort] = useState<SortKey>("popular");
  const [preview, setPreview] = useState<ProjectTemplate | null>(null);
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
      ].join(" ").toLowerCase();
      const matchSearch = !search || haystack.includes(search.toLowerCase());
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
      case "popular":
      default:
        sorted.sort((a, b) => (b.forks + b.rating * 100) - (a.forks + a.rating * 100));
        break;
    }
    return sorted;
  }, [search, category, sort]);

  const handleUseTemplate = (tpl: ProjectTemplate) => {
    toast({
      title: "Opening New Project wizard",
      description: `Loaded settings from "${tpl.name}".`,
    });
    navigate("/projects/new", {
      state: {
        template: {
          name: tpl.name,
          taskType: tpl.taskType,
          prompt: tpl.prompt,
          baseModel: tpl.baseModel,
        },
      },
    });
  };

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Sparkles className="h-6 w-6 text-primary" /> Template Marketplace
              </h1>
              <p className="text-sm text-muted-foreground">
                Pre-configured pipelines for classification, tool calling, and QA. Start training in seconds.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="px-3 py-1 text-xs">
                {projectTemplates.length} templates available
              </Badge>
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search templates by name, task, or tag..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
              />
            </div>
            <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="popular">Most Popular</SelectItem>
                <SelectItem value="rating">Highest Rated</SelectItem>
                <SelectItem value="forks">Most Forked</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="flex items-center gap-1.5 overflow-x-auto pb-2 scrollbar-none">
            {templateCategories.map((cat) => (
              <Button
                key={cat}
                variant={category === cat ? "default" : "outline"}
                size="sm"
                onClick={() => setCategory(cat)}
                className="rounded-full text-xs shrink-0"
              >
                {cat}
              </Button>
            ))}
          </div>
        </FadeIn>

        {filtered.length === 0 ? (
          <Card>
            <CardContent className="p-12 text-center space-y-3">
              <Layers className="h-10 w-10 text-muted-foreground mx-auto" />
              <p className="font-medium text-foreground">No templates match your filter</p>
              <p className="text-xs text-muted-foreground">
                Try adjusting your search keywords or switching category filters.
              </p>
              <Button variant="outline" size="sm" onClick={() => { setSearch(""); setCategory("All"); }}>
                Clear Filters
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {filtered.map((tpl, i) => (
              <FadeIn key={tpl.id} delay={0.05 * (i % 6)}>
                <Card className="h-full flex flex-col justify-between hover:border-primary/50 transition-all group">
                  <CardContent className="p-5 space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="space-y-1">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <Badge variant="secondary" className="text-[10px] uppercase font-semibold">
                            {tpl.taskType}
                          </Badge>
                          {tpl.featured && (
                            <Badge variant="default" className="text-[10px] gap-1 bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30">
                              <Star className="h-3 w-3 fill-current" /> Featured
                            </Badge>
                          )}
                        </div>
                        <h3 className="font-semibold text-foreground text-base group-hover:text-primary transition-colors">
                          {tpl.name}
                        </h3>
                      </div>
                      <div className="flex items-center gap-1 text-xs text-muted-foreground shrink-0">
                        <Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500" />
                        <span className="font-medium text-foreground">{tpl.rating}</span>
                      </div>
                    </div>

                    <p className="text-xs text-muted-foreground line-clamp-2">
                      {tpl.description}
                    </p>

                    <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground">
                      <span className="bg-muted px-2 py-0.5 rounded font-mono truncate max-w-[180px]">
                        {tpl.baseModel.split("/").pop()}
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" /> {tpl.epochs} epochs
                      </span>
                      <span className="flex items-center gap-1">
                        <GitFork className="h-3 w-3" /> {tpl.forks.toLocaleString()}
                      </span>
                    </div>

                    <div className="flex items-center gap-1 flex-wrap">
                      {tpl.tags.map((tag) => (
                        <span key={tag} className="text-[10px] text-muted-foreground bg-muted/60 px-1.5 py-0.5 rounded">
                          #{tag}
                        </span>
                      ))}
                    </div>

                    <div className="pt-2 border-t flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1 text-xs gap-1.5"
                        onClick={() => setPreview(tpl)}
                      >
                        <Eye className="h-3.5 w-3.5" /> Preview
                      </Button>
                      <Button
                        size="sm"
                        className="flex-1 text-xs gap-1.5"
                        onClick={() => handleUseTemplate(tpl)}
                      >
                        <Rocket className="h-3.5 w-3.5" /> Use Template
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </FadeIn>
            ))}
          </div>
        )}

        {/* Template Preview Dialog */}
        <Dialog open={!!preview} onOpenChange={(open) => !open && setPreview(null)}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
            {preview && (
              <>
                <DialogHeader className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{preview.category}</Badge>
                    <Badge variant="secondary" className="font-mono text-xs">{preview.taskType}</Badge>
                  </div>
                  <DialogTitle className="text-xl flex items-center gap-2">
                    {preview.name}
                  </DialogTitle>
                  <DialogDescription>
                    {preview.longDescription}
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                  <div className="rounded-lg border p-3.5 bg-muted/40 space-y-1.5">
                    <div className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                      <Zap className="h-3.5 w-3.5 text-primary" /> Task Prompt Configuration
                    </div>
                    <p className="text-xs font-mono text-muted-foreground bg-background p-2.5 rounded border">
                      {preview.prompt}
                    </p>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                    <div className="border rounded-lg p-2.5">
                      <p className="text-[11px] text-muted-foreground">Task Type</p>
                      <p className="font-semibold text-xs text-foreground uppercase mt-0.5">{preview.taskType}</p>
                    </div>
                    <div className="border rounded-lg p-2.5">
                      <p className="text-[11px] text-muted-foreground">Base Model</p>
                      <p className="font-semibold text-xs text-foreground truncate mt-0.5" title={preview.baseModel}>
                        {preview.baseModel.split("/").pop()}
                      </p>
                    </div>
                    <div className="border rounded-lg p-2.5">
                      <p className="text-[11px] text-muted-foreground">Epochs</p>
                      <p className="font-semibold text-xs text-foreground mt-0.5">{preview.epochs}</p>
                    </div>
                    <div className="border rounded-lg p-2.5">
                      <p className="text-[11px] text-muted-foreground">Learning Rate</p>
                      <p className="font-semibold text-xs text-foreground mt-0.5">{preview.learningRate}</p>
                    </div>
                  </div>

                  <div className="text-xs text-muted-foreground space-y-1">
                    <p>• <strong>Target Task:</strong> {preview.taskType} pipeline configured for the Engine backend.</p>
                    <p>• <strong>Author:</strong> {preview.author} · {preview.forks.toLocaleString()} projects launched with this template.</p>
                  </div>
                </div>

                <DialogFooter className="flex sm:justify-between items-center gap-2">
                  <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
                    Close
                  </Button>
                  <Button size="sm" className="gap-1.5" onClick={() => { const t = preview; setPreview(null); handleUseTemplate(t); }}>
                    <Rocket className="h-4 w-4" /> Start Project with this Template
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
