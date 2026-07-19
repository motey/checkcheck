// Unit tests for the self-managed SSE reconnect in composables/useSync.ts.
//
// Regression for docs/ISSUES.md "Client stays Offline after the server recovers
// — SSE never reconnects": when the `/api/sync` EventSource closes on an HTTP
// error status (a 502/503 from a bounced backend behind Traefik) the browser
// fails the stream permanently and never retries, so the client stayed stuck
// "Offline" until a manual reload. useSync now schedules its own capped-backoff
// reconnect on any `onerror`, and a successful `onopen` (ours or the browser's
// own retry) restores connectivity.
//
// We drive a mock EventSource through the real useSync connect/error/open
// lifecycle and assert against the REAL connectivity module (utils/connectivity)
// that the online signal flips back true on recovery — no reload.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ── Mock EventSource ─────────────────────────────────────────────────────────
class MockEventSource {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSED = 2 as const;
  static instances: MockEventSource[] = [];

  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: any) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() {
    this.readyState = MockEventSource.CLOSED;
  }
  // ── test helpers ──
  /** Simulate the stream opening (server reachable). */
  emitOpen() {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.();
  }
  /** Simulate a permanent HTTP-error close (502/503): browser gives up. */
  emitErrorClosed() {
    this.readyState = MockEventSource.CLOSED;
    this.onerror?.();
  }
  /** Simulate a transient error where the browser intends to auto-retry. */
  emitErrorConnecting() {
    this.readyState = MockEventSource.CONNECTING;
    this.onerror?.();
  }

  static get latest() {
    return this.instances[this.instances.length - 1]!;
  }
  static reset() {
    this.instances = [];
  }
}

// ── Store / composable-dependency mocks (useSync is Nuxt-coupled) ────────────
const stub = () => ({
  refresh: vi.fn(),
  resync: vi.fn(),
  fetchCounts: vi.fn(),
  refreshIfOpen: vi.fn(),
  refreshUnread: vi.fn(),
  refreshAllCheckListItems: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  checkLists: [] as any[],
  checkListsItems: {} as Record<string, any[]>,
  open: false,
});
vi.mock("@/stores/checklist", () => ({ useCheckListsStore: () => stub() }));
vi.mock("@/stores/checklist_item", () => ({ useCheckListsItemStore: () => stub() }));
vi.mock("@/stores/share", () => ({ useShareStore: () => stub() }));
vi.mock("@/stores/notification", () => ({ useNotificationStore: () => stub() }));
vi.mock("@/stores/invite", () => ({ useInviteStore: () => stub() }));
vi.mock("@/utils/localFirst", () => ({ isLocalFirstEnabled: () => true }));

const applyDelta = vi.fn(async () => {});
vi.mock("@/utils/localSnapshot", () => ({ applyDelta }));

/**
 * Fresh useSync + fresh connectivity module per test. `createSharedComposable`
 * memoizes the composable (and its private `es` / timer closure) for the life of
 * the module, and utils/connectivity keeps module-level state, so we reset the
 * module registry and re-import to isolate each scenario.
 */
async function loadFresh() {
  vi.resetModules();
  const connectivity = await import("@/utils/connectivity");
  const { useSync } = await import("@/composables/useSync");
  return { ...useSync(), isOnline: connectivity.isOnline };
}

beforeEach(() => {
  MockEventSource.reset();
  applyDelta.mockClear();
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", MockEventSource);
  vi.stubGlobal("useNuxtApp", () => ({ $pinia: {} }));
  // Intentionally NO `document` global: node's default (undefined) makes useSync
  // skip its visibilitychange listener and lets Vue's runtime-dom take its
  // no-document path. A partial document stub breaks the runtime-dom import.
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useSync SSE self-managed reconnect", () => {
  it("recovers to online after a permanent HTTP-error close (the stuck-Offline bug)", async () => {
    const { connect, isOnline } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();
    expect(isOnline()).toBe(true);

    // Backend bounces → EventSource fails on 503 → permanent CLOSED.
    MockEventSource.latest.emitErrorClosed();
    expect(isOnline()).toBe(false);

    // First capped-backoff reconnect (1s) — a brand-new stream is created.
    const before = MockEventSource.instances.length;
    vi.advanceTimersByTime(1000);
    expect(MockEventSource.instances.length).toBe(before + 1);

    // Server still booting → the fresh stream also 503s → CLOSED again.
    MockEventSource.latest.emitErrorClosed();
    expect(isOnline()).toBe(false);

    // Backoff doubled to 2s → another reconnect.
    vi.advanceTimersByTime(2000);

    // Server is back: this stream opens.
    MockEventSource.latest.emitOpen();
    expect(isOnline()).toBe(true);
    // A reconnect (not the first-ever open) reconciles via a delta pull.
    expect(applyDelta).toHaveBeenCalled();
  });

  it("cancels the pending manual reconnect when the browser's own retry succeeds first", async () => {
    const { connect, isOnline } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();

    // Transient error: browser intends to auto-retry (readyState CONNECTING). We
    // still schedule a backup reconnect.
    const es0 = MockEventSource.latest;
    es0.emitErrorConnecting();
    expect(isOnline()).toBe(false);

    // Browser's native retry succeeds on the SAME stream before our timer fires.
    es0.emitOpen();
    expect(isOnline()).toBe(true);

    // Our pending reconnect must have been cancelled — no extra stream created.
    const count = MockEventSource.instances.length;
    vi.advanceTimersByTime(60_000);
    expect(MockEventSource.instances.length).toBe(count);
  });

  it("does not reconnect after an explicit disconnect", async () => {
    const { connect, disconnect } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();
    MockEventSource.latest.emitErrorClosed(); // schedules a reconnect

    disconnect(); // must clear the pending timer

    const count = MockEventSource.instances.length;
    vi.advanceTimersByTime(60_000);
    expect(MockEventSource.instances.length).toBe(count);
  });
});
