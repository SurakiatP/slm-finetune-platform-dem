import { useState } from "react";
import { Download, FileText, Loader2, Package } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useModelDownloadUrl } from "@/hooks/queries";
import type { ArtifactFormat, ModelArtifact, ModelDownloadFile } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";

type DownloadKey = "gguf" | "lora" | "readme";

/** Pick which file in a mint response answers a given menu item. `lora` and
 *  `readme` both mint against the `lora` format (a multi-file directory
 *  containing `adapter_config.json` / `adapter_model.safetensors` /
 *  `README.md` — verified against the backend's own test fixtures) and just
 *  pick a different file out of the same listing. */
function pickFile(files: ModelDownloadFile[], key: DownloadKey): ModelDownloadFile | undefined {
  if (files.length === 0) return undefined;
  if (key === "readme") {
    return files.find((f) => /readme/i.test(f.name)) ?? files[0];
  }
  if (key === "lora") {
    return (
      files.find((f) => !/readme/i.test(f.name) && /adapter_model/i.test(f.name)) ??
      files.find((f) => !/readme/i.test(f.name)) ??
      files[0]
    );
  }
  return files[0];
}

/** Mints a presigned MinIO URL on click and opens it in a new tab — GGUF,
 *  LoRA adapter weights, and the adapter's README, all via
 *  `useModelDownloadUrl` (one-shot mutation, never cached). */
export function DownloadMenu({ model }: { model: ModelArtifact }) {
  const { t } = useLanguage();
  const [pendingKey, setPendingKey] = useState<DownloadKey | null>(null);
  const [mintError, setMintError] = useState<string | null>(null);
  const mutation = useModelDownloadUrl();

  const hasGguf = Boolean(model.gguf_uri);
  const hasLora = Boolean(model.lora_adapter_uri);
  const anyAvailable = hasGguf || hasLora;

  const handleDownload = (key: DownloadKey, format: ArtifactFormat) => {
    setPendingKey(key);
    setMintError(null);
    mutation.mutate(
      { id: model.id, format },
      {
        onSuccess: (data) => {
          const file = pickFile(data.files, key);
          if (file) window.open(file.url, "_blank", "noopener,noreferrer");
        },
        onError: (err) => setMintError(err instanceof Error ? err.message : "Download failed"),
        onSettled: () => setPendingKey(null),
      },
    );
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2" disabled={!anyAvailable}>
          <Download className="h-3.5 w-3.5" /> {t("model.download")}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          disabled={!hasGguf || pendingKey !== null}
          onSelect={() => handleDownload("gguf", "gguf")}
          className="gap-2"
        >
          {pendingKey === "gguf" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Package className="h-3.5 w-3.5" />
          )}
          {t("model.downloadGguf")}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!hasLora || pendingKey !== null}
          onSelect={() => handleDownload("lora", "lora")}
          className="gap-2"
        >
          {pendingKey === "lora" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Package className="h-3.5 w-3.5" />
          )}
          {t("model.downloadLora")}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!hasLora || pendingKey !== null}
          onSelect={() => handleDownload("readme", "lora")}
          className="gap-2"
        >
          {pendingKey === "readme" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
          {t("model.downloadReadme")}
        </DropdownMenuItem>
      </DropdownMenuContent>
      {mintError && <p role="alert" className="mt-1 text-xs text-destructive">{mintError}</p>}
    </DropdownMenu>
  );
}
