import { useState, useEffect } from "react";
import {
  Key,
  Plus,
  Copy,
  Check,
  ShieldAlert,
  Loader2,
  ShieldCheck,
  Eye,
  EyeOff,
  Code2,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { listApiKeys, createApiKey, revokeApiKey, type ApiKey } from "@/lib/apiKeysApi";

export function ApiKeyManager() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  const [copiedModalSecret, setCopiedModalSecret] = useState(false);
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [showAllSecrets, setShowAllSecrets] = useState(false);

  // Quickstart code snippets state
  const [quickstartKeyId, setQuickstartKeyId] = useState<string>("");
  const [quickstartShowReal, setQuickstartShowReal] = useState(false);
  const [copiedSnippet, setCopiedSnippet] = useState(false);

  const { toast } = useToast();

  const fetchKeys = async () => {
    try {
      const res = await listApiKeys();
      setKeys(res);
      if (res.length > 0 && !quickstartKeyId) {
        const firstActive = res.find((k) => k.status === "active") || res[0];
        setQuickstartKeyId(firstActive.id);
      }
    } catch {
      // Ignored
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKeys();
  }, []);

  const handleCreate = async () => {
    if (!newKeyName.trim()) return;
    setSubmitting(true);
    try {
      const result = await createApiKey(newKeyName.trim());
      setCreatedSecret(result.rawKey);
      setNewKeyName("");
      setCreateOpen(false);
      await fetchKeys();
      setQuickstartKeyId(result.id);
      toast({ title: "API key created successfully" });
    } catch (e) {
      toast({
        title: "Could not create API key",
        description: e instanceof Error ? e.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (id: string, name: string) => {
    try {
      await revokeApiKey(id);
      toast({ title: "API key revoked", description: `"${name}" is now disabled.` });
      await fetchKeys();
    } catch (e) {
      toast({
        title: "Could not revoke key",
        description: e instanceof Error ? e.message : "Unknown error",
        variant: "destructive",
      });
    }
  };

  const handleCopyKey = (secret: string, keyId: string) => {
    navigator.clipboard.writeText(secret);
    setCopiedKeyId(keyId);
    toast({ title: "API Key copied to clipboard" });
    setTimeout(() => setCopiedKeyId(null), 2000);
  };

  const handleCopyModalSecret = (secret: string) => {
    navigator.clipboard.writeText(secret);
    setCopiedModalSecret(true);
    toast({ title: "Secret key copied to clipboard" });
    setTimeout(() => setCopiedModalSecret(false), 2000);
  };

  const toggleShowSecret = (id: string) => {
    setShowSecrets((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleShowAll = () => {
    const nextState = !showAllSecrets;
    setShowAllSecrets(nextState);
    const updated: Record<string, boolean> = {};
    keys.forEach((k) => {
      updated[k.id] = nextState;
    });
    setShowSecrets(updated);
  };

  const activeKeys = keys.filter((k) => k.status === "active");
  const selectedSnippetKey = keys.find((k) => k.id === quickstartKeyId) || activeKeys[0];

  const getMaskedDisplay = (k: ApiKey) => {
    const suffixTail = k.keySuffix.length > 4 ? k.keySuffix.slice(-4) : k.keySuffix;
    return `${k.keyPrefix}••••••••${suffixTail}`;
  };

  const snippetToken = selectedSnippetKey
    ? quickstartShowReal
      ? selectedSnippetKey.rawKey
      : getMaskedDisplay(selectedSnippetKey)
    : "YOUR_API_KEY";

  const pythonSnippet = `import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/api/v1/inference",
    api_key="${snippetToken}"
)

response = client.chat.completions.create(
    model="my-finetuned-model",
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=0.7,
)

print(response.choices[0].message.content)`;

  const curlSnippet = `curl -X POST http://localhost:8000/api/v1/inference/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${snippetToken}" \\
  -d '{
    "model": "my-finetuned-model",
    "messages": [{"role": "user", "content": "Hello!"}],
    "temperature": 0.7
  }'`;

  const tsSnippet = `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8000/api/v1/inference",
  apiKey: "${snippetToken}",
});

async function main() {
  const completion = await client.chat.completions.create({
    model: "my-finetuned-model",
    messages: [{ role: "user", content: "Hello!" }],
  });
  console.log(completion.choices[0].message.content);
}

main();`;

  const handleCopySnippetText = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedSnippet(true);
    toast({ title: "Code snippet copied" });
    setTimeout(() => setCopiedSnippet(false), 2000);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Key className="h-4 w-4 text-primary" /> API Keys
                </CardTitle>
                <Badge variant="outline" className="text-[10px]">
                  {activeKeys.length} Active
                </Badge>
              </div>
              <CardDescription className="text-xs mt-1">
                Manage and reveal your secret keys for authenticating inference requests and API pipelines.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 self-start sm:self-auto">
              {keys.length > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5 text-xs h-8"
                  onClick={toggleShowAll}
                  title={showAllSecrets ? "Hide all keys" : "Reveal all real API keys"}
                >
                  {showAllSecrets ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  {showAllSecrets ? "Hide All" : "Reveal Real Keys"}
                </Button>
              )}
              <Button size="sm" className="gap-1.5 text-xs h-8" onClick={() => setCreateOpen(true)}>
                <Plus className="h-3.5 w-3.5" /> Create New Key
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent>
          {loading ? (
            <div className="py-8 flex items-center justify-center text-xs text-muted-foreground gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading keys...
            </div>
          ) : keys.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted-foreground border rounded-lg border-dashed">
              <Key className="h-8 w-8 mx-auto mb-2 opacity-40 text-muted-foreground" />
              <p className="font-medium text-foreground">No API keys created yet</p>
              <p className="text-muted-foreground mt-0.5">
                Create a secret key to authenticate your models in external applications or scripts.
              </p>
            </div>
          ) : (
            <div className="border rounded-lg overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Secret API Key</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="w-24 text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.map((k) => {
                    const isRevealed = Boolean(showSecrets[k.id]);
                    return (
                      <TableRow key={k.id}>
                        <TableCell className="font-semibold text-xs text-foreground">
                          {k.name}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5 flex-wrap sm:flex-nowrap">
                            {isRevealed ? (
                              <code className="text-xs font-mono font-medium text-foreground bg-primary/10 border border-primary/20 px-2 py-0.5 rounded select-all break-all">
                                {k.rawKey}
                              </code>
                            ) : (
                              <span className="font-mono text-xs text-muted-foreground select-none">
                                {getMaskedDisplay(k)}
                              </span>
                            )}
                            <div className="flex items-center gap-0.5 shrink-0">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                onClick={() => toggleShowSecret(k.id)}
                                title={isRevealed ? "Hide API key" : "Show real API key"}
                                aria-label={isRevealed ? "Hide API key" : "Show real API key"}
                              >
                                {isRevealed ? (
                                  <EyeOff className="h-3.5 w-3.5" />
                                ) : (
                                  <Eye className="h-3.5 w-3.5" />
                                )}
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                onClick={() => handleCopyKey(k.rawKey, k.id)}
                                title="Copy real API key"
                                aria-label="Copy real API key"
                              >
                                {copiedKeyId === k.id ? (
                                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                                ) : (
                                  <Copy className="h-3.5 w-3.5" />
                                )}
                              </Button>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          {k.status === "active" ? (
                            <Badge className="text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                              Active
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] text-muted-foreground">
                              Revoked
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {new Date(k.createdAt).toLocaleDateString()}
                        </TableCell>
                        <TableCell className="text-right">
                          {k.status === "active" ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 text-xs text-destructive hover:text-destructive hover:bg-destructive/10 px-2"
                              onClick={() => handleRevoke(k.id, k.name)}
                            >
                              Revoke
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Integration Code Snippets Card */}
      {keys.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div>
                <CardTitle className="text-sm flex items-center gap-2">
                  <Code2 className="h-4 w-4 text-primary" /> API Quickstart & Code Examples
                </CardTitle>
                <CardDescription className="text-xs mt-0.5">
                  Use your real API keys directly in client applications and scripts.
                </CardDescription>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                {activeKeys.length > 0 && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] text-muted-foreground">Key:</span>
                    <Select value={quickstartKeyId} onValueChange={setQuickstartKeyId}>
                      <SelectTrigger className="h-7 text-xs w-[140px] sm:w-[170px]">
                        <SelectValue placeholder="Select Key" />
                      </SelectTrigger>
                      <SelectContent>
                        {activeKeys.map((k) => (
                          <SelectItem key={k.id} value={k.id} className="text-xs">
                            {k.name} ({k.keyPrefix}...)
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}

                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs gap-1.5"
                  onClick={() => setQuickstartShowReal((prev) => !prev)}
                >
                  {quickstartShowReal ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                  {quickstartShowReal ? "Mask Key" : "Show Real Key in Code"}
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent>
            <Tabs defaultValue="curl" className="w-full">
              <div className="flex items-center justify-between border-b pb-1">
                <TabsList className="h-8">
                  <TabsTrigger value="curl" className="text-xs py-1">cURL</TabsTrigger>
                  <TabsTrigger value="python" className="text-xs py-1">Python</TabsTrigger>
                  <TabsTrigger value="ts" className="text-xs py-1">Node / TypeScript</TabsTrigger>
                </TabsList>
              </div>

              <TabsContent value="curl" className="mt-2 relative">
                <Button
                  variant="ghost"
                  size="sm"
                  className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                  onClick={() => handleCopySnippetText(curlSnippet)}
                >
                  {copiedSnippet ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                  Copy
                </Button>
                <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                  {curlSnippet}
                </pre>
              </TabsContent>

              <TabsContent value="python" className="mt-2 relative">
                <Button
                  variant="ghost"
                  size="sm"
                  className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                  onClick={() => handleCopySnippetText(pythonSnippet)}
                >
                  {copiedSnippet ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                  Copy
                </Button>
                <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                  {pythonSnippet}
                </pre>
              </TabsContent>

              <TabsContent value="ts" className="mt-2 relative">
                <Button
                  variant="ghost"
                  size="sm"
                  className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                  onClick={() => handleCopySnippetText(tsSnippet)}
                >
                  {copiedSnippet ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                  Copy
                </Button>
                <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                  {tsSnippet}
                </pre>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      )}

      {/* Dialog: Create Key */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base flex items-center gap-2">
              <Key className="h-4 w-4 text-primary" /> Create API Key
            </DialogTitle>
            <DialogDescription className="text-xs">
              Give your API key a descriptive label to easily identify where it is used.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <label className="text-xs font-medium text-foreground">Key Name</label>
              <Input
                placeholder="e.g. Production Service, Dev Test Key"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                autoFocus
              />
            </div>
          </div>
          <DialogFooter className="gap-2">
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleCreate} disabled={!newKeyName.trim() || submitting}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />}
              Generate Key
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog: Key Reveal Warning */}
      <Dialog open={!!createdSecret} onOpenChange={(open) => !open && setCreatedSecret(null)}>
        <DialogContent className="max-w-md space-y-3">
          <DialogHeader>
            <DialogTitle className="text-base flex items-center gap-2 text-foreground">
              <ShieldCheck className="h-5 w-5 text-emerald-500" /> Save your API Key
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              Your real API key has been generated. You can copy it now, or view and copy it anytime from your API Keys list using the eye icon.
            </DialogDescription>
          </DialogHeader>

          {createdSecret && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 p-2.5 rounded-lg border bg-muted/50 font-mono text-xs text-foreground">
                <span className="flex-1 select-all break-all font-semibold text-primary">{createdSecret}</span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1 text-xs shrink-0"
                  onClick={() => handleCopyModalSecret(createdSecret)}
                >
                  {copiedModalSecret ? (
                    <Check className="h-3.5 w-3.5 text-emerald-500" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {copiedModalSecret ? "Copied" : "Copy"}
                </Button>
              </div>

              <div className="flex items-start gap-2 text-[11px] text-amber-600 dark:text-amber-400 bg-amber-500/10 p-2.5 rounded-lg border border-amber-500/20">
                <ShieldAlert className="h-4 w-4 shrink-0 mt-0.5" />
                <span>Keep this key confidential. Anyone with this key can make inference and data requests.</span>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button size="sm" onClick={() => setCreatedSecret(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
