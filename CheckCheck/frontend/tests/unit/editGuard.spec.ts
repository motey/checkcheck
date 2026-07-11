// Unit tests for the WI-10 focused-field registry (utils/editGuard) and the
// WI-7 connectivity signal (utils/connectivity). Both are tiny module-level
// state holders that the sync spine leans on — a regression here silently
// breaks focused-edit protection (§4) or outbox draining, so pin the contract.
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  markEditing,
  clearEditing,
  isEditing,
  defaultEditGuard,
  noopEditGuard,
  combineGuards,
  type EditGuard,
} from "@/utils/editGuard";
import {
  isOnline,
  setConnectivity,
  onConnectivityChange,
  probe,
  assertOnline,
  OfflineError,
  decideApiErrorAction,
} from "@/utils/connectivity";

describe("editGuard registry", () => {
  afterEach(() => {
    clearEditing("item", "i1", "text");
    clearEditing("checklist", "c1", "name");
  });

  it("marks, reports and clears a focused field", () => {
    expect(isEditing("item", "i1", "text")).toBe(false);
    markEditing("item", "i1", "text");
    expect(isEditing("item", "i1", "text")).toBe(true);
    expect(defaultEditGuard.isEditing("item", "i1", "text")).toBe(true);
    clearEditing("item", "i1", "text");
    expect(isEditing("item", "i1", "text")).toBe(false);
  });

  it("keys strictly by (kind, id, field) — no cross-protection", () => {
    markEditing("checklist", "c1", "name");
    expect(isEditing("checklist", "c1", "text")).toBe(false);
    expect(isEditing("checklist", "c2", "name")).toBe(false);
    expect(isEditing("item", "c1", "name" as any)).toBe(false);
  });

  it("clearing an unmarked field is a safe no-op; noop guard never protects", () => {
    clearEditing("item", "never-marked", "text");
    markEditing("item", "i1", "text");
    expect(noopEditGuard.isEditing("item", "i1", "text")).toBe(false);
  });
});

describe("combineGuards (WI-11: focus + outbox)", () => {
  const focusName: EditGuard = { isEditing: (_k, _i, f) => f === "name" };
  const outboxPos: EditGuard = {
    isEditing: (_k, _i, f) => f === "position.index",
    isRemoved: (_k, id) => id === "gone",
  };

  it("protects a field if any member guard does", () => {
    const g = combineGuards(focusName, outboxPos);
    expect(g.isEditing("checklist", "c1", "name")).toBe(true);
    expect(g.isEditing("item", "i1", "position.index")).toBe(true);
    expect(g.isEditing("item", "i1", "text")).toBe(false);
  });

  it("reports removed if any member does (and tolerates a guard without isRemoved)", () => {
    const g = combineGuards(focusName, outboxPos);
    expect(g.isRemoved!("item", "gone")).toBe(true);
    expect(g.isRemoved!("item", "here")).toBe(false);
  });
});

describe("connectivity signal", () => {
  afterEach(() => {
    setConnectivity(true); // restore the module default for other suites
    vi.unstubAllGlobals();
  });

  it("setConnectivity flips isOnline and notifies listeners once per change", () => {
    const seen: boolean[] = [];
    const off = onConnectivityChange((v) => seen.push(v));
    setConnectivity(false);
    setConnectivity(false); // duplicate — must not re-notify
    setConnectivity(true);
    off();
    setConnectivity(false); // after unsubscribe — must not notify
    expect(seen).toEqual([false, true]);
    setConnectivity(true);
    expect(isOnline()).toBe(true);
  });

  it("probe treats any real HTTP answer (<500) as reachable and a throw as offline", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401 })));
    await expect(probe()).resolves.toBe(true);
    expect(isOnline()).toBe(true);

    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("network down");
    }));
    await expect(probe()).resolves.toBe(false);
    expect(isOnline()).toBe(false);
  });

  // WI-12: the guard the online-only stores (share/invite/notification) call so
  // nothing is queued and no request is made while offline.
  it("assertOnline throws OfflineError only when offline, with a passable message", () => {
    setConnectivity(true);
    expect(() => assertOnline("nope")).not.toThrow();

    setConnectivity(false);
    expect(() => assertOnline()).toThrow(OfflineError);
    try {
      assertOnline("Sharing isn't available offline.");
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(OfflineError);
      expect((err as OfflineError).offline).toBe(true);
      expect((err as Error).message).toBe("Sharing isn't available offline.");
    }
  });
});

// WI-13: the pure redirect/toast/suppress decision for the global API error
// handler (plugins/api.ts). The offline-auth grace only kicks in when the
// local-first flag is ON *and* the device is offline; every other combination
// must behave exactly like the legacy path.
describe("decideApiErrorAction (WI-13 offline-auth grace)", () => {
  const on = { localFirstEnabled: true, online: true };
  const offlineFlag = { localFirstEnabled: true, online: false };
  const offlineNoFlag = { localFirstEnabled: false, online: false };
  const onlineNoFlag = { localFirstEnabled: false, online: true };

  it("401 offline with the flag on is suppressed — no /login bounce (the grace)", () => {
    expect(decideApiErrorAction(401, offlineFlag)).toBe("suppress");
  });

  it("401 redirects to /login in every other case (online, or flag off)", () => {
    expect(decideApiErrorAction(401, on)).toBe("redirect");
    expect(decideApiErrorAction(401, onlineNoFlag)).toBe("redirect");
    // Flag off ⇒ no local data to fall back on ⇒ legacy redirect even offline.
    expect(decideApiErrorAction(401, offlineNoFlag)).toBe("redirect");
  });

  it("a network-class failure (no status) offline with the flag on is suppressed", () => {
    expect(decideApiErrorAction(undefined, offlineFlag)).toBe("suppress");
  });

  it("a network-class failure toasts when online, or when the flag is off", () => {
    expect(decideApiErrorAction(undefined, on)).toBe("toast");
    expect(decideApiErrorAction(undefined, offlineNoFlag)).toBe("toast");
  });

  it("a real server error (defined, non-401 status) always toasts — even under the grace", () => {
    expect(decideApiErrorAction(500, offlineFlag)).toBe("toast");
    expect(decideApiErrorAction(404, offlineFlag)).toBe("toast");
    expect(decideApiErrorAction(403, on)).toBe("toast");
  });
});
