import { describe, expect, it } from "vitest";
import { decideLiveRefresh, joinRoom, leaveRoom, shouldRefreshOnVisible } from "./useRealtimeEvent";

describe("room reference count", () => {
    it("joins on the first holder only and leaves on the last holder only", () => {
        const rooms = new Map<string, number>();
        expect(joinRoom(rooms, "Project Payments")).toBe(true);
        expect(joinRoom(rooms, "Project Payments")).toBe(false);
        // One of two holders unmounts: the room must be kept (the SDK hook left it here).
        expect(leaveRoom(rooms, "Project Payments")).toBe(false);
        expect(rooms.get("Project Payments")).toBe(1);
        expect(leaveRoom(rooms, "Project Payments")).toBe(true);
        expect(rooms.has("Project Payments")).toBe(false);
    });

    it("counts each doctype separately", () => {
        const rooms = new Map<string, number>();
        joinRoom(rooms, "Project Inflows");
        expect(joinRoom(rooms, "Project Expenses")).toBe(true);
        expect(leaveRoom(rooms, "Project Inflows")).toBe(true);
        expect(rooms.get("Project Expenses")).toBe(1);
    });

    it("never goes negative or asks to leave a room it never joined", () => {
        const rooms = new Map<string, number>();
        expect(leaveRoom(rooms, "Vendors")).toBe(false);
        expect(rooms.has("Vendors")).toBe(false);
        expect(joinRoom(rooms, "Vendors")).toBe(true);
    });
});

describe("decideLiveRefresh", () => {
    const base = { hidden: false, now: 10_000, lastRunAt: 0, intervalMs: 1500 };

    it("runs at once when the last run is older than the interval", () => {
        expect(decideLiveRefresh(base)).toBe("run-now");
    });

    it("schedules a trailing run inside the interval, so a burst collapses to one", () => {
        expect(decideLiveRefresh({ ...base, lastRunAt: 9_000 })).toBe("schedule");
    });

    it("only marks the change pending while the tab is hidden", () => {
        expect(decideLiveRefresh({ ...base, hidden: true })).toBe("mark-pending");
        expect(decideLiveRefresh({ ...base, hidden: true, lastRunAt: 9_000 })).toBe("mark-pending");
    });
});

describe("shouldRefreshOnVisible", () => {
    const base = { pending: false, now: 100_000, lastFetchedAt: 90_000, staleAfterMs: 30_000 };

    it("catches up on a change that arrived while the tab was hidden", () => {
        expect(shouldRefreshOnVisible({ ...base, pending: true })).toBe(true);
    });

    it("re-fetches old rows even without an event (a missed or dropped socket)", () => {
        expect(shouldRefreshOnVisible({ ...base, lastFetchedAt: 60_000 })).toBe(true);
    });

    it("leaves fresh rows alone", () => {
        expect(shouldRefreshOnVisible(base)).toBe(false);
    });

    it("does nothing before the first load has finished", () => {
        expect(shouldRefreshOnVisible({ ...base, lastFetchedAt: 0 })).toBe(false);
    });
});
