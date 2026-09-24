import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock, Eye, GitFork, Layers, Loader2, Rocket, Search, Sparkles, Star, X, Zap } from "lucide-react";

import { ApiError } from "@/api/client";
import type { Template, TemplateSort } from "@/api/types";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { FadeIn, PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useTemplates } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";

type CategoryFilter = "all" | "featured" | string;

/** The Engine's template marketplace is the single source of truth. A
 * template may remain visible when its training corpus is unavailable, but
 * the backend's `available` flag must prevent it from starting a project. */
export default function Templates() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [isComposing, setIsComposing] = useState(false);
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [sort, setSort] = useState<TemplateSort>("popular");
  const [preview, setPreview] = useState<Template | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const navigate = useNavigate();

  // Server-side filtering preserves the Engine's availability and ratings.
  // Waiting for composition to end prevents partial IME queries.
  useEffect(() => {
    if (isComposing) return;
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [isComposing, searchInput]);

  const query = useMemo(() => ({
    category: category !== "all" && category !== "featured" ? category : undefined,
    featured: category === "featured" ? true : undefined,
    search: search || undefined,
    sort,
    limit: 100,
  }), [category, search, sort]);
  const { data, isLoading, isFetching, isError, error, refetch } = useTemplates(query);
  // Keep every category reachable after a server-side category/search filter
  // narrows the visible cards. TanStack deduplicates this with the main query
  // while the default catalogue view is active.
  const { data: catalogue } = useTemplates({ limit: 200 });
  const templates = useMemo(() => data?.items ?? [], [data?.items]);
  const categories = useMemo(
    () => [...new Set((catalogue?.items ?? templates).map((template) => template.category))].sort((a, b) => a.localeCompare(b)),
    [catalogue?.items, templates],
  );

  const clearSearch = () => {
    setSearchInput("");
    setSearch("");
    searchRef.current?.focus();
  };

  const handleUseTemplate = (template: Template) => {
    if (!template.available) return;
    toast({
      title: "Opening New Project wizard",
      description: `Loaded settings from “${template.name}”. Add your training data in the next step.`,
    });
    navigate("/projects/new", {
      state: { template: {
        name: template.name,
        taskType: template.task_type,
        prompt: template.prompt,
        baseModel: template.base_model,
      } },
    });
  };

  const errorDetail = error instanceof ApiError
    ? { detail: error.message, code: error.code }
    : { detail: error instanceof Error ? error.message : "Unable to load templates." };

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2"><Sparkles className="h-6 w-6 text-primary" /> Template Marketplace</h1>
              <p className="text-sm text-muted-foreground">Curated Engine templates for classification, tool calling, and QA.</p>
            </div>
            <Badge variant="outline" className="w-fit px-3 py-1 text-xs" aria-live="polite">{data?.total ?? 0} templates available</Badge>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden />
              <Input
                ref={searchRef}
                placeholder="Search templates by name, task, or tag..."
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onCompositionStart={() => setIsComposing(true)}
                onCompositionEnd={() => setIsComposing(false)}
                className="pl-9 pr-10"
                aria-label="Search templates"
              />
              {searchInput && <Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1/2 h-8 w-8 -translate-y-1/2" onClick={clearSearch} aria-label="Clear template search"><X className="h-4 w-4" /></Button>}
            </div>
            <div className="flex w-full sm:w-auto rounded-md border border-input p-1" role="group" aria-label="Sort templates">
              {([
                ["popular", "Popular"],
                ["rating", "Rating"],
                ["forks", "Forks"],
              ] as const).map(([value, label]) => (
                <Button key={value} type="button" variant={sort === value ? "secondary" : "ghost"} size="sm" onClick={() => setSort(value)} aria-pressed={sort === value}>{label}</Button>
              ))}
            </div>
          </div>
        </FadeIn>

        {!isLoading && categories.length > 0 && <FadeIn delay={0.1}>
          <div className="flex items-center gap-1.5 overflow-x-auto pb-2" aria-label="Template categories">
            {(["all", "featured", ...categories] as CategoryFilter[]).map((value) => {
              const label = value === "all" ? "All" : value === "featured" ? "Featured" : value;
              return <Button key={value} variant={category === value ? "default" : "outline"} size="sm" onClick={() => setCategory(value)} className="rounded-full text-xs shrink-0" aria-pressed={category === value}>{label}</Button>;
            })}
          </div>
        </FadeIn>}

        {isLoading ? (
          <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground" role="status"><Loader2 className="h-5 w-5 animate-spin" /> Loading templates…</div>
        ) : isError ? (
          <div className="space-y-3"><ErrorDetail error={errorDetail} /><Button variant="outline" onClick={() => void refetch()}>Try again</Button></div>
        ) : templates.length === 0 ? (
          <Card><CardContent className="p-12 text-center space-y-3"><Layers className="h-10 w-10 text-muted-foreground mx-auto" /><p className="font-medium text-foreground">No templates match your filter</p><p className="text-xs text-muted-foreground">Try another search or category.</p><Button variant="outline" size="sm" onClick={() => { clearSearch(); setCategory("all"); }}>Clear filters</Button></CardContent></Card>
        ) : (
          <div className="relative">
            {isFetching && <span className="absolute -top-5 right-0 text-xs text-muted-foreground" role="status">Updating…</span>}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {templates.map((template, index) => <FadeIn key={template.id} delay={0.05 * (index % 6)}><TemplateCard template={template} onPreview={setPreview} onUse={handleUseTemplate} /></FadeIn>)}
            </div>
          </div>
        )}
        <TemplatePreview template={preview} onOpenChange={(open) => !open && setPreview(null)} onUse={handleUseTemplate} />
      </div>
    </PageTransition>
  );
}

