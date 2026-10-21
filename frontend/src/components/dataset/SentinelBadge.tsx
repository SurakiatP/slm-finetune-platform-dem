import { Badge } from "@/components/ui/badge";
import { useLanguage } from "@/i18n/LanguageContext";

/** Marks the sentinel class the SDG adds to every dataset (`unknown` label /
 *  `no_tool_needed` tool) so users read it as expected, not as a stray label. */
export function SentinelBadge() {
  const { t } = useLanguage();
  return (
    <Badge variant="outline" className="ml-1 px-1.5 py-0 text-[10px] font-normal" title={t("sentinel.hint")}>
      {t("sentinel.badge")}
    </Badge>
  );
}
