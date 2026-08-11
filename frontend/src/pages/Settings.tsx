import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Key, Copy, Eye, EyeOff, Plus, Trash2, Bell, User,
  Check, Shield, Clock, Code, Zap, AlertTriangle,
  Webhook, Send, Pause, Play, RotateCcw,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { PageTransition, FadeIn } from "@/components/motion";
import { useLanguage } from "@/i18n/LanguageContext";
import { restartOnboarding } from "@/components/OnboardingTour";

// --- API Keys Tab ---
import { useApiKeys } from "@/hooks/useUserData";
import { createApiKey, revokeApiKey } from "@/lib/apiKeysApi";

const codeExamples = {
  python: `import openai

client = openai.OpenAI(
    api_key="sk-slm-prod-a8f3...x9k2",
    base_url="https://api.slmstudio.ai/v1"
)

response = client.chat.completions.create(
    model="thai-ner-v2",
    messages=[{"role": "user", "content": "วิเคราะห์: บริษัท ปตท. จำกัด"}],
    temperature=0.1
)
print(response.choices[0].message.content)`,
  curl: `curl -X POST https://api.slmstudio.ai/v1/chat/completions \\
  -H "Authorization: Bearer sk-slm-prod-a8f3...x9k2" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "thai-ner-v2",
    "messages": [{"role": "user", "content": "วิเคราะห์: บริษัท ปตท. จำกัด"}],
    "temperature": 0.1
  }'`,
  javascript: `const response = await fetch("https://api.slmstudio.ai/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer sk-slm-prod-a8f3...x9k2",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "thai-ner-v2",
    messages: [{ role: "user", content: "วิเคราะห์: บริษัท ปตท. จำกัด" }],
    temperature: 0.1,
  }),
});
const data = await response.json();
console.log(data.choices[0].message.content);`,
};

