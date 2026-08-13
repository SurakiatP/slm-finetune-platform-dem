import { describe, expect, it } from "vitest";
import { describeAuthError } from "@/lib/authError";

describe("describeAuthError", () => {
  it("distinguishes an unreachable Supabase project from invalid credentials", () => {
    expect(describeAuthError(new Error("Failed to fetch"))).toMatch(/Supabase project URL/i);
    expect(describeAuthError({ message: "Invalid login credentials", status: 400 }))
      .toBe("Invalid login credentials");
  });
});
