/**
 * Live socket events that survive other components mounting and unmounting.
 *
 * Why not the frappe-react-sdk hooks (1.17.0):
 *  - `useFrappeEventListener` cleans up with `socket.off(event)` and NO handler, which removes
 *    EVERY component's handler for that event in the tab — and it re-registers whenever its
 *    handler identity changes, so one list re-rendering silences every other list.
 *  - `useFrappeDocTypeEventListener` (= `list_update` + a room join) also sends
 *    `doctype_unsubscribe` when any one listener unmounts, taking the whole tab out of the room.
 *
 * Here:
 *  - Handlers are dispatched from ONE `socket.onAny` listener. `socket.off(event)` never touches
 *    `onAny` listeners, so the SDK hooks still used elsewhere cannot wipe ours.
 *  - Room membership is reference-counted per doctype; `doctype_unsubscribe` is sent only when
 *    the last holder leaves. Until every caller is moved off the SDK hooks, an SDK unsubscribe
 *    for a room we still hold is dropped (see `guardRoomUnsubscribe`).
 *
 * See `.claude/context/domain/concurrent-edit.md` (Known gap 1).
 */
import { useContext, useEffect, useRef } from "react";
import { FrappeConfig, FrappeContext } from "frappe-react-sdk";

type Handler = (data: any) => void;
type Socket = NonNullable<FrappeConfig["socket"]>;

// ---------------------------------------------------------------------------
// Pure helpers (unit-tested)
// ---------------------------------------------------------------------------

/** Add a holder for `key`. Returns true when this is the first holder (join the room now). */
export const joinRoom = (rooms: Map<string, number>, key: string): boolean => {
    const next = (rooms.get(key) ?? 0) + 1;
    rooms.set(key, next);
    return next === 1;
};

/** Remove a holder for `key`. Returns true when that was the last holder (leave the room now). */
export const leaveRoom = (rooms: Map<string, number>, key: string): boolean => {
    const current = rooms.get(key) ?? 0;
    if (current <= 1) {
        rooms.delete(key);
        return current === 1;
    }
    rooms.set(key, current - 1);
    return false;
};

/**
 * Throttle state for a live re-fetch: at most one run per `intervalMs`, the last event in a
 * burst always lands (trailing run), and nothing runs while the tab is hidden — it is marked
 * pending and caught up when the tab becomes visible.
 */
export type LiveRefreshDecision = "run-now" | "schedule" | "mark-pending";

export const decideLiveRefresh = (opts: {
    hidden: boolean;
    now: number;
    lastRunAt: number;
    intervalMs: number;
}): LiveRefreshDecision => {
    if (opts.hidden) return "mark-pending";
    return opts.now - opts.lastRunAt >= opts.intervalMs ? "run-now" : "schedule";
};

/** On becoming visible: catch up when an event arrived while hidden, or the data is old. */
export const shouldRefreshOnVisible = (opts: {
    pending: boolean;
    now: number;
    lastFetchedAt: number;
    staleAfterMs: number;
}): boolean => opts.pending || (opts.lastFetchedAt > 0 && opts.now - opts.lastFetchedAt >= opts.staleAfterMs);

// ---------------------------------------------------------------------------
// One dispatcher per socket
// ---------------------------------------------------------------------------

const handlersBySocket = new WeakMap<Socket, Map<string, Set<{ current: Handler }>>>();

const handlersFor = (socket: Socket) => {
    let byEvent = handlersBySocket.get(socket);
    if (!byEvent) {
        const created = new Map<string, Set<{ current: Handler }>>();
        handlersBySocket.set(socket, created);
        socket.onAny((event: string, data: any) => {
            created.get(event)?.forEach((ref) => {
                try {
                    ref.current(data);
                } catch (err) {
                    console.error(`[useRealtimeEvent] handler for "${event}" failed`, err);
                }
            });
        });
        byEvent = created;
    }
    return byEvent;
};

/**
 * Listen to one socket event for the component's lifetime. The handler may change on every
 * render; the latest one is called and nothing is re-registered.
 */
export const useRealtimeEvent = (event: string | null | undefined, handler: Handler) => {
    const { socket } = useContext(FrappeContext) as FrappeConfig;
    const handlerRef = useRef<Handler>(handler);
    handlerRef.current = handler;

    useEffect(() => {
        if (!socket || !event) return;
        const byEvent = handlersFor(socket);
        let set = byEvent.get(event);
        if (!set) {
            set = new Set();
            byEvent.set(event, set);
        }
        set.add(handlerRef);
        return () => {
            set!.delete(handlerRef);
        };
    }, [socket, event]);
};

// ---------------------------------------------------------------------------
// Doctype rooms (list_update is only delivered to sockets in `doctype:<name>`)
// ---------------------------------------------------------------------------

const doctypeRooms = new Map<string, number>();
const guardedSockets = new WeakSet<Socket>();

/**
 * TEMPORARY — remove once no file imports `useFrappeDocTypeEventListener`: the SDK hook sends
 * `doctype_unsubscribe` on unmount even when another component still needs the room. Drop that
 * message while our count for the doctype is above zero.
 */
const guardRoomUnsubscribe = (socket: Socket) => {
    if (guardedSockets.has(socket)) return;
    guardedSockets.add(socket);
    const emit = socket.emit.bind(socket);
    (socket as any).emit = (event: string, ...args: any[]) => {
        if (event === "doctype_unsubscribe" && (doctypeRooms.get(args[0]) ?? 0) > 0) return socket;
        return emit(event, ...args);
    };
};

const useDoctypeRoom = (doctype: string | null | undefined) => {
    const { socket } = useContext(FrappeContext) as FrappeConfig;

    useEffect(() => {
        if (!socket || !doctype) return;
        guardRoomUnsubscribe(socket);
        if (joinRoom(doctypeRooms, doctype)) socket.emit("doctype_subscribe", doctype);
        // A reconnect gets a new server-side socket with no rooms: join again.
        const onReconnect = () => socket.emit("doctype_subscribe", doctype);
        socket.io.on("reconnect", onReconnect);
        return () => {
            socket.io.off("reconnect", onReconnect);
            if (leaveRoom(doctypeRooms, doctype)) socket.emit("doctype_unsubscribe", doctype);
        };
    }, [socket, doctype]);
};

/**
 * Replacement for `useFrappeDocTypeEventListener`: calls `handler` with Frappe's `list_update`
 * payload whenever a record of `doctype` is created, saved or deleted by anyone.
 */
export const useDoctypeListUpdates = (doctype: string | null | undefined, handler: Handler) => {
    useDoctypeRoom(doctype);
    const handlerRef = useRef<Handler>(handler);
    handlerRef.current = handler;
    useRealtimeEvent(doctype ? "list_update" : null, (data: any) => {
        if (data?.doctype === doctype) handlerRef.current(data);
    });
};