function ApiKeysTab() {
  const { keys, refresh } = useApiKeys();
  const [showKey, setShowKey] = useState<Record<string, boolean>>({});
  const [newKeyName, setNewKeyName] = useState("");
  const [codeLang, setCodeLang] = useState<"python" | "curl" | "javascript">("python");
  const { toast } = useToast();

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  const handleCreateKey = async () => {
    if (!newKeyName.trim()) return;
    try {
      await createApiKey(newKeyName);
      setNewKeyName("");
      await refresh();
      toast({ title: "API key created", description: `Key "${newKeyName}" has been generated.` });
    } catch (e) {
      toast({ title: "Failed to create key", description: (e as Error).message, variant: "destructive" });
    }
  };

  const handleRevoke = async (id: string) => {
    await revokeApiKey(id);
    await refresh();
    toast({ title: "API key revoked", variant: "destructive" });
  };

  return (
    <div className="space-y-6">
      {/* Create Key */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Plus className="h-5 w-5 text-primary" /> Create New API Key
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3">
            <Input
              placeholder="Key name (e.g. Production)"
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreateKey()}
              className="max-w-xs"
            />
            <Button onClick={handleCreateKey} disabled={!newKeyName.trim()}>
              Generate Key
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Keys List */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Your API Keys</CardTitle>
          <CardDescription>Manage your API keys for accessing SLM Studio endpoints</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {keys.map((k) => (
              <div key={k.id} className="flex items-center justify-between p-3 rounded-lg border border-border bg-muted/30">
                <div className="flex items-center gap-3 min-w-0">
                  <Key className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{k.name}</span>
                      <Badge variant={k.status === "active" ? "default" : "secondary"} className="text-[10px]">
                        {k.status}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <code className="text-xs text-muted-foreground font-mono">
                        {showKey[k.id] ? `${k.keyPrefix}-${k.keySuffix}xxxxxxxx` : `${k.keyPrefix}...${k.keySuffix}`}
                      </code>
                      <button onClick={() => setShowKey({ ...showKey, [k.id]: !showKey[k.id] })} className="text-muted-foreground hover:text-foreground">
                        {showKey[k.id] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                      </button>
                      <button onClick={() => handleCopy(`${k.keyPrefix}...${k.keySuffix}`)} className="text-muted-foreground hover:text-foreground">
                        <Copy className="h-3 w-3" />
                      </button>
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Created {new Date(k.createdAt).toLocaleDateString()} · {k.lastUsedAt ? `Last used ${new Date(k.lastUsedAt).toLocaleDateString()}` : "Never used"}
                    </p>
                  </div>
                </div>
                {k.status === "active" && (
                  <Button variant="ghost" size="sm" onClick={() => handleRevoke(k.id)} className="text-destructive hover:text-destructive">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Code Examples */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Code className="h-5 w-5 text-primary" /> Quick Start
          </CardTitle>
          <CardDescription>Use your API key with any OpenAI-compatible SDK</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-1 mb-3">
            {(["python", "curl", "javascript"] as const).map((lang) => (
              <Button key={lang} variant={codeLang === lang ? "default" : "outline"} size="sm" onClick={() => setCodeLang(lang)} className="text-xs capitalize">
                {lang}
              </Button>
            ))}
          </div>
          <div className="relative">
            <pre className="bg-muted rounded-lg p-4 text-xs font-mono overflow-x-auto border border-border">
              <code>{codeExamples[codeLang]}</code>
            </pre>
            <Button variant="ghost" size="icon" className="absolute top-2 right-2 h-7 w-7" onClick={() => handleCopy(codeExamples[codeLang])}>
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Notifications Tab ---
function NotificationsTab() {
  const [settings, setSettings] = useState({
    trainingComplete: true,
    trainingFailed: true,
    weeklyReport: false,
    creditLow: true,
    modelDeployed: true,
    emailDigest: false,
    slackIntegration: false,
  });

  const toggle = (key: keyof typeof settings) => setSettings({ ...settings, [key]: !settings[key] });

  const notifItems = [
    { key: "trainingComplete" as const, icon: Check, title: "Training Complete", desc: "Get notified when a training job finishes successfully" },
    { key: "trainingFailed" as const, icon: AlertTriangle, title: "Training Failed", desc: "Alert when a training job encounters an error" },
    { key: "creditLow" as const, icon: Zap, title: "Low Credits Warning", desc: "Notify when credits drop below 20%" },
    { key: "modelDeployed" as const, icon: Shield, title: "Model Deployed", desc: "Confirmation when a model is deployed to production" },
    { key: "weeklyReport" as const, icon: Clock, title: "Weekly Usage Report", desc: "Receive a summary of usage and costs every Monday" },
    { key: "emailDigest" as const, icon: Bell, title: "Email Digest", desc: "Daily digest of all notifications via email" },
  ];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Notification Preferences</CardTitle>
          <CardDescription>Choose what notifications you'd like to receive</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1">
          {notifItems.map((item, i) => (
            <div key={item.key}>
              <div className="flex items-center justify-between py-3">
                <div className="flex items-center gap-3">
                  <item.icon className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">{item.title}</p>
                    <p className="text-xs text-muted-foreground">{item.desc}</p>
                  </div>
                </div>
                <Switch checked={settings[item.key]} onCheckedChange={() => toggle(item.key)} />
              </div>
              {i < notifItems.length - 1 && <Separator />}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Integrations</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between py-2">
            <div className="flex items-center gap-3">
              <div className="h-8 w-8 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground">S</div>
              <div>
                <p className="text-sm font-medium">Slack</p>
                <p className="text-xs text-muted-foreground">Send notifications to a Slack channel</p>
              </div>
            </div>
            <Button variant="outline" size="sm">Connect</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Account Tab ---
function AccountTab() {
  const { toast } = useToast();
  const [profile, setProfile] = useState({
    name: "Somchai Dev",
    email: "somchai@example.com",
    org: "AI Labs Thailand",
    role: "Admin",
    timezone: "Asia/Bangkok",
  });

  const handleSave = () => {
    toast({ title: "Profile updated", description: "Your account settings have been saved." });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <User className="h-5 w-5 text-primary" /> Profile
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Full Name</Label>
              <Input value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Email</Label>
              <Input value={profile.email} onChange={(e) => setProfile({ ...profile, email: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Organization</Label>
              <Input value={profile.org} onChange={(e) => setProfile({ ...profile, org: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Timezone</Label>
              <Select value={profile.timezone} onValueChange={(v) => setProfile({ ...profile, timezone: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="Asia/Bangkok">Asia/Bangkok (GMT+7)</SelectItem>
                  <SelectItem value="Asia/Tokyo">Asia/Tokyo (GMT+9)</SelectItem>
                  <SelectItem value="US/Pacific">US/Pacific (GMT-8)</SelectItem>
                  <SelectItem value="Europe/London">Europe/London (GMT+0)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button onClick={handleSave}>Save Changes</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Shield className="h-5 w-5 text-primary" /> Security
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Password</p>
              <p className="text-xs text-muted-foreground">Last changed 30 days ago</p>
            </div>
            <Button variant="outline" size="sm">Change Password</Button>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Two-Factor Authentication</p>
              <p className="text-xs text-muted-foreground">Add an extra layer of security</p>
            </div>
            <Button variant="outline" size="sm">Enable 2FA</Button>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-destructive">Delete Account</p>
              <p className="text-xs text-muted-foreground">Permanently delete your account and all data</p>
            </div>
            <Button variant="destructive" size="sm">Delete</Button>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Onboarding Tour</p>
              <p className="text-xs text-muted-foreground">Restart the interactive platform walkthrough</p>
            </div>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={restartOnboarding}>
              <RotateCcw className="h-3.5 w-3.5" /> Restart Tour
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Webhooks Tab ---
interface WebhookConfig {
  id: string;
  url: string;
  events: string[];
  status: "active" | "paused";
  lastTriggered: string;
}

const webhookEvents = ["training.complete", "training.failed", "model.deployed", "credit.low"];

function WebhooksTab() {
  const [webhooks, setWebhooks] = useState<WebhookConfig[]>([
    { id: "1", url: "https://api.example.com/webhooks/slm", events: ["training.complete", "training.failed"], status: "active", lastTriggered: "2 hours ago" },
    { id: "2", url: "https://hooks.slack.com/services/T00/B00/xxx", events: ["training.complete", "model.deployed"], status: "active", lastTriggered: "1 day ago" },
  ]);
  const [newUrl, setNewUrl] = useState("");
  const [newEvents, setNewEvents] = useState<string[]>([]);
  const { toast } = useToast();

  const signingKey = "whsec_k8f3m2x9p1q7...a4b6";

  const handleAdd = () => {
    if (!newUrl.trim() || newEvents.length === 0) return;
    setWebhooks([...webhooks, {
      id: Date.now().toString(),
      url: newUrl,
      events: newEvents,
      status: "active",
      lastTriggered: "Never",
    }]);
    setNewUrl("");
    setNewEvents([]);
    toast({ title: "Webhook added" });
  };

  const toggleEvent = (ev: string) => {
    setNewEvents(newEvents.includes(ev) ? newEvents.filter((e) => e !== ev) : [...newEvents, ev]);
  };

  const handleTest = (wh: WebhookConfig) => {
    toast({ title: "Webhook tested", description: `POST sent to ${wh.url}` });
  };

  const handleToggle = (id: string) => {
    setWebhooks(webhooks.map((w) => w.id === id ? { ...w, status: w.status === "active" ? "paused" as const : "active" as const } : w));
  };

  const handleDelete = (id: string) => {
    setWebhooks(webhooks.filter((w) => w.id !== id));
    toast({ title: "Webhook deleted", variant: "destructive" });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Plus className="h-5 w-5 text-primary" /> Add Webhook
          </CardTitle>
          <CardDescription>Receive HTTP POST notifications for training events</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Endpoint URL</Label>
            <Input placeholder="https://your-api.com/webhooks" value={newUrl} onChange={(e) => setNewUrl(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Events</Label>
            <div className="flex flex-wrap gap-2">
              {webhookEvents.map((ev) => (
                <Button key={ev} variant={newEvents.includes(ev) ? "default" : "outline"} size="sm" onClick={() => toggleEvent(ev)} className="text-xs">
                  {ev}
                </Button>
              ))}
            </div>
          </div>
          <Button onClick={handleAdd} disabled={!newUrl.trim() || newEvents.length === 0}>Add Webhook</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Active Webhooks</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {webhooks.map((wh) => (
            <div key={wh.id} className="p-3 rounded-lg border border-border space-y-2">
              <div className="flex items-center justify-between">
                <code className="text-xs font-mono text-foreground truncate max-w-[60%]">{wh.url}</code>
                <div className="flex items-center gap-1">
                  <Badge variant={wh.status === "active" ? "default" : "secondary"} className="text-[10px]">{wh.status}</Badge>
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                {wh.events.map((ev) => (
                  <Badge key={ev} variant="outline" className="text-[10px]">{ev}</Badge>
                ))}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">Last triggered: {wh.lastTriggered}</span>
                <div className="flex gap-1">
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleTest(wh)} title="Test">
                    <Send className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleToggle(wh.id)} title={wh.status === "active" ? "Pause" : "Resume"}>
                    {wh.status === "active" ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => handleDelete(wh.id)} title="Delete">
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
          {webhooks.length === 0 && <p className="text-sm text-muted-foreground text-center py-4">No webhooks configured</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Shield className="h-5 w-5 text-primary" /> Signing Secret
          </CardTitle>
          <CardDescription>Use this secret to verify webhook payloads</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <code className="text-xs font-mono bg-muted p-2 rounded flex-1">{signingKey}</code>
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => { navigator.clipboard.writeText(signingKey); toast({ title: "Copied" }); }}>
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Main Settings Page ---
export default function Settings() {
  const { t } = useLanguage();

  return (
    <PageTransition>
      <div className="space-y-6">
        <FadeIn>
          <div>
            <h1 className="text-2xl font-bold">{t("settings.title")}</h1>
            <p className="text-muted-foreground text-sm">{t("settings.subtitle")}</p>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <Tabs defaultValue="api-keys" className="space-y-6">
            <TabsList className="flex w-full max-w-3xl overflow-x-auto">
              <TabsTrigger value="api-keys" className="text-xs">
                <Key className="h-3.5 w-3.5 mr-1.5" /> {t("settings.apiKeys")}
              </TabsTrigger>
              <TabsTrigger value="notifications" className="text-xs">
                <Bell className="h-3.5 w-3.5 mr-1.5" /> {t("settings.notifications")}
              </TabsTrigger>
              <TabsTrigger value="webhooks" className="text-xs">
                <Webhook className="h-3.5 w-3.5 mr-1.5" /> {t("settings.webhooks")}
              </TabsTrigger>
              <TabsTrigger value="account" className="text-xs">
                <User className="h-3.5 w-3.5 mr-1.5" /> {t("settings.account")}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="api-keys"><ApiKeysTab /></TabsContent>
            <TabsContent value="notifications"><NotificationsTab /></TabsContent>
            <TabsContent value="webhooks"><WebhooksTab /></TabsContent>
            <TabsContent value="account"><AccountTab /></TabsContent>
          </Tabs>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
