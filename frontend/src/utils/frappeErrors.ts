/**
 * Utility to extract a human-readable error message from a Frappe error object.
 * Handles _server_messages, exception, and basic message properties.
 */
export const getFrappeError = (error: any): string => {
    if (!error) return "An unknown error occurred.";

    // 1. Check for _server_messages (often a stringified JSON array of stringified JSON objects)
    if (error._server_messages) {
        try {
            const messages = typeof error._server_messages === 'string' 
                ? JSON.parse(error._server_messages) 
                : error._server_messages;

            if (Array.isArray(messages)) {
                return messages.map((m: any) => {
                    if (typeof m === 'string') {
                        try {
                            const parsed = JSON.parse(m);
                            return parsed.message || m;
                        } catch {
                            return m;
                        }
                    }
                    return m.message || JSON.stringify(m);
                }).join(", ");
            }
        } catch (e) {
            console.error("Error parsing _server_messages:", e);
        }
    }

    // 2. Check for exception property
    if (error.exception) {
        // Often looks like "frappe.exceptions.ValidationError: Specific Message"
        const parts = error.exception.split(':');
        if (parts.length > 1) {
            return parts.slice(1).join(':').trim();
        }
        return error.exception;
    }

    // 3. Fallback to basic message or toString
    return error.message || error.toString() || "Something went wrong.";
};

/**
 * Stale-save guard for a plain `updateDoc`. Sends back the `modified` the screen loaded, and
 * Frappe's own `check_if_latest` refuses the save (TimestampMismatchError) when someone else saved
 * the record in between — instead of the second save silently overwriting the first.
 * A record loaded without `modified` sends nothing, so the save goes through unguarded as before.
 */
export const staleGuard = (record?: { modified?: string | null } | null): { modified?: string } =>
    record?.modified ? { modified: record.modified } : {};

export const STALE_RECORD_MESSAGE =
    "Someone else changed this record after you opened it. Refresh and try again.";

/**
 * Someone else saved the record first. Two shapes: Frappe's own version check
 * (TimestampMismatchError), and -- when both saves land in the same second -- PostgreSQL refusing
 * the second one while it waited on the row lock (SerializationFailure, "could not serialize access
 * due to concurrent update"). The second only happens AFTER the other save committed, so the
 * latest data is there to show: both get the same warning instead of a generic "Failed!".
 */
export const isStaleRecordError = (error: any): boolean =>
    error?.exc_type === "TimestampMismatchError" ||
    error?.exc_type === "SerializationFailure" ||
    /TimestampMismatchError|could not serialize access due to concurrent update/.test(
        `${error?.exception ?? ""} ${error?.exc ?? ""}`
    );

/** The toast text for a failed save: the stale-record message, else the error's own. */
export const describeWriteError = (error: any, fallback: string): string => {
    if (isStaleRecordError(error)) return STALE_RECORD_MESSAGE;
    // The server's own reason (a frappe.throw) rides `_server_messages`; the SDK's `message` is often
    // only "There was an error." -- prefer the server's words when it sent any.
    if (error?._server_messages) return getFrappeError(error);
    return error?.message || fallback;
};

/** The server endpoint that words the stale-save message (`api/concurrent_edit/last_change`). */
export const STALE_MESSAGE_METHOD = "nirmaan_stack.api.concurrent_edit.last_change.get_stale_message";

/** What the server says about a record whose save was just refused as stale. */
export interface StaleInfo {
    /** "Ravi changed this record at 29 Sep, 10:02 AM, after you opened it. Refresh and try again." */
    message: string;
    /** The record's CURRENT `modified` -- a retry carries this, so it can never reuse the refused one. */
    modified?: string;
    /** The LAST saver's display name and save time. Only the last one: with 3+ users, earlier
     *  changes may be someone else's, so never present `by` as the author of every change. */
    by?: string;
    at?: string;
    /** The last save was the caller's own (another tab or window). */
    self?: boolean;
}

/** The SDK's `call` client (from `FrappeContext`) -- only its `get` is used. */
export interface FrappeCallClient {
    get: (method: string, params?: Record<string, unknown>) => Promise<any>;
}

/**
 * Who changed the record and its current version, through the SDK client (never a raw `fetch`).
 * Any failure falls back to the plain message with no version; callers then keep what they have.
 */
export const fetchStaleInfo = async (call: FrappeCallClient, doctype: string, name: string): Promise<StaleInfo> => {
    try {
        const res = await call.get(STALE_MESSAGE_METHOD, { doctype, name });
        const msg = res?.message;
        if (msg && typeof msg.message === "string" && msg.message) {
            return {
                message: msg.message,
                modified: typeof msg.modified === "string" ? msg.modified : undefined,
                by: typeof msg.by === "string" && msg.by ? msg.by : undefined,
                at: typeof msg.at === "string" && msg.at ? msg.at : undefined,
                self: msg.self === true,
            };
        }
    } catch {
        // fall through to the plain message
    }
    return { message: STALE_RECORD_MESSAGE };
};
