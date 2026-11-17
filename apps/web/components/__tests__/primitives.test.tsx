import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualityBadge, StatePill } from "../ui/primitives";

describe("primitives", () => {
  it("labels unavailable data explicitly", () => {
    render(<QualityBadge quality="unavailable" />);
    expect(screen.getByText("NO DATA")).toBeTruthy();
  });
  it("renders component state labels", () => {
    render(<StatePill state="not_configured" />);
    expect(screen.getByText("Not configured")).toBeTruthy();
  });
});
