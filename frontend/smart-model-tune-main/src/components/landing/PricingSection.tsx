import { Button } from "@/components/ui/button";
import { Check } from "lucide-react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { useLanguage } from "@/i18n/LanguageContext";

const plans = [
  {
    name: "Free",
    price: "$0",
    period: "/month",
    description: "Try fine-tuning with limited resources",
    features: ["1 project", "2 training jobs/month", "Qwen2.5-0.5B only", "Community support", "Basic evaluation"],
    cta: "Get Started",
    highlighted: false,
  },
  {
    name: "Pro",
    price: "$49",
    period: "/month",
    description: "For teams shipping production NLP",
    features: ["Unlimited projects", "20 training jobs/month", "All base models", "Priority support", "Full evaluation suite", "API access", "ONNX/GGUF export"],
    cta: "Start Free Trial",
    highlighted: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "",
    description: "Dedicated infrastructure & support",
    features: ["Unlimited everything", "Dedicated GPU cluster", "Custom base models", "SSO & team management", "SLA guarantee", "On-premise deployment", "Dedicated account manager"],
    cta: "Contact Sales",
    highlighted: false,
  },
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
              key={plan.name}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.12 }}
              whileHover={plan.highlighted ? { scale: 1.03 } : undefined}
              className={`rounded-xl border p-6 flex flex-col ${
                plan.highlighted
                  ? "border-primary shadow-lg ring-1 ring-primary/20 relative"
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
              <h3 className="font-semibold text-lg">{plan.name}</h3>
              <div className="mt-2 mb-1">
                <span className="text-3xl font-bold">{plan.price}</span>
                <span className="text-muted-foreground text-sm">{plan.period}</span>
              </div>
              <p className="text-sm text-muted-foreground mb-6">{plan.description}</p>

              <ul className="space-y-2.5 mb-8 flex-1">
                {plan.features.map((f) => (
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
                <Link to="/signup">{plan.cta}</Link>
              </Button>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default PricingSection;
