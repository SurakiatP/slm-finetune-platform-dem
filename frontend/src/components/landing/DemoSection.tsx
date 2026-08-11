import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { motion } from "framer-motion";
import { Play, Loader2, CheckCircle2 } from "lucide-react";

const examplePrompts = [
  "Classify customer reviews as positive, negative, or neutral with confidence scores",
  "Extract person names, organizations, and dates from news articles",
  "Answer questions about product specifications from technical manuals",
];

const DemoSection = () => {
  const [prompt, setPrompt] = useState("");
  const [stage, setStage] = useState<"idle" | "running" | "done">("idle");
  const [currentStep, setCurrentStep] = useState(0);

  const steps = [
    "Analyzing task description...",
    "Task type identified: Classification (3 classes)",
    "Selecting base model: Qwen2.5-1.5B",
    "Generating 3,000 synthetic examples via GPT-4o...",
    "Data curation — filtering low-quality samples...",
    "Fine-tuning with LoRA (rank=16, epochs=3)...",
    "Evaluation complete — 92.7% accuracy, 4.1ms latency",
  ];

  const runDemo = () => {
    if (!prompt.trim()) return;
    setStage("running");
    setCurrentStep(0);

    steps.forEach((_, i) => {
      setTimeout(() => {
        setCurrentStep(i + 1);
        if (i === steps.length - 1) setStage("done");
      }, (i + 1) * 800);
    });
  };

  const reset = () => {
    setStage("idle");
    setCurrentStep(0);
  };

  return (
    <section id="demo" className="py-24 bg-secondary/30">
      <div className="container max-w-3xl">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="text-center mb-12"
        >
          <p className="text-sm font-medium text-primary mb-2">Interactive Demo</p>
          <h2 className="text-3xl font-bold tracking-tight">Try it yourself</h2>
          <p className="mt-3 text-muted-foreground">
            Describe a task and see how TuneLab processes it.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.15 }}
          className="rounded-xl border bg-card p-6 shadow-sm"
        >
          <Textarea
            value={prompt}
            onChange={(e) => { setPrompt(e.target.value); if (stage === "done") reset(); }}
            placeholder="Describe your fine-tuning task..."
            className="min-h-[80px] resize-none font-mono text-sm mb-4"
          />

          <div className="flex flex-wrap gap-2 mb-4">
            {examplePrompts.map((ep) => (
              <button
                key={ep}
                onClick={() => { setPrompt(ep); reset(); }}
                className="text-xs border rounded-full px-3 py-1 text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
              >
                {ep.slice(0, 50)}...
              </button>
            ))}
          </div>

          <Button onClick={runDemo} disabled={!prompt.trim() || stage === "running"} className="mb-6">
            {stage === "running" ? (
              <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Processing...</>
            ) : (
              <><Play className="mr-2 h-4 w-4" /> Run Demo</>
            )}
          </Button>

          {currentStep > 0 && (
            <div className="space-y-2 font-mono text-sm">
              {steps.slice(0, currentStep).map((step, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex items-start gap-2"
                >
                  <CheckCircle2 className="h-4 w-4 mt-0.5 text-success shrink-0" />
                  <span className={i === currentStep - 1 && stage !== "done" ? "text-foreground" : "text-muted-foreground"}>
                    {step}
                  </span>
                </motion.div>
              ))}
            </div>
          )}
        </motion.div>
      </div>
    </section>
  );
};

export default DemoSection;
