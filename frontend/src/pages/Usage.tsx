import { FadeIn, PageTransition } from "@/components/motion";
import { UsageSummaryPanel } from "@/components/usage/UsageSummaryPanel";
import { useLanguage } from "@/i18n/LanguageContext";

/** Cross-project usage & cost page — OpenRouter token usage and cost for
 *  the current UTC calendar month, rolled up by (model, stage). */
export default function Usage() {
  const { t } = useLanguage();

  return (
    <PageTransition>
      <div className="max-w-7xl space-y-6">
        <FadeIn>
          <div>
            <h1 className="text-2xl font-bold text-foreground">{t("usage.title")}</h1>
            <p className="text-sm text-muted-foreground">
              OpenRouter token usage and cost for the current UTC calendar month.
            </p>
          </div>
        </FadeIn>

        <UsageSummaryPanel />
      </div>
    </PageTransition>
  );
}
