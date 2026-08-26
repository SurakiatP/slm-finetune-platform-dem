import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Send, Bot, User, Loader2 } from "lucide-react";
import { chatCompletions } from "@/api/endpoints/inference";
import type { ChatMessage } from "@/api/types";

interface DisplayMessage extends ChatMessage {
  latencyMs?: number;
  tokens?: number;
}

export function ChatPanel({
  modelId,
  displayName,
  className = "",
}: {
  modelId: string;
  displayName: string;
  className?: string;
}) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isLoading) return;

    const userMsg: DisplayMessage = { role: "user", content: text };
    const history = [...messages, userMsg];
    setMessages(history);
    setInput("");
    setIsLoading(true);
    setError(null);

    const start = Date.now();
    try {
      // Backend rejects stream=true — collect the full completion at once.
      const response = await chatCompletions({
        model: modelId,
        messages: history.map(({ role, content }) => ({ role, content })),
        temperature: 0.7,
        max_tokens: 512,
        stream: false,
      });
      const content = response.choices[0]?.message?.content ?? "";
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content,
          latencyMs: Date.now() - start,
          tokens: response.usage?.completion_tokens,
        },
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Inference request failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className={`flex flex-col border border-border rounded-lg overflow-hidden bg-background ${className}`}>
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">{displayName}</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs h-7 text-muted-foreground"
          onClick={() => { setMessages([]); setError(null); }}
        >
          Clear
        </Button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[350px] max-h-[500px]">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <Bot className="h-10 w-10 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground">Send a message to test the model</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "assistant" && (
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
                <Bot className="h-3.5 w-3.5 text-primary" />
              </div>
            )}
            <div className="max-w-[85%] space-y-1">
              <div
                className={`px-3.5 py-2.5 rounded-xl text-sm whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground rounded-br-sm"
                    : "bg-secondary/50 border border-border rounded-bl-sm text-foreground"
                }`}
              >
                {msg.content}
              </div>
              {msg.role === "assistant" && msg.latencyMs && (
                <div className="flex gap-3 text-[10px] text-muted-foreground px-1">
                  <span>{msg.latencyMs}ms</span>
                  <span>{msg.tokens} tokens</span>
                </div>
              )}
            </div>
            {msg.role === "user" && (
              <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center shrink-0 mt-0.5">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            )}
          </div>
        ))}
        {isLoading && (
          <div className="flex gap-2.5">
            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
              <Loader2 className="h-3.5 w-3.5 text-primary animate-spin" />
            </div>
            <div className="px-3.5 py-2.5 rounded-xl bg-secondary/50 border border-border rounded-bl-sm">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
        {error && (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border p-3 flex gap-2">
        <Input
          placeholder="Type a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && void handleSend()}
          className="border-0 shadow-none focus-visible:ring-0 bg-transparent"
          disabled={isLoading}
        />
        <Button aria-label="Send message" size="icon" onClick={() => void handleSend()} disabled={!input.trim() || isLoading} className="shrink-0">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
