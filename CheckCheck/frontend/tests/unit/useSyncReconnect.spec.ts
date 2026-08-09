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
  listeners = new Map<string, Set<(e: any) => void>>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: any) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: (e: any) => void) {
    this.listeners.get(type)?.delete(fn);
  }
  close() {
    this.readyState = MockEventSource.CLOSED;
  }
  // ── test helpers ──
  /**
   * Simulate a full successful connect: response headers (`onopen`) followed by
   * the server's `ready` message, which is what actually proves the server has
   * SUBSCRIBED this client (see routes_sync_notification.SSE_READY_MESSAGE).
   */
  emitOpen() {
    this.emitHeaders();
    this.emitReady();
  }
  /** Response headers only: connected, but not yet in the server's fan-out set. */
  emitHeaders() {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.();
  }
  /** The server's readiness handshake. */
  emitReady() {
    for (const fn of this.listeners.get("ready") ?? []) fn({ data: "{}" });
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

// Resolves `true` = "the pull reached the server" (applyDelta's contract).
const applyDelta = vi.fn(async () => true);
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
  const syncStatus = await import("@/utils/syncStatus");
  const { useSync } = await import("@/composables/useSync");
  return {
    ...useSync(),
    isOnline: connectivity.isOnline,
    setConnectivity: connectivity.setConnectivity,
    streamLive: () => syncStatus.getSyncStatus().streamLive,
  };
}

beforeEach(() => {
  MockEventSource.reset();
  applyDelta.mockClear();
  applyDelta.mockImplementation(async () => true);
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

// ── Readiness handshake (bug B4) ─────────────────────────────────────────────
//
// Response headers arrive before the server has added this client to its fan-out
// list, and pokes are never redelivered, so anything published in that window is
// lost. Nothing that depends on "pokes will reach me" may key off `onopen`; it
// waits for the server's `ready` message.
describe("useSync readiness handshake", () => {
  it("does not claim connectivity or a live stream on headers alone", async () => {
    const { connect, isOnline, setConnectivity, streamLive } = await loadFresh();

    connect();
    setConnectivity(false); // start from a known-down signal
    MockEventSource.latest.emitHeaders();

    expect(streamLive()).toBe(false);
    expect(isOnline()).toBe(false);
  });

  it("goes live only once the server confirms the subscription", async () => {
    const { connect, isOnline, streamLive } = await loadFresh();

    connect();
    MockEventSource.latest.emitHeaders();
    MockEventSource.latest.emitReady();

    expect(streamLive()).toBe(true);
    expect(isOnline()).toBe(true);
  });

  it("reconciles on a RECONNECT's ready, not on its headers", async () => {
    const { connect } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen(); // first-ever connect, nothing to reconcile
    expect(applyDelta).not.toHaveBeenCalled();

    MockEventSource.latest.emitErrorClosed();
    vi.advanceTimersByTime(1000);

    // Headers on the new stream: still not subscribed, so pulling now could race
    // a poke into a set we are not in yet.
    MockEventSource.latest.emitHeaders();
    expect(applyDelta).not.toHaveBeenCalled();

    MockEventSource.latest.emitReady();
    expect(applyDelta).toHaveBeenCalled();
  });

  it("drops the live flag when the stream errors", async () => {
    const { connect, streamLive } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();
    expect(streamLive()).toBe(true);

    MockEventSource.latest.emitErrorClosed();
    expect(streamLive()).toBe(false);
  });
});

// ── Offline-gap recovery (bug B3) ────────────────────────────────────────────
//
// A stream can die *quietly*: a sleeping laptop, a dropped Wi-Fi link, an expired
// NAT mapping, and `context.setOffline` in the E2E suite, all kill the connection
// without firing `onerror`. No error means no reconnect, and since pokes only
// arrive over SSE the tab then sits stale forever. Recovery must therefore also
// hang off the connectivity signal, not only off stream errors.
describe("useSync recovers from an offline gap without an onerror", () => {
  it("rebuilds the stream when connectivity returns and the stream is not live", async () => {
    const { connect, setConnectivity } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();
    const before = MockEventSource.instances.length;

    // The link goes away and comes back WITHOUT the stream ever erroring.
    setConnectivity(false);
    setConnectivity(true);

    expect(MockEventSource.instances.length).toBe(before + 1);

    // And the rebuilt stream reconciles the gap once the server confirms it.
    MockEventSource.latest.emitOpen();
    expect(applyDelta).toHaveBeenCalled();
  });

  it("does not rebuild on its own ready→setConnectivity(true) (no reconnect loop)", async () => {
    const { connect } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen(); // ready → setConnectivity(true) re-enters the watcher
    const count = MockEventSource.instances.length;

    vi.advanceTimersByTime(60_000);
    expect(MockEventSource.instances.length).toBe(count);
  });

  it("does not resurrect a stream we deliberately closed", async () => {
    const { connect, disconnect, setConnectivity } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen();
    disconnect();

    const count = MockEventSource.instances.length;
    setConnectivity(false);
    setConnectivity(true);
    vi.advanceTimersByTime(60_000);
    expect(MockEventSource.instances.length).toBe(count);
  });

  it("retries a recovery delta pull that never reached the server", async () => {
    const { connect } = await loadFresh();

    connect();
    MockEventSource.latest.emitOpen(); // first connect
    MockEventSource.latest.emitErrorClosed();
    vi.advanceTimersByTime(1000);

    // The reconnect's pull fails to reach the server (the board would otherwise
    // stay stale with nothing scheduled to fix it).
    applyDelta.mockImplementationOnce(async () => false);
    MockEventSource.latest.emitOpen();
    await vi.waitFor(() => expect(applyDelta).toHaveBeenCalledTimes(1));

    // A retry is scheduled on the capped backoff and this one lands.
    await vi.advanceTimersByTimeAsync(1000);
    expect(applyDelta).toHaveBeenCalledTimes(2);

    // Converged, so no further retries.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(applyDelta).toHaveBeenCalledTimes(2);
  });
});
