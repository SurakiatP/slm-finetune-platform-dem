// Placeholder — replaced by W2-T4 with the real training create form.
// Contract (fixed by the wave plan): default export with these exact props.
interface TrainingCreateDialogProps {
  projectId: string;
  datasetId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onStarted?: (trainingId: string) => void;
}

export default function TrainingCreateDialog(_props: TrainingCreateDialogProps) {
  return null;
}
