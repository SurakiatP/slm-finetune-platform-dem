import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Send, Bot, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ProjectFormData } from "@/pages/NewProject";

const suggestions = [
  "Classify customer support tickets into billing, technical, general, and feedback categories",
  "Extract product names, prices, and quantities from invoices",
  "Answer questions about our product documentation",
  "Route user requests to the correct API endpoint with parameters",
];

interface Message {
  role: "user" | "assistant";
  content: string;
}

const assistantResponses: Record<number, string> = {
  0: "Great! I understand your task. Let me help you set this up. Could you provide more details about the expected input/output format? Or you can proceed to the next step to select the task type.",
  1: "Thanks for the additional details. This will help me configure the training parameters optimally. Feel free to continue refining or move to the next step.",
};

export function TaskPromptStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  const [messages, setMessages] = useState<Message[]>(
    formData.taskPrompt
      ? [
          { role: "user", content: formData.taskPrompt },
          { role: "assistant", content: assistantResponses[0] },
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
      content: assistantResponses[responseIndex] || "Got it! Your task description has been updated. You can proceed to the next step or continue refining.",
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");
    updateForm({ taskPrompt: msg });
  };

  return (
    <div className="space-y-4">
      <div>
        <Label className="text-sm font-semibold">Project Name</Label>
        <Input
          placeholder="e.g. Customer Intent Classifier"
          value={formData.projectName}
          onChange={(e) => updateForm({ projectName: e.target.value })}
          className="mt-1.5"
        />
      </div>

      <div>
        <Label className="text-sm font-semibold">Describe Your Task</Label>
        <p className="text-xs text-muted-foreground mt-0.5 mb-3">
          Tell us what you want your fine-tuned model to do. Be as specific as possible.
        </p>

        {/* Chat area */}
        <div className="border border-border rounded-lg overflow-hidden bg-secondary/30">
          <div ref={scrollRef} className="h-[280px] overflow-y-auto p-4 space-y-3">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-4">
                <Bot className="h-10 w-10 text-muted-foreground/50" />
                <div>
                  <p className="text-sm font-medium text-foreground">Describe your fine-tuning task</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Try one of these examples or write your own:
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                  {suggestions.map((s) => (
                    <button
                      key={s}
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
                  {msg.content}
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
              placeholder="Describe what your model should do..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              className="border-0 shadow-none focus-visible:ring-0 bg-transparent"
            />
            <Button size="icon" onClick={() => handleSend()} disabled={!input.trim()} className="shrink-0">
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
