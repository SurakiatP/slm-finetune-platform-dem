import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

interface CopyButtonProps {
  value: string;
  label?: string;
  className?: string;
}

/** Small copy-to-clipboard icon button with a toast confirmation, for
 *  copying job IDs, artifact URIs, API keys, etc. */
export function CopyButton({ value, label = "Copy", className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const { toast } = useToast();

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={copied ? "Copied" : label}
      className={cn("h-7 w-7 text-muted-foreground hover:text-foreground", className)}
      onClick={() => {
        void navigator.clipboard
          .writeText(value)
          .then(() => {
            setCopied(true);
            toast({ description: "Copied to clipboard" });
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => {
            toast({ variant: "destructive", description: "Copy failed" });
          });
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
    </Button>
  );
}
