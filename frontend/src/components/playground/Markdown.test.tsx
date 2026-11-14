import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders bold, inline code and lists instead of raw markers", () => {
    const { container } = render(<Markdown text={"**Positive** with `code`\n\n- one\n- two"} />);
    expect(screen.getByText("Positive").tagName).toBe("STRONG");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.textContent).not.toContain("**");
  });

  it("never turns model text into HTML", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> **hi**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });
});
