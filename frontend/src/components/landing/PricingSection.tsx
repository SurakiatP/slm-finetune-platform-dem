import { Button } from "@/components/ui/button";
import { Check } from "lucide-react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { useLanguage } from "@/i18n/LanguageContext";

// Text lives in translations under `pricing.<id>.*`; features are `pricing.<id>.f1..fN`.
const featureKeys = (id: string, count: number) => Array.from({ length: count }, (_, n) => `${id}.f${n + 1}`);
const plans = [
  { id: "free", price: "$0", monthly: true, features: featureKeys("free", 5), highlighted: false },
  { id: "pro", price: "$49", monthly: true, features: featureKeys("pro", 7), highlighted: true },
  { id: "enterprise", price: null, monthly: false, features: featureKeys("enterprise", 7), highlighted: false },
];

const PricingSection = () => {
  const { t } = useLanguage();

  return (
    <section id="pricing" className="py-24">
      <div className="container">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-16"
        >
          <p className="text-sm font-medium text-primary mb-2">{t("pricing.label")}</p>
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">{t("pricing.title")}</h2>
          <p className="mt-4 text-muted-foreground">{t("pricing.subtitle")}</p>
        </motion.div>

        <div className="grid gap-6 md:grid-cols-3 max-w-4xl mx-auto">
          {plans.map((plan, i) => (
            <motion.div
              key={plan.id}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.12 }}
              whileHover={plan.highlighted ? { scale: 1.03 } : undefined}
              className={`rounded-xl border p-6 flex flex-col ${
                plan.highlighted
                  ? "border-primary shadow-card ring-1 ring-primary/20 relative"
                  : "bg-card"
              }`}
            >
              {plan.highlighted && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.8 }}
                  whileInView={{ opacity: 1, scale: 1 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.3, delay: 0.4 }}
                  className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-3 py-0.5 text-xs font-medium text-primary-foreground"
                >
                  {t("pricing.mostPopular")}
                </motion.div>
              )}
              <h3 className="font-semibold text-lg">{t(`pricing.${plan.id}.name`)}</h3>
              <div className="mt-2 mb-1">
                <span className="text-3xl font-bold">{plan.price ?? t("pricing.custom")}</span>
                {plan.monthly && <span className="text-muted-foreground text-sm">{t("pricing.perMonth")}</span>}
              </div>
              <p className="text-sm text-muted-foreground mb-6">{t(`pricing.${plan.id}.desc`)}</p>

              <ul className="space-y-2.5 mb-8 flex-1">
                {plan.features.map((key) => t(`pricing.${key}`)).map((f) => (
                  <li key={f} className="flex items-center gap-2 text-sm">
                    <Check className="h-4 w-4 text-success shrink-0" />
                    {f}
                  </li>
                ))}
              </ul>

              <Button
                variant={plan.highlighted ? "default" : "outline"}
                className="w-full"
                asChild
              >
                <Link to="/signup">{t(`pricing.${plan.id}.cta`)}</Link>
              </Button>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default PricingSection;
