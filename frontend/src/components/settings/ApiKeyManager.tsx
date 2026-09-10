import { useState, useEffect } from "react";
import { Key, Plus, Copy, Check, Trash2, ShieldAlert, Loader2, ShieldCheck } from "lucide-react";
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
import { useToast } from "@/hooks/use-toast";
import { listApiKeys, createApiKey, revokeApiKey, type ApiKey } from "@/lib/apiKeysApi";

export function ApiKeyManager() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const { toast } = useToast();

  const fetchKeys = async () => {
    try {
      const res = await listApiKeys();
      setKeys(res);
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

  const handleCopySecret = (secret: string) => {
    navigator.clipboard.writeText(secret);
    setCopied(true);
    toast({ title: "Secret copied to clipboard" });
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <CardTitle className="text-base flex items-center gap-2">
                <Key className="h-4 w-4 text-primary" /> API Keys
              </CardTitle>
            </div>
            <CardDescription className="text-xs mt-1">
              Secret keys for programmatic authentication with the inference endpoints and API pipelines.
            </CardDescription>
          </div>
          <Button size="sm" className="gap-1.5 text-xs self-start sm:self-auto" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> Create New Key
          </Button>
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
              Create a secret key to integrate models into external applications or scripts.
            </p>
          </div>
        ) : (
          <div className="border rounded-lg overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Secret Key</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-24 text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="font-semibold text-xs text-foreground">
                      {k.name}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {k.keyPrefix}••••••••{k.keySuffix}
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
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

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
              Please copy this key and store it securely. For security reasons, you will not be able to see it again.
            </DialogDescription>
          </DialogHeader>

          {createdSecret && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 p-2.5 rounded-lg border bg-muted/50 font-mono text-xs text-foreground">
                <span className="flex-1 select-all break-all">{createdSecret}</span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1 text-xs shrink-0"
                  onClick={() => handleCopySecret(createdSecret)}
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "Copied" : "Copy"}
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
    </Card>
  );
}
