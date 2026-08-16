export interface DatasetInfo {
  id: string;
  name: string;
  rows: number;
  columns: number;
  fileSize: string;
  format: string;
  createdAt: string;
}

export interface ColumnSchema {
  name: string;
  type: "string" | "number" | "boolean";
  nullable: boolean;
  uniqueCount: number;
}

export interface ColumnStats {
  name: string;
  type: string;
  min?: number;
  max?: number;
  mean?: number;
  topValues?: { value: string; count: number }[];
  avgLength?: number;
}

export const mockDatasets: DatasetInfo[] = [
  { id: "ds-1", name: "thai-complaints", rows: 5000, columns: 4, fileSize: "2.3 MB", format: "JSONL", createdAt: "2024-01-15" },
  { id: "ds-2", name: "product-reviews", rows: 12000, columns: 5, fileSize: "8.1 MB", format: "CSV", createdAt: "2024-02-01" },
  { id: "ds-3", name: "ner-annotations", rows: 3200, columns: 3, fileSize: "1.7 MB", format: "JSONL", createdAt: "2024-02-10" },
];

export const mockSchemas: Record<string, ColumnSchema[]> = {
  "ds-1": [
    { name: "text", type: "string", nullable: false, uniqueCount: 4980 },
    { name: "category", type: "string", nullable: false, uniqueCount: 4 },
    { name: "confidence", type: "number", nullable: true, uniqueCount: 120 },
    { name: "language", type: "string", nullable: false, uniqueCount: 2 },
  ],
  "ds-2": [
    { name: "review_text", type: "string", nullable: false, uniqueCount: 11950 },
    { name: "rating", type: "number", nullable: false, uniqueCount: 5 },
    { name: "product_id", type: "string", nullable: false, uniqueCount: 340 },
    { name: "helpful_votes", type: "number", nullable: true, uniqueCount: 89 },
    { name: "verified", type: "boolean", nullable: false, uniqueCount: 2 },
  ],
  "ds-3": [
    { name: "text", type: "string", nullable: false, uniqueCount: 3180 },
    { name: "entities", type: "string", nullable: false, uniqueCount: 2800 },
    { name: "entity_count", type: "number", nullable: false, uniqueCount: 15 },
  ],
};

export const mockSampleRows: Record<string, Record<string, string | number | boolean>[]> = {
  "ds-1": [
    { text: "อินเทอร์เน็ตหลุดบ่อยมาก ใช้งานไม่ได้เลย", category: "technical", confidence: 0.95, language: "th" },
    { text: "ค่าบริการแพงเกินไป อยากยกเลิก", category: "billing", confidence: 0.88, language: "th" },
    { text: "พนักงานบริการดีมาก ขอบคุณครับ", category: "service", confidence: 0.92, language: "th" },
    { text: "ไม่สามารถชำระเงินผ่านแอปได้", category: "billing", confidence: 0.91, language: "th" },
    { text: "Speed test shows only 5 Mbps", category: "technical", confidence: 0.87, language: "en" },
  ],
  "ds-2": [
    { review_text: "Great product, works as expected!", rating: 5, product_id: "P001", helpful_votes: 12, verified: true },
    { review_text: "Broke after 2 weeks of use", rating: 1, product_id: "P042", helpful_votes: 8, verified: true },
    { review_text: "Decent for the price range", rating: 3, product_id: "P001", helpful_votes: 3, verified: false },
    { review_text: "Best purchase I made this year", rating: 5, product_id: "P118", helpful_votes: 25, verified: true },
    { review_text: "Average quality, nothing special", rating: 3, product_id: "P042", helpful_votes: 1, verified: true },
  ],
  "ds-3": [
    { text: "นายสมชาย ทำงานที่ บริษัท ปตท.", entities: '[{"text":"สมชาย","label":"PERSON"},{"text":"ปตท.","label":"ORG"}]', entity_count: 2 },
    { text: "กรุงเทพมหานคร เป็นเมืองหลวง", entities: '[{"text":"กรุงเทพมหานคร","label":"LOC"}]', entity_count: 1 },
    { text: "ธนาคารกสิกรไทย สาขาสีลม", entities: '[{"text":"ธนาคารกสิกรไทย","label":"ORG"},{"text":"สีลม","label":"LOC"}]', entity_count: 2 },
  ],
};

export const mockColumnStats: Record<string, ColumnStats[]> = {
  "ds-1": [
    { name: "text", type: "string", avgLength: 42, topValues: [{ value: "(unique texts)", count: 4980 }] },
    { name: "category", type: "string", topValues: [{ value: "technical", count: 1580 }, { value: "billing", count: 1320 }, { value: "service", count: 1250 }, { value: "general", count: 850 }] },
    { name: "confidence", type: "number", min: 0.52, max: 0.99, mean: 0.87 },
    { name: "language", type: "string", topValues: [{ value: "th", count: 4200 }, { value: "en", count: 800 }] },
  ],
  "ds-2": [
    { name: "review_text", type: "string", avgLength: 68 },
    { name: "rating", type: "number", min: 1, max: 5, mean: 3.7 },
    { name: "product_id", type: "string", topValues: [{ value: "P001", count: 245 }, { value: "P042", count: 198 }, { value: "P118", count: 176 }] },
    { name: "helpful_votes", type: "number", min: 0, max: 342, mean: 8.4 },
    { name: "verified", type: "string", topValues: [{ value: "true", count: 9600 }, { value: "false", count: 2400 }] },
  ],
  "ds-3": [
    { name: "text", type: "string", avgLength: 35 },
    { name: "entities", type: "string", avgLength: 80 },
    { name: "entity_count", type: "number", min: 0, max: 8, mean: 2.1 },
  ],
};
