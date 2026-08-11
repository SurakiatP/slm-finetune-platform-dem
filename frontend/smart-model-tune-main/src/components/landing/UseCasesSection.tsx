import { motion } from "framer-motion";
import { Tag, ScanSearch, HelpCircle, Code2, FileText, ArrowUpDown } from "lucide-react";

const useCases = [
  {
    icon: Tag,
    title: "Text Classification",
    description: "Sentiment analysis, spam detection, topic categorization, intent classification.",
    example: '"Classify support tickets into: billing, technical, general"',
  },
  {
    icon: ScanSearch,
    title: "Named Entity Recognition",
    description: "Extract people, organizations, locations, dates, and custom entities.",
    example: '"Extract product names and prices from e-commerce descriptions"',
  },
  {
    icon: HelpCircle,
    title: "Question Answering",
    description: "Domain-specific QA, FAQ bots, document comprehension.",
    example: '"Answer medical questions based on clinical guidelines"',
  },
  {
    icon: Code2,
    title: "Function Calling",
    description: "Tool use, API routing, structured output generation.",
    example: '"Route user requests to the correct API endpoint with params"',
  },
  {
    icon: FileText,
    title: "Information Extraction",
    description: "Invoice parsing, resume extraction, contract analysis.",
    example: '"Extract key terms from legal contracts in JSON format"',
  },
  {
    icon: ArrowUpDown,
    title: "Ranking & Scoring",
    description: "Relevance ranking, quality scoring, priority classification.",
    example: '"Rank search results by relevance to the user query"',
  },
];

const UseCasesSection = () => {
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
          <p className="text-sm font-medium text-primary mb-2">Use Cases</p>
          <h2 className="text-3xl font-bold tracking-tight md:text-4xl">Built for real-world NLP tasks</h2>
          <p className="mt-4 text-muted-foreground max-w-lg mx-auto">
            Fine-tune compact models for production workloads at a fraction of the cost.
          </p>
        </motion.div>

        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3 max-w-5xl mx-auto">
          {useCases.map((uc, i) => (
            <motion.div
              key={uc.title}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: i * 0.08 }}
              className="group rounded-xl border bg-card p-6 hover:border-primary/30 transition-colors"
            >
              <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-accent">
                <uc.icon className="h-4 w-4 text-accent-foreground" />
              </div>
              <h3 className="mb-1 font-semibold">{uc.title}</h3>
              <p className="text-sm text-muted-foreground mb-3">{uc.description}</p>
              <p className="text-xs font-mono text-primary/80 bg-accent/50 rounded-md px-3 py-2">
                {uc.example}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default UseCasesSection;