function TemplateCard({ template, onPreview, onUse }: { template: Template; onPreview: (template: Template) => void; onUse: (template: Template) => void }) {
  return <Card className="h-full flex flex-col justify-between transition-colors hover:border-primary/50"><CardContent className="p-5 space-y-4">
    <div className="flex items-start justify-between gap-2"><div className="space-y-1 min-w-0"><div className="flex items-center gap-1.5 flex-wrap"><Badge variant="secondary" className="text-[10px] uppercase font-semibold">{template.task_type}</Badge>{template.featured && <Badge className="text-[10px] gap-1 bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30"><Star className="h-3 w-3 fill-current" /> Featured</Badge>}{!template.available && <Badge variant="destructive" className="text-[10px]">Unavailable</Badge>}</div><h2 className="font-semibold text-foreground text-base">{template.name}</h2></div><div className="flex items-center gap-1 text-xs text-muted-foreground shrink-0" aria-label={template.rating === null ? "Not yet rated" : `Rated ${template.rating} out of 5`}><Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500" aria-hidden /><span className="font-medium text-foreground">{template.rating?.toFixed(1) ?? "—"}</span></div></div>
    <p className="text-xs text-muted-foreground line-clamp-2">{template.description}</p>
    {!template.available && template.unavailable_reason && <p className="rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-foreground">{template.unavailable_reason}</p>}
    <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground"><span className="bg-muted px-2 py-0.5 rounded font-mono truncate max-w-[180px]">{template.base_model.split("/").pop()}</span><span className="flex items-center gap-1"><Clock className="h-3 w-3" /> {template.epochs} epochs</span><span className="flex items-center gap-1"><GitFork className="h-3 w-3" /> {template.forks.toLocaleString()}</span></div>
    <div className="flex items-center gap-1 flex-wrap">{template.tags.map((tag) => <span key={tag} className="text-[10px] text-muted-foreground bg-muted/60 px-1.5 py-0.5 rounded">#{tag}</span>)}</div>
    <div className="pt-2 border-t flex items-center gap-2"><Button variant="outline" size="sm" className="flex-1 text-xs gap-1.5" onClick={() => onPreview(template)}><Eye className="h-3.5 w-3.5" /> Preview</Button><Button size="sm" className="flex-1 text-xs gap-1.5" disabled={!template.available} onClick={() => onUse(template)}><Rocket className="h-3.5 w-3.5" /> Use template</Button></div>
  </CardContent></Card>;
}

function TemplatePreview({ template, onOpenChange, onUse }: { template: Template | null; onOpenChange: (open: boolean) => void; onUse: (template: Template) => void }) {
  return <Dialog open={Boolean(template)} onOpenChange={onOpenChange}><DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">{template && <>
    <DialogHeader className="space-y-2"><div className="flex items-center gap-2"><Badge variant="outline">{template.category}</Badge><Badge variant="secondary" className="font-mono text-xs">{template.task_type}</Badge></div><DialogTitle className="text-xl">{template.name}</DialogTitle><DialogDescription>{template.long_description}</DialogDescription></DialogHeader>
    <div className="space-y-4 py-2"><div className="rounded-lg border p-3.5 bg-muted/40 space-y-1.5"><div className="text-xs font-semibold text-foreground flex items-center gap-1.5"><Zap className="h-3.5 w-3.5 text-primary" /> Task prompt</div><p className="text-xs font-mono text-muted-foreground bg-background p-2.5 rounded border">{template.prompt}</p></div><div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center"><TemplateFact label="Base model" value={template.base_model.split("/").pop() ?? template.base_model} title={template.base_model} /><TemplateFact label="Epochs" value={String(template.epochs)} /><TemplateFact label="Learning rate" value={String(template.learning_rate)} /><TemplateFact label="Dataset rows" value={template.dataset_size.toLocaleString()} /></div>{!template.available && template.unavailable_reason && <ErrorDetail error={{ detail: template.unavailable_reason, code: "template_unavailable" }} />}</div>
    <DialogFooter className="flex sm:justify-between items-center gap-2"><Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Close</Button><Button size="sm" className="gap-1.5" disabled={!template.available} onClick={() => { onOpenChange(false); onUse(template); }}><Rocket className="h-4 w-4" /> Start project with this template</Button></DialogFooter>
  </>}</DialogContent></Dialog>;
}

function TemplateFact({ label, value, title }: { label: string; value: string; title?: string }) {
  return <div className="border rounded-lg p-2.5 min-w-0"><p className="text-[11px] text-muted-foreground">{label}</p><p className="font-semibold text-xs text-foreground truncate mt-0.5" title={title}>{value}</p></div>;
}
