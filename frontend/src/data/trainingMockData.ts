

export interface PipelineStep {
  id: string;
  label: string;
  description: string;
  status: "completed" | "active" | "pending" | "failed";
  duration?: string;
}

export interface TrainingLogEntry {
  timestamp: string;
  level: "info" | "warning" | "error" | "success";
  message: string;
}

export interface LossCurvePoint {
  step: number;
  trainLoss: number;
  valLoss: number;
}

export interface ComparisonResult {
  id: string;
  input: string;
  studentOutput: string;
  teacherOutput: string;
  studentLatencyMs: number;
  teacherLatencyMs: number;
  studentScore: number;
  teacherScore: number;
}

export const mockPipelineSteps: PipelineStep[] = [
  { id: "scope", label: "Task Scoping", description: "Analyzing task requirements and defining scope", status: "completed", duration: "2m 15s" },
  { id: "synth", label: "Synthetic Data Generation", description: "Generating training data using Teacher LLM", status: "completed", duration: "18m 42s" },
  { id: "curate", label: "Data Curation", description: "Filtering and validating generated data quality", status: "completed", duration: "5m 30s" },
  { id: "train", label: "Fine-Tuning", description: "Training Student SLM with LoRA adapters", status: "active", duration: "Running..." },
  { id: "eval", label: "Evaluation", description: "Comparing Student SLM vs Teacher LLM performance", status: "pending" },
];

export const mockTrainingLog: TrainingLogEntry[] = [
  { timestamp: "14:22:01", level: "info", message: "Starting training pipeline for project 'Thai NER Extractor'" },
  { timestamp: "14:22:03", level: "info", message: "Task type: Named Entity Recognition | Base model: Gemma 2 2B" },
  { timestamp: "14:22:05", level: "info", message: "Step 1/5: Task Scoping — analyzing task requirements..." },
  { timestamp: "14:24:20", level: "success", message: "Task Scoping completed. Identified 4 entity types: PERSON, ORG, LOC, DATE" },
  { timestamp: "14:24:22", level: "info", message: "Step 2/5: Synthetic Data Generation — calling Teacher LLM (GPT-4o)..." },
  { timestamp: "14:28:10", level: "info", message: "Generated 1,200 / 5,200 samples (23%)..." },
  { timestamp: "14:35:44", level: "info", message: "Generated 3,600 / 5,200 samples (69%)..." },
  { timestamp: "14:43:04", level: "success", message: "Synthetic Data Generation completed. 5,200 samples generated." },
  { timestamp: "14:43:06", level: "info", message: "Step 3/5: Data Curation — filtering low-quality samples..." },
  { timestamp: "14:45:12", level: "warning", message: "Removed 340 samples with inconsistent entity spans" },
  { timestamp: "14:48:36", level: "success", message: "Data Curation completed. 4,860 clean samples retained (93.5%)" },
  { timestamp: "14:48:38", level: "info", message: "Step 4/5: Fine-Tuning — initializing LoRA adapters (rank=16, alpha=32)..." },
  { timestamp: "14:48:50", level: "info", message: "Trainable parameters: 4.2M / 2.0B total (0.21%)" },
  { timestamp: "14:49:00", level: "info", message: "Epoch 1/8 started — batch_size=16, lr=1e-4" },
  { timestamp: "14:58:22", level: "info", message: "Epoch 1/8 completed — train_loss: 1.842, val_loss: 1.756" },
  { timestamp: "15:07:45", level: "info", message: "Epoch 2/8 completed — train_loss: 1.245, val_loss: 1.198" },
  { timestamp: "15:17:08", level: "info", message: "Epoch 3/8 completed — train_loss: 0.876, val_loss: 0.852" },
  { timestamp: "15:26:31", level: "info", message: "Epoch 4/8 completed — train_loss: 0.612, val_loss: 0.598" },
  { timestamp: "15:35:54", level: "info", message: "Epoch 5/8 completed — train_loss: 0.485, val_loss: 0.471" },
  { timestamp: "15:45:17", level: "warning", message: "Learning rate reduced to 5e-5 (plateau detected)" },
  { timestamp: "15:45:20", level: "info", message: "Epoch 6/8 started..." },
];

export const mockLossCurve: LossCurvePoint[] = Array.from({ length: 60 }, (_, i) => ({
  step: (i + 1) * 50,
  trainLoss: 2.2 * Math.exp(-i * 0.055) + 0.3 + Math.random() * 0.06,
  valLoss: 2.3 * Math.exp(-i * 0.05) + 0.32 + Math.random() * 0.08,
}));

export const mockComparisonResults: ComparisonResult[] = [
  {
    id: "cmp-1",
    input: "นายสมชาย เดินทางไปประชุมที่ธนาคารกรุงเทพ สาขาสีลม เมื่อวันที่ 15 กุมภาพันธ์",
    studentOutput: "[สมชาย: PERSON] [ธนาคารกรุงเทพ: ORG] [สีลม: LOC] [15 กุมภาพันธ์: DATE]",
    teacherOutput: "[นายสมชาย: PERSON] [ธนาคารกรุงเทพ: ORG] [สีลม: LOC] [15 กุมภาพันธ์: DATE]",
    studentLatencyMs: 42,
    teacherLatencyMs: 380,
    studentScore: 92,
    teacherScore: 97,
  },
  {
    id: "cmp-2",
    input: "บริษัท ปตท. จำกัด (มหาชน) แจ้งผลประกอบการไตรมาส 3 ปี 2026 กำไรสุทธิ 25,000 ล้านบาท",
    studentOutput: "[ปตท.: ORG] [ไตรมาส 3 ปี 2026: DATE] [25,000 ล้านบาท: MONEY]",
    teacherOutput: "[บริษัท ปตท. จำกัด (มหาชน): ORG] [ไตรมาส 3 ปี 2026: DATE] [25,000 ล้านบาท: MONEY]",
    studentLatencyMs: 38,
    teacherLatencyMs: 420,
    studentScore: 88,
    teacherScore: 95,
  },
  {
    id: "cmp-3",
    input: "ดร.วิชัย พบปะกับตัวแทนจาก Google Thailand ที่โรงแรม Marriott สุขุมวิท",
    studentOutput: "[ดร.วิชัย: PERSON] [Google Thailand: ORG] [Marriott สุขุมวิท: LOC]",
    teacherOutput: "[ดร.วิชัย: PERSON] [Google Thailand: ORG] [โรงแรม Marriott สุขุมวิท: LOC]",
    studentLatencyMs: 35,
    teacherLatencyMs: 395,
    studentScore: 94,
    teacherScore: 98,
  },
  {
    id: "cmp-4",
    input: "กระทรวงสาธารณสุขจัดฉีดวัคซีนให้ประชาชนในกรุงเทพมหานครตั้งแต่วันที่ 1 มีนาคม 2026",
    studentOutput: "[กระทรวงสาธารณสุข: ORG] [กรุงเทพมหานคร: LOC] [1 มีนาคม 2026: DATE]",
    teacherOutput: "[กระทรวงสาธารณสุข: ORG] [กรุงเทพมหานคร: LOC] [1 มีนาคม 2026: DATE]",
    studentLatencyMs: 40,
    teacherLatencyMs: 410,
    studentScore: 97,
    teacherScore: 97,
  },
];
