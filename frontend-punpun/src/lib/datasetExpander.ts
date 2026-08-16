// Expand the small mock sample rows into a larger realistic dataset
// so quality stats look meaningful but stay deterministic.
import { mockSampleRows, mockSchemas, mockDatasets } from "@/data/datasetMockData";

export type ExpandedRow = Record<string, string | number | boolean>;

// Deterministic pseudo-random based on a string seed
function seedRandom(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6D2B79F5;
    let t = Math.imul(h ^ (h >>> 15), 1 | h);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// For ds-1 (thai-complaints) we mimic an imbalanced dataset:
// technical 33%, billing 28%, service 25%, general 12%, refund 2%
const DS1_LABELS = [
  { label: "technical", weight: 33 },
  { label: "billing", weight: 28 },
  { label: "service", weight: 25 },
  { label: "general", weight: 12 },
  { label: "refund", weight: 2 },
];
const DS1_TEXTS_TH: Record<string, string[]> = {
  technical: [
    "อินเทอร์เน็ตหลุดบ่อยมาก ใช้งานไม่ได้เลย",
    "Speed test แสดงเพียง 5 Mbps ทั้งที่จ่าย 100",
    "Wi-Fi router ไม่ทำงานหลังจากอัปเดต",
    "ใช้แอปไม่ได้ ขึ้น error ตลอด",
    "ระบบล่มตอนกลางคืนบ่อยมาก",
  ],
  billing: [
    "ค่าบริการแพงเกินไป อยากยกเลิก",
    "ไม่สามารถชำระเงินผ่านแอปได้",
    "บิลเดือนนี้แพงผิดปกติ",
    "ขอใบเสร็จย้อนหลัง 3 เดือน",
    "หักบัตรเครดิตซ้ำสองรอบ",
  ],
  service: [
    "พนักงานบริการดีมาก ขอบคุณครับ",
    "Call center รอนานเกินไป",
    "พนักงานสุภาพและช่วยแก้ปัญหาได้",
    "ติดต่อพนักงานไม่ได้เลยทั้งวัน",
    "บริการประทับใจมากครับ",
  ],
  general: [
    "อยากทราบโปรโมชันเดือนนี้",
    "เปลี่ยนแพ็กเกจได้อย่างไร",
    "อยากย้ายค่ายเบอร์เดิม",
  ],
  refund: [
    "ขอคืนเงินค่าบริการที่จ่ายเกิน",
    "ต้องการเงินคืนภายในกี่วัน",
  ],
};
const DS1_DUPLICATES = ["อินเทอร์เน็ตหลุดบ่อยมาก ใช้งานไม่ได้เลย"]; // intentionally seeded as exact dup

// For ds-2 (product-reviews) — rating-based "labels"
const DS2_REVIEWS: Record<number, string[]> = {
  1: ["Broke after 2 weeks of use", "Total waste of money", "Doesn't work at all"],
  2: ["Disappointing quality", "Not worth the price"],
  3: ["Decent for the price range", "Average quality, nothing special", "Okay but expected more"],
  4: ["Good product overall", "Works well, minor issues"],
  5: ["Great product, works as expected!", "Best purchase I made this year", "Excellent quality and fast shipping"],
};
const DS2_DIST = [
  { rating: 5, weight: 42 },
  { rating: 4, weight: 24 },
  { rating: 3, weight: 16 },
  { rating: 1, weight: 12 },
  { rating: 2, weight: 6 },
];

// For ds-3 (ner) — single text field with optional missing entity_count
const DS3_TEMPLATES = [
  { text: "นายสมชาย ทำงานที่ บริษัท ปตท.", entities: '[{"text":"สมชาย","label":"PERSON"},{"text":"ปตท.","label":"ORG"}]', entity_count: 2 },
  { text: "กรุงเทพมหานคร เป็นเมืองหลวง", entities: '[{"text":"กรุงเทพมหานคร","label":"LOC"}]', entity_count: 1 },
  { text: "ธนาคารกสิกรไทย สาขาสีลม", entities: '[{"text":"ธนาคารกสิกรไทย","label":"ORG"},{"text":"สีลม","label":"LOC"}]', entity_count: 2 },
  { text: "นางสาวมาลี เกิดที่จังหวัดเชียงใหม่ เมื่อปี 2535", entities: '[{"text":"มาลี","label":"PERSON"},{"text":"เชียงใหม่","label":"LOC"}]', entity_count: 2 },
];

function pickWeighted<T extends { weight: number }>(rng: () => number, items: T[]): T {
  const total = items.reduce((s, x) => s + x.weight, 0);
  let r = rng() * total;
  for (const it of items) {
    r -= it.weight;
    if (r <= 0) return it;
  }
  return items[items.length - 1];
}

export function expandDataset(datasetId: string): ExpandedRow[] {
  const meta = mockDatasets.find((d) => d.id === datasetId);
  if (!meta) return [];
  const rng = seedRandom(datasetId);
  // Cap at 600 rows for perf — enough for stats to be meaningful.
  const total = Math.min(meta.rows, 600);
  const rows: ExpandedRow[] = [];

  if (datasetId === "ds-1") {
    for (let i = 0; i < total; i++) {
      const labelChoice = pickWeighted(rng, DS1_LABELS);
      const texts = DS1_TEXTS_TH[labelChoice.label];
      const text = texts[Math.floor(rng() * texts.length)];
      const language = rng() < 0.16 ? "en" : "th";
      // ~3% missing labels, ~5% missing confidence
      const missingLabel = rng() < 0.03;
      const confidence = rng() < 0.05 ? null : +(0.5 + rng() * 0.49).toFixed(2);
      rows.push({
        text,
        category: missingLabel ? "" : labelChoice.label,
        confidence: confidence ?? "",
        language,
      });
    }
    // Inject 18 exact duplicates
    for (let i = 0; i < 18; i++) {
      rows.push({ text: DS1_DUPLICATES[0], category: "technical", confidence: 0.9, language: "th" });
    }
    return rows;
  }

  if (datasetId === "ds-2") {
    for (let i = 0; i < total; i++) {
      const ratingChoice = pickWeighted(rng, DS2_DIST);
      const pool = DS2_REVIEWS[ratingChoice.rating];
      const review = pool[Math.floor(rng() * pool.length)];
      // ~2% missing rating, occasional outlier text
      const missing = rng() < 0.02;
      const longOutlier = rng() < 0.04;
      rows.push({
        review_text: longOutlier ? review + " " + review.repeat(8) : review,
        rating: missing ? "" : ratingChoice.rating,
        product_id: ["P001", "P042", "P118", "P234", "P567"][Math.floor(rng() * 5)],
        helpful_votes: Math.floor(rng() * 30),
        verified: rng() > 0.2,
      });
    }
    return rows;
  }

  if (datasetId === "ds-3") {
    for (let i = 0; i < total; i++) {
      const t = DS3_TEMPLATES[Math.floor(rng() * DS3_TEMPLATES.length)];
      const missing = rng() < 0.025;
      rows.push({
        text: t.text,
        entities: missing ? "" : t.entities,
        entity_count: missing ? "" : t.entity_count,
      });
    }
    return rows;
  }

  // Fallback: return any provided sample rows
  return mockSampleRows[datasetId] || [];
}

export function getLabelColumn(datasetId: string): string | null {
  const schema = mockSchemas[datasetId];
  if (!schema) return null;
  // Heuristic: prefer "category", "label", "rating", "intent"
  const preferred = ["category", "label", "rating", "intent", "class"];
  for (const p of preferred) {
    if (schema.find((c) => c.name === p)) return p;
  }
  return null;
}

export function getTextColumn(datasetId: string): string | null {
  const schema = mockSchemas[datasetId];
  if (!schema) return null;
  const preferred = ["text", "review_text", "content", "input"];
  for (const p of preferred) {
    if (schema.find((c) => c.name === p)) return p;
  }
  return schema.find((c) => c.type === "string")?.name ?? null;
}
