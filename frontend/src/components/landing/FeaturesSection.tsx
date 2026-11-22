import { motion } from "framer-motion";
import { MessageSquareText, BarChart3, Layers, Database, Cpu, Shield } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

const featureKeys = [
  { icon: MessageSquareText, titleKey: "features.promptTitle", descKey: "features.promptDesc" },
  { icon: Database, titleKey: "features.dataTitle", descKey: "features.dataDesc" },
  { icon: Cpu, titleKey: "features.modelTitle", descKey: "features.modelDesc" },
  { icon: BarChart3, titleKey: "features.evalTitle", descKey: "features.evalDesc" },
  { icon: Layers, titleKey: "features.loraTitle", descKey: "features.loraDesc" },
  { icon: Shield, titleKey: "features.exportTitle", descKey: "features.exportDesc" },
];

const FeaturesSection = () => {
  const { t } = useLanguage();

  return (
    <section id="features" className="py-24 bg-secondary/30">
      <div className="container">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <p className="text-sm font-medium text-primary mb-2">{t("features.label")}</p>
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t("features.title")}</h2>
          <p className="mt-4 text-muted-foreground max-w-lg mx-auto">
            {t("features.subtitle")}
          </p>
        </motion.div>

        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3 max-w-5xl mx-auto">
          {featureKeys.map((feature, i) => (
            <motion.div
              key={feature.titleKey}
              initial={{ opacity: 0, y: 24, scale: 0.95 }}
              whileInView={{ opacity: 1, y: 0, scale: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.08 }}
              className="rounded-xl border bg-card p-6 hover:shadow-md transition-shadow"
            >
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-accent">
                <feature.icon className="h-5 w-5 text-accent-foreground" />
              </div>
              <h3 className="mb-2 font-semibold">{t(feature.titleKey)}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{t(feature.descKey)}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default FeaturesSection;
