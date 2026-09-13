import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatStrip } from "./StatStrip";

describe("StatStrip", () => {
  it("renders every stat's value and label as text", () => {
    render(
      <StatStrip
        stats={[
          { label: "Fulfilled", value: 12 },
          { label: "Failed", value: 3 },
        ]}
      />,
    );
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Fulfilled")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("shows the optional detail line only when one is supplied", () => {
    const { rerender } = render(
      <StatStrip stats={[{ label: "Free space", value: "283 GB", detail: "of 4.5 TB" }]} />,
    );
    expect(screen.getByText("of 4.5 TB")).toBeInTheDocument();

    rerender(<StatStrip stats={[{ label: "Free space", value: "283 GB" }]} />);
    expect(screen.queryByText("of 4.5 TB")).not.toBeInTheDocument();
  });

  it("defaults to the neutral tone rather than an undefined class", () => {
    const { container } = render(<StatStrip stats={[{ label: "Total", value: 1 }]} />);
    const tile = container.querySelector(".stat-strip__item");
    expect(tile).toHaveClass("stat-strip__item--neutral");
    expect(tile?.className).not.toContain("undefined");
  });

  it("applies the requested tone", () => {
    const { container } = render(
      <StatStrip stats={[{ label: "Failed", value: 2, tone: "bad" }]} />,
    );
    expect(container.querySelector(".stat-strip__item")).toHaveClass("stat-strip__item--bad");
  });

  it("keeps the value readable as text, since tone colour alone must not carry meaning", () => {
    render(<StatStrip stats={[{ label: "Failed", value: 7, tone: "bad" }]} />);
    // Someone who cannot see the colour still gets the number and its label.
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});
