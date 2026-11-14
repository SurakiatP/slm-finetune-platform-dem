import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUrlState } from "./useUrlState";

const wrapper = ({ children }: { children: ReactNode }) => (
  <MemoryRouter initialEntries={["/projects?q=a"]}>{children}</MemoryRouter>
);

describe("useUrlState", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("keeps every keystroke locally and writes the URL once, debounced", () => {
    const { result } = renderHook(
      () => ({ state: useUrlState<string>("q", ""), params: useSearchParams()[0] }),
      { wrapper },
    );
    expect(result.current.state[0]).toBe("a");

    act(() => {
      for (const v of ["ab", "abc", "abcd"]) result.current.state[1](v);
    });
    expect(result.current.state[0]).toBe("abcd");
    expect(result.current.params.get("q")).toBe("a");

    act(() => vi.advanceTimersByTime(300));
    expect(result.current.params.get("q")).toBe("abcd");
    expect(result.current.state[0]).toBe("abcd");
  });

  it("drops the param when cleared", () => {
    const { result } = renderHook(
      () => ({ state: useUrlState<string>("q", ""), params: useSearchParams()[0] }),
      { wrapper },
    );
    act(() => result.current.state[1](""));
    act(() => vi.advanceTimersByTime(300));
    expect(result.current.params.has("q")).toBe(false);
    expect(result.current.state[0]).toBe("");
  });
});
