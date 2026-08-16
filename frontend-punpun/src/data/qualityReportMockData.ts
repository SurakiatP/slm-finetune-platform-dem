export interface QualityIssue {
  id: string;
  severity: "critical" | "warning" | "info";
  category: "imbalance" | "duplicates" | "missing" | "outliers" | "length";
  title: string;
  description: string;
  affectedRows: number;
  suggestion: string;
}

export interface ClassDistribution {
  label: string;
  count: number;
  percent: number;
}

export interface LengthBucket {
  bucket: string;
  count: number;
}

export interface QualityReport {
  overallScore: number;
  totalRows: number;
  duplicateRows: number;
  missingLabels: number;
  outliers: number;
  issues: QualityIssue[];
  classDistribution: ClassDistribution[];
  lengthDistribution: LengthBucket[];
}

export const mockQualityReport: QualityReport = {
  overallScore: 72,
  totalRows: 2400,
  duplicateRows: 87,
  missingLabels: 14,
  outliers: 23,
  issues: [
    {
      id: "iss-1",
      severity: "critical",
      category: "imbalance",
      title: "Severe class imbalance detected",
      description: "Class 'refund' has only 3.2% of total samples, while 'general' has 47%.",
      affectedRows: 77,
      suggestion: "Consider oversampling minority class or collecting 200+ more 'refund' samples to balance the dataset.",
    },
    {
      id: "iss-2",
      severity: "warning",
      category: "duplicates",
      title: "87 duplicate rows found",
      description: "Exact text duplicates can cause overfitting and skew evaluation metrics.",
      affectedRows: 87,
      suggestion: "Remove duplicates before training. Estimated ~30 credits saved per epoch.",
    },
    {
      id: "iss-3",
      severity: "warning",
      category: "missing",
      title: "14 rows have missing labels",
      description: "Rows without labels will be skipped during training, reducing effective dataset size.",
      affectedRows: 14,
      suggestion: "Use the Annotation Tool to label these rows, or remove them from the dataset.",
    },
    {
      id: "iss-4",
      severity: "info",
      category: "outliers",
      title: "23 unusually long samples",
      description: "Samples exceeding 2,048 tokens may be truncated and lose context.",
      affectedRows: 23,
      suggestion: "Review long samples — consider chunking or summarizing before training.",
    },
    {
      id: "iss-5",
      severity: "info",
      category: "length",
      title: "Text length variance is high",
      description: "Length distribution std dev is 412 chars (mean: 156). High variance can affect batch training efficiency.",
      affectedRows: 0,
      suggestion: "Consider length bucketing in your data loader for better GPU utilization.",
    },
  ],
  classDistribution: [
    { label: "general", count: 1128, percent: 47 },
    { label: "billing", count: 432, percent: 18 },
    { label: "technical", count: 384, percent: 16 },
    { label: "account", count: 264, percent: 11 },
    { label: "shipping", count: 115, percent: 4.8 },
    { label: "refund", count: 77, percent: 3.2 },
  ],
  lengthDistribution: [
    { bucket: "0-50", count: 234 },
    { bucket: "51-150", count: 1102 },
    { bucket: "151-300", count: 723 },
    { bucket: "301-600", count: 295 },
    { bucket: "601-1200", count: 23 },
    { bucket: "1200+", count: 23 },
  ],
};
