import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Send, Bot, User, Loader2 } from "lucide-react";

interface Message {
  role: "user" | "assistant";
  content: string;
  latencyMs?: number;
  tokens?: number;
}

const mockResponses: Record<string, string[]> = {
  default: [
    "Based on the input, I've classified this as **Technical Support** with 94.2% confidence.\n\nThe key signals are:\n- Mentions of \"not working\" → technical issue\n- Reference to \"internet\" → connectivity category\n\nWould you like me to elaborate on the classification logic?",
    "I've extracted the following entities:\n\n| Entity | Type | Confidence |\n|--------|------|------------|\n| สมชาย | PERSON | 96.1% |\n| กรุงเทพ | LOCATION | 98.3% |\n| ธนาคารกสิกร | ORGANIZATION | 91.7% |\n\nProcessed in 42ms with 3 entities detected.",
    "Here's my analysis of the query:\n\n**Answer:** The return policy allows customers to return products within 30 days of purchase for a full refund, provided the item is in its original packaging.\n\n**Source:** FAQ Section 4.2 — Return & Refund Policy\n**Confidence:** 89.5%",
  ],
};

function getResponse(index: number): string {
  const responses = mockResponses.default;
  return responses[index % responses.length];
}

export function ChatPanel({
  modelName,
  className = "",
}: {
  modelName: string;
  className?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const responseIndex = useRef(0);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || isLoading) return;

    const userMsg: Message = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsLoading(true);

    // Simulate response with delay
    const delay = 400 + Math.random() * 800;
    setTimeout(() => {
      const response = getResponse(responseIndex.current);
      responseIndex.current++;
      const assistantMsg: Message = {
        role: "assistant",
        content: response,
        latencyMs: Math.round(delay),
        tokens: Math.round(response.length / 4),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setIsLoading(false);
    }, delay);
  };

  return (
    <div className={`flex flex-col border border-border rounded-lg overflow-hidden bg-background ${className}`}>
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">{modelName}</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs h-7 text-muted-foreground"
          onClick={() => { setMessages([]); responseIndex.current = 0; }}
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
      </div>

      {/* Input */}
      <div className="border-t border-border p-3 flex gap-2">
        <Input
          placeholder="Type a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
          className="border-0 shadow-none focus-visible:ring-0 bg-transparent"
          disabled={isLoading}
        />
        <Button size="icon" onClick={handleSend} disabled={!input.trim() || isLoading} className="shrink-0">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
