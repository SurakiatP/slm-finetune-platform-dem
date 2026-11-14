import { motion } from "framer-motion";
import { useLanguage } from "@/i18n/LanguageContext";
import { Tag, ScanSearch, HelpCircle, Code2, FileText, ArrowUpDown } from "lucide-react";

// Text lives in translations under `useCases.<id>.{title,desc,example}`.
const useCases = [
  { id: "classification", icon: Tag },
  { id: "ner", icon: ScanSearch },
  { id: "qa", icon: HelpCircle },
  { id: "functionCalling", icon: Code2 },
  { id: "extraction", icon: FileText },
  { id: "ranking", icon: ArrowUpDown },
];

const UseCasesSection = () => {
  const { t } = useLanguage();
  return (
    <section id="use-cases" className="py-24">
      <div className="container">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <p className="text-sm font-medium text-primary mb-2">{t("useCases.label")}</p>
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t("useCases.title")}</h2>
          <p className="mt-4 text-muted-foreground max-w-lg mx-auto">
            {t("useCases.subtitle")}
          </p>
        </motion.div>

        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3 max-w-5xl mx-auto">
          {useCases.map((uc, i) => (
            <motion.div
              key={uc.id}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.08 }}
              className="group rounded-xl border bg-card p-6 hover:border-primary/30 transition-colors"
            >
              <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-accent">
                <uc.icon className="h-4 w-4 text-accent-foreground" />
              </div>
              <h3 className="mb-1 font-semibold">{t(`useCases.${uc.id}.title`)}</h3>
              <p className="text-sm text-muted-foreground mb-3">{t(`useCases.${uc.id}.desc`)}</p>
              <p className="text-xs font-mono text-primary/80 bg-accent/50 rounded-md px-3 py-2">
                {t(`useCases.${uc.id}.example`)}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default UseCasesSection;
