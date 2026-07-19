import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SearchPage } from "./SearchPage";

/**
 * jsdom does not implement EventSource, so the streaming search is exercised against this
 * recording stub: enough surface for useSearchStream (onmessage/onerror/addEventListener/
 * close), plus instance bookkeeping so tests can assert when streams open, with what URL,
 * and that superseded ones are closed.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  closed = false;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Record<string, Array<() => void>> = {};

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = init?.withCredentials ?? false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, callback: () => void) {
    (this.listeners[type] ??= []).push(callback);
  }

  close() {
    this.closed = true;
  }
}

vi.stubGlobal("EventSource", FakeEventSource);

function renderSearchPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SearchPage />
    </QueryClientProvider>,
  );
}

function typeQuery(text: string) {
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: text } });
}

describe("SearchPage as-you-type", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not open a stream while typing, only after the debounce settles", () => {
    renderSearchPage();
    typeQuery("tum hi");
    expect(FakeEventSource.instances).toHaveLength(0);

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=tum+hi");
  });

  it("never fires for a single character", () => {
    renderSearchPage();
    typeQuery("t");
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("a newer query closes the superseded stream and opens a fresh one", () => {
    renderSearchPage();
    typeQuery("tum");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    typeQuery("tum hi ho");
    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.instances[1].url).toContain("q=tum+hi+ho");
    expect(FakeEventSource.instances[1].closed).toBe(false);
  });

  it("keystrokes inside the debounce window collapse into one stream", () => {
    renderSearchPage();
    typeQuery("tu");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    typeQuery("tum");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    typeQuery("tum h");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=tum+h");
  });

  it("submitting flushes immediately without waiting for the debounce", () => {
    renderSearchPage();
    typeQuery("arijit");
    expect(FakeEventSource.instances).toHaveLength(0);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Search" }));
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=arijit");
  });
});

describe("SearchPage '/' shortcut", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
  });

  it("focuses the search box when '/' is pressed anywhere on the page", () => {
    renderSearchPage();
    const searchbox = screen.getByRole("searchbox");
    expect(searchbox).not.toHaveFocus();

    fireEvent.keyDown(document.body, { key: "/" });

    expect(searchbox).toHaveFocus();
  });

  it("does not intercept '/' typed inside the search box itself", () => {
    renderSearchPage();
    const searchbox = screen.getByRole("searchbox");
    searchbox.focus();

    fireEvent.keyDown(searchbox, { key: "/" });
    typeQuery("ac/dc");

    expect(searchbox).toHaveValue("ac/dc");
  });
});
