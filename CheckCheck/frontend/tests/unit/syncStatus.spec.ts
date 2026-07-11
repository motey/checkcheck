import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  beginSync,
  endSync,
  getSyncStatus,
  onSyncStatusChange,
  __resetSyncStatusForTests,
} from "@/utils/syncStatus";

// ── Sync activity signal (WI-14) ─────────────────────────────────────────────
// The framework-free state behind the global sync indicator: a "syncing" flag
// the delta pull toggles, and a last-synced clock that advances only on a
// server-reaching pull.

describe("syncStatus", () => {
  beforeEach(() => {
    __resetSyncStatusForTests();
  });

  it("beginSync flips syncing on and notifies once (idempotent)", () => {
    const cb = vi.fn();
    onSyncStatusChange(cb);
    beginSync();
    expect(getSyncStatus().syncing).toBe(true);
    expect(cb).toHaveBeenCalledTimes(1);
    beginSync(); // already syncing — no second notify
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("endSync(true) clears syncing and advances lastSyncedAt", () => {
    beginSync();
    expect(getSyncStatus().lastSyncedAt).toBeNull();
    const before = Date.now();
    endSync(true);
    const s = getSyncStatus();
    expect(s.syncing).toBe(false);
    expect(s.lastSyncedAt).not.toBeNull();
    expect(s.lastSyncedAt!).toBeGreaterThanOrEqual(before);
  });

  it("endSync(false) clears syncing but does NOT advance lastSyncedAt", () => {
    beginSync();
    endSync(false);
    expect(getSyncStatus()).toEqual({ syncing: false, lastSyncedAt: null });
  });

  it("endSync(false) with no in-flight sync is a quiet no-op", () => {
    const cb = vi.fn();
    onSyncStatusChange(cb);
    endSync(false);
    expect(cb).not.toHaveBeenCalled();
    expect(getSyncStatus().syncing).toBe(false);
  });

  it("unsubscribe stops notifications", () => {
    const cb = vi.fn();
    const off = onSyncStatusChange(cb);
    off();
    beginSync();
    expect(cb).not.toHaveBeenCalled();
  });
});

describe("syncStatus persistence", () => {
  afterEach(() => {
    delete (globalThis as { localStorage?: unknown }).localStorage;
    __resetSyncStatusForTests();
  });

  it("persists lastSyncedAt to localStorage when available", () => {
    const store = new Map<string, string>();
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    };
    __resetSyncStatusForTests();
    beginSync();
    endSync(true);
    expect(store.get("checkcheck:lastSyncedAt")).toBeTruthy();
  });

  it("survives a localStorage.setItem that throws (quota)", () => {
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceeded");
      },
      removeItem: () => {},
    };
    __resetSyncStatusForTests();
    beginSync();
    expect(() => endSync(true)).not.toThrow();
    expect(getSyncStatus().lastSyncedAt).not.toBeNull(); // in-memory value still set
  });
});
