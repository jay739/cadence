import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { RouteTransition } from "./RouteTransition";

function TestShell() {
  return (
    <Routes>
      <Route
        element={
          <div>
            <Link to="/one">One</Link>
            <Link to="/two">Two</Link>
            <RouteTransition />
          </div>
        }
      >
        <Route path="/one" element={<p>Page one</p>} />
        <Route path="/two" element={<p>Page two</p>} />
      </Route>
    </Routes>
  );
}

describe("RouteTransition", () => {
  it("renders the matched route's content", () => {
    render(
      <MemoryRouter initialEntries={["/one"]}>
        <TestShell />
      </MemoryRouter>,
    );
    expect(screen.getByText("Page one")).toBeInTheDocument();
  });

  it("swaps to the new route's content on navigation and drops the old", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/one"]}>
        <TestShell />
      </MemoryRouter>,
    );
    expect(screen.getByText("Page one")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Two" }));

    expect(screen.getByText("Page two")).toBeInTheDocument();
    expect(screen.queryByText("Page one")).not.toBeInTheDocument();
  });

  it("remounts the transition wrapper on pathname change so the enter animation replays", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/one"]}>
        <TestShell />
      </MemoryRouter>,
    );
    const firstNode = screen.getByText("Page one").closest(".route-transition");
    expect(firstNode).not.toBeNull();

    await user.click(screen.getByRole("link", { name: "Two" }));

    const secondNode = screen.getByText("Page two").closest(".route-transition");
    expect(secondNode).not.toBeNull();
    // A fresh DOM node (not an update to the same one) is what restarts the CSS animation.
    expect(secondNode).not.toBe(firstNode);
  });
});
