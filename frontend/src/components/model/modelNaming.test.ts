import { describe, expect, it } from "vitest";
import { suggestTrainingName } from "./modelNaming";

const NAME_PATTERN = /^[a-z0-9][a-z0-9._-]{0,62}$/;
const now = new Date(2026, 10, 13, 9, 5);

describe("suggestTrainingName", () => {
  it("derives a tag-safe name from the project name", () => {
    expect(suggestTrainingName("Thai Sentiment Analyzer", now)).toBe("thai-sentiment-analyzer-20261113-0905");
  });

  it("falls back to 'model' for non-Latin names and stays valid", () => {
    const name = suggestTrainingName("วิเคราะห์ความรู้สึก", now);
    expect(name).toBe("model-20261113-0905");
    expect(NAME_PATTERN.test(name)).toBe(true);
  });

  it("keeps long names within the 63-character limit", () => {
    expect(NAME_PATTERN.test(suggestTrainingName("x".repeat(200), now))).toBe(true);
  });
});
