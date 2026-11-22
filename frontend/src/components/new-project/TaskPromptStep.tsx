import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Send, Bot, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ProjectFormData } from "@/pages/NewProject";
import { useLanguage } from "@/i18n/LanguageContext";

const suggestionKeys = [
  "newProject.prompt.suggestion1",
  "newProject.prompt.suggestion2",
  "newProject.prompt.suggestion3",
  "newProject.prompt.suggestion4",
];

/** User messages keep their text; assistant replies keep a translation key so
 *  they follow the UI language. */
interface Message {
  role: "user" | "assistant";
  content: string;
}

const assistantResponseKeys: Record<number, string> = {
  0: "newProject.prompt.reply1",
  1: "newProject.prompt.reply2",
};
const fallbackResponseKey = "newProject.prompt.replyMore";

export function TaskPromptStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  const { t } = useLanguage();
  const [messages, setMessages] = useState<Message[]>(
    formData.taskPrompt
      ? [
          { role: "user", content: formData.taskPrompt },
          { role: "assistant", content: assistantResponseKeys[0] },
        ]
      : []
  );
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = (text?: string) => {
    const msg = text || input.trim();
    if (!msg) return;

    const userMsg: Message = { role: "user", content: msg };
    const responseIndex = messages.filter((m) => m.role === "user").length;
    const assistantMsg: Message = {
      role: "assistant",
      content: assistantResponseKeys[responseIndex] ?? fallbackResponseKey,
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");
    updateForm({ taskPrompt: msg });
  };

  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="project-name" className="text-sm font-semibold">{t("newProject.prompt.projectName")}</Label>
        <Input
          id="project-name"
          placeholder={t("newProject.prompt.projectNamePlaceholder")}
          value={formData.projectName}
          onChange={(e) => updateForm({ projectName: e.target.value })}
          className="mt-1.5"
        />
      </div>

      <div>
        <Label className="text-sm font-semibold">{t("newProject.prompt.describeTitle")}</Label>
        <p className="text-xs text-muted-foreground mt-0.5 mb-3">
          {t("newProject.prompt.describeHint")}
        </p>

        {/* Chat area */}
        <div className="border border-border rounded-lg overflow-hidden bg-secondary/30">
          <div ref={scrollRef} className="h-[280px] overflow-y-auto p-4 space-y-3">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-4">
                <Bot className="h-10 w-10 text-muted-foreground/50" />
                <div>
                  <p className="text-sm font-medium text-foreground">{t("newProject.prompt.emptyTitle")}</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("newProject.prompt.emptyHint")}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                  {suggestionKeys.map((key) => t(key)).map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => handleSend(s)}
                      className="text-[11px] px-3 py-1.5 rounded-full border border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors text-left"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role === "assistant" && (
                  <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                    <Bot className="h-3.5 w-3.5 text-primary" />
                  </div>
                )}
                <div
                  className={`max-w-[75%] px-3.5 py-2 rounded-xl text-sm ${
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground rounded-br-sm"
                      : "bg-background border border-border rounded-bl-sm text-foreground"
                  }`}
                >
                  {msg.role === "assistant" ? t(msg.content) : msg.content}
                </div>
                {msg.role === "user" && (
                  <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center shrink-0">
                    <User className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Input bar */}
          <div className="border-t border-border p-3 bg-background flex gap-2">
            <Input
              placeholder={t("newProject.prompt.inputPlaceholder")}
              aria-label={t("newProject.prompt.inputPlaceholder")}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && handleSend()}
              className="border-0 shadow-none focus-visible:ring-0 bg-transparent"
            />
            <Button size="icon" aria-label={t("playground.send")} onClick={() => handleSend()} disabled={!input.trim()} className="shrink-0">
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
