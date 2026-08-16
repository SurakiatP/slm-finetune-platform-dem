// Compute QualityReport from expanded dataset rows
import { expandDataset, getLabelColumn, getTextColumn, type ExpandedRow } from "./datasetExpander";
import type { QualityReport, QualityIssue, ClassDistribution, LengthBucket } from "@/data/qualityReportMockData";

export function computeQualityReport(datasetId: string): QualityReport {
  const rows = expandDataset(datasetId);
  const labelCol = getLabelColumn(datasetId);
  const textCol = getTextColumn(datasetId);

  // ---- Duplicates (exact text match) ----
  const textCounts = new Map<string, number>();
  if (textCol) {
    for (const r of rows) {
      const v = String(r[textCol] ?? "").trim();
      if (!v) continue;
      textCounts.set(v, (textCounts.get(v) ?? 0) + 1);
    }
  }
  const duplicateRows = Array.from(textCounts.values()).reduce((s, c) => s + (c > 1 ? c - 1 : 0), 0);

  // ---- Missing labels ----
  const missingLabels = labelCol
    ? rows.filter((r) => r[labelCol] === "" || r[labelCol] === null || r[labelCol] === undefined).length
    : 0;

  // ---- Class distribution ----
  const classCounts = new Map<string, number>();
  if (labelCol) {
    for (const r of rows) {
      const v = String(r[labelCol] ?? "").trim();
      if (!v) continue;
      classCounts.set(v, (classCounts.get(v) ?? 0) + 1);
    }
  }
  const totalClass = Array.from(classCounts.values()).reduce((s, c) => s + c, 0);
  const classDistribution: ClassDistribution[] = Array.from(classCounts.entries())
    .map(([label, count]) => ({
      label,
      count,
      percent: totalClass ? +((count / totalClass) * 100).toFixed(1) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  // ---- Text length distribution ----
  const lengths: number[] = [];
  if (textCol) {
    for (const r of rows) {
      const v = String(r[textCol] ?? "");
      if (v) lengths.push(v.length);
    }
  }
  const buckets: { name: string; min: number; max: number }[] = [
    { name: "0-50", min: 0, max: 50 },
    { name: "51-150", min: 51, max: 150 },
    { name: "151-300", min: 151, max: 300 },
    { name: "301-600", min: 301, max: 600 },
    { name: "601-1200", min: 601, max: 1200 },
    { name: "1200+", min: 1201, max: Infinity },
  ];
  const lengthDistribution: LengthBucket[] = buckets.map((b) => ({
    bucket: b.name,
    count: lengths.filter((l) => l >= b.min && l <= b.max).length,
  }));

  const meanLen = lengths.length ? lengths.reduce((s, l) => s + l, 0) / lengths.length : 0;
  const stdLen = lengths.length
    ? Math.sqrt(lengths.reduce((s, l) => s + (l - meanLen) ** 2, 0) / lengths.length)
    : 0;

  // ---- Outliers: text length > 2x mean+std ----
  const outlierThreshold = meanLen + 2 * stdLen;
  const outliers = lengths.filter((l) => l > outlierThreshold && l > 600).length;

  // ---- Issues / suggestions ----
  const issues: QualityIssue[] = [];
  if (classDistribution.length >= 2) {
    const min = classDistribution[classDistribution.length - 1];
    const max = classDistribution[0];
    if (min.percent < 5 && totalClass > 50) {
      issues.push({
        id: "iss-imbalance",
        severity: "critical",
        category: "imbalance",
        title: `Severe class imbalance — '${min.label}' only ${min.percent}%`,
        description: `Class '${min.label}' has ${min.count} samples (${min.percent}%), while '${max.label}' has ${max.count} (${max.percent}%).`,
        affectedRows: min.count,
        suggestion: `Oversample '${min.label}' or collect ~${Math.max(50, Math.round(totalClass * 0.05) - min.count)} more samples to reach 5%.`,
      });
    } else if (min.percent < 10 && totalClass > 50) {
      issues.push({
        id: "iss-imbalance",
        severity: "warning",
        category: "imbalance",
        title: `Mild class imbalance — '${min.label}' at ${min.percent}%`,
        description: `Smallest class has ${min.count} samples vs largest at ${max.count}.`,
        affectedRows: min.count,
        suggestion: `Consider class weighting or moderate oversampling of '${min.label}'.`,
      });
    }
  }

  if (duplicateRows > 0) {
    issues.push({
      id: "iss-duplicates",
      severity: duplicateRows > 30 ? "warning" : "info",
      category: "duplicates",
      title: `${duplicateRows} duplicate rows detected`,
      description: `Exact text duplicates skew evaluation and cause overfitting.`,
      affectedRows: duplicateRows,
      suggestion: `Remove duplicates before training — saves training time and improves eval validity.`,
    });
  }

  if (missingLabels > 0) {
    issues.push({
      id: "iss-missing",
      severity: missingLabels > 20 ? "warning" : "info",
      category: "missing",
      title: `${missingLabels} rows have missing labels`,
      description: `Unlabeled rows will be skipped during training.`,
      affectedRows: missingLabels,
      suggestion: `Use the Annotation Tool to label these rows or remove them.`,
    });
  }

  if (outliers > 0) {
    issues.push({
      id: "iss-outliers",
      severity: "info",
      category: "outliers",
      title: `${outliers} unusually long samples`,
      description: `Samples exceeding ${Math.round(outlierThreshold)} chars (2σ above mean) may be truncated by the tokenizer.`,
      affectedRows: outliers,
      suggestion: `Review and chunk or summarize long samples before training.`,
    });
  }

  if (stdLen > meanLen * 1.5 && lengths.length > 10) {
    issues.push({
      id: "iss-length",
      severity: "info",
      category: "length",
      title: "High text length variance",
      description: `Std dev: ${Math.round(stdLen)} chars (mean: ${Math.round(meanLen)}). Affects batch efficiency.`,
      affectedRows: 0,
      suggestion: "Use length bucketing in the data loader to improve GPU utilization.",
    });
  }

  // ---- Overall score ----
  // Start at 100, deduct for each problem class
  let score = 100;
  if (totalClass > 0) {
    const minPct = classDistribution[classDistribution.length - 1]?.percent ?? 100;
    if (minPct < 5) score -= 25;
    else if (minPct < 10) score -= 12;
  }
  score -= Math.min(15, Math.round((duplicateRows / Math.max(rows.length, 1)) * 100));
  score -= Math.min(10, Math.round((missingLabels / Math.max(rows.length, 1)) * 100));
  score -= Math.min(8, outliers);
  score = Math.max(15, Math.min(100, score));

  return {
    overallScore: score,
    totalRows: rows.length,
    duplicateRows,
    missingLabels,
    outliers,
    issues,
    classDistribution,
    lengthDistribution,
  };
}

export function getDatasetSampleSize(datasetId: string): number {
  return expandDataset(datasetId).length;
}

export function getRawRows(datasetId: string): ExpandedRow[] {
  return expandDataset(datasetId);
}
