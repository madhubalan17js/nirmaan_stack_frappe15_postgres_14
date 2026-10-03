/**
 * "Someone else changed this record" -- handled INSIDE an edit dialog, for any doctype.
 *
 * A save refused because someone saved the record first (TimestampMismatchError) no longer closes
 * the dialog. A banner says who changed the record and what changed; the form is refilled with the
 * LATEST saved version (`takeLatest`) and the user enters again only what they still need; the next
 * save carries the LATEST `modified`. So "Save again" can never write a stale copy over someone
 * else's change. Other save errors are left to the dialog.
 *
 * `opened` is the version the form currently reflects: the record on the first conflict, the
 * previous conflict's latest version after that. The banner lists what changed since THAT version.
 *
 * No per-doctype setup: the changed fields are found by comparing the version the form was opened
 * on with the latest one, field by field. A dialog wires it in with four lines, given the pure
 * `formFrom(record)` it already uses to fill its form:
 *
 *   const stale = useStaleConflict({ doctype, record, open });   // the hook
 *   ...{ ...changes, ...stale.guard() }                          // in the save payload
 *   if (await stale.handle(error, (latest, opened) => setForm(f => takeLatest(f, formFrom(opened), formFrom(latest))))) return;
 *   <StaleConflictBanner conflict={stale.conflict} />             // in the dialog body
 *
 * After a refusal the lists and pages behind the dialog still show the old values (only a
 * SUCCESSFUL save refreshes them). `handle` therefore also asks them to re-fetch: every
 * `useServerDataTable` list of this doctype (via `RECORD_CHANGED_EVENT`) and every SWR cache
 * entry whose key names the doctype (the SDK's default getDoc / getDocList keys do). A list
 * with a custom SWR key that does not name the doctype passes its own `onRefresh`.
 * A dialog that refills its form when its record re-fetches must skip that while
 * `stale.conflict` is set, or the refresh would wipe what the user typed.
 *
 * ⚠️ KNOWN GAP: this refresh reaches ONLY the tab whose save was refused (a `window` event).
 * Anyone else just viewing the list stays stale until they reload, because frappe-react-sdk's
 * live-event hooks drop other components' `list_update` handlers (pre-existing SDK bug, not fixed
 * here). Still safe: their own save would be refused. Details and the other gaps (a direct DB
 * update is invisible; child-table records are not covered):
 * `.claude/context/domain/concurrent-edit.md` → "Known gaps".
 */
import { AlertTriangle } from "lucide-react";
import { FrappeConfig, FrappeContext, useSWRConfig } from "frappe-react-sdk";
import { useCallback, useContext, useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { describeWriteError, fetchStaleInfo, isStaleRecordError, StaleInfo, staleGuard } from "@/utils/frappeErrors";

export interface StaleConflict {
    /** "Updated by Nitesh Kumar at 01 Oct, 06:07 PM, after you opened it." */
    headline: string;
    /** The labels of every field that differs -- filled, changed or cleared: ["Amount", "Invoice ref"]. */
    changes: string[];
    /** The latest version's `modified`: the next save is applied on top of it. */
    modified?: string;
    /** The latest version could not be read, so the form still shows the version it was opened on. */
    unloaded?: boolean;
}

type AnyRecord = { name: string; modified?: string | null } & Record<string, any>;

/** Frappe's own bookkeeping fields: never something a user changed. */
const SYSTEM_FIELDS = new Set(["name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "doctype"]);

const DATE = /^\d{4}-\d{2}-\d{2}/;

const isBlank = (v: unknown) => v === null || v === undefined || String(v).trim() === "";

/** "payment_ref" -> "Payment ref" */
const labelOf = (key: string) => key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

const sameValue = (a: unknown, b: unknown) => {
    if (isBlank(a) && isBlank(b)) return true;
    if (isBlank(a) || isBlank(b)) return false;
    const na = Number(a);
    const nb = Number(b);
    if (!Number.isNaN(na) && !Number.isNaN(nb)) return na === nb;
    const sa = String(a).trim();
    const sb = String(b).trim();
    return DATE.test(sa) && DATE.test(sb) ? sa.slice(0, 10) === sb.slice(0, 10) : sa === sb;
};

/**
 * The NAMES of the fields that differ between the version opened and the latest one, e.g.
 * ["Invoice date", "Invoice ref", "Invoice attachment"]. PURE. Names only (owner 2026-10-01): the
 * form below already shows the new values. Skips system fields, private (`_`) fields, child tables
 * and fields the screen never loaded (a list query fetches only some columns). Each name is the
 * form's own label when the server sent it (`labels`, e.g. "UTR"), else the tidied fieldname.
 */
export const changedFields = (
    opened: Record<string, any>,
    latest: Record<string, any>,
    labels: Record<string, string> = {}
): string[] =>
    Object.keys(latest).flatMap((key) => {
        if (SYSTEM_FIELDS.has(key) || key.startsWith("_")) return [];
        if (!(key in opened)) return [];
        const after = latest[key];
        if (typeof after === "object" && after !== null) return []; // child tables / JSON
        return sameValue(opened[key], after) ? [] : [labels[key] || labelOf(key)];
    });

/**
 * The form to show after a conflict: the LATEST saved version, every field (owner, 2026-10-01).
 * PURE. What the user had typed is not kept -- the banner lists what the other person changed, and
 * the user enters again only what they still need on top of the latest data. `latest` is the same
 * record-to-form mapping the dialog uses to fill itself, so the keys and formats line up; fields
 * the mapping does not cover keep their current value.
 */
export const takeLatest = <F extends Record<string, any>>(current: F, _opened: Partial<F>, latest: Partial<F>): F =>
    ({ ...current, ...latest });

/**
 * The toast text for a failed save, naming who changed the record when the save was refused as
 * stale. For the one-click actions that toast and reload instead of keeping a dialog open.
 */
export const useWriteErrorMessage = () => {
    const { call } = useContext(FrappeContext) as FrappeConfig;
    return useCallback(
        async (error: unknown, fallback: string, doctype: string, name: string): Promise<string> =>
            isStaleRecordError(error) ? (await fetchStaleInfo(call, doctype, name)).message : describeWriteError(error, fallback),
        [call]
    );
};

/**
 * After a conflict: the attachment an edit dialog should now show. PURE. Like every other field
 * (`takeLatest`), the attachment shows the LATEST saved version and the user's own picked file is
 * dropped (the caller clears it) -- whether or not the other person touched the attachment. The
 * user then keeps it, replaces it (picks their file again) or removes it with the usual controls.
 */
export const followAttachment = (
    _opened: string | null | undefined,
    latest: string | null | undefined
): { url: string | undefined; action: "keep" } => ({ url: latest || undefined, action: "keep" });

/**
 * The banner's first line. PURE. Names only the LAST saver -- with 3+ users the listed changes may
 * be several people's, so it never says "<name> changed this record" as if they made them all.
 */
export const conflictHeadline = (info: StaleInfo): string => {
    if (info.by && info.at) {
        return info.self
            ? `You updated this record in another tab or window at ${info.at}.`
            : `Updated by ${info.by} at ${info.at}, after you opened it.`;
    }
    // No structured fields (older server): keep who and when from the sentence.
    return info.message.replace(/,?\s*after you opened it\..*$/, ".");
};

/**
 * One refusal, worked out. PURE. `base` is the version the form currently reflects (null = the
 * record itself). Returns the banner, the version the merge is against (`opened`), and the next
 * base: the latest version when it could be read, else the base unchanged.
 */
export const resolveConflict = (
    record: AnyRecord,
    base: AnyRecord | null,
    latest: AnyRecord | null,
    info: StaleInfo
): { conflict: StaleConflict; opened: AnyRecord; nextBase: AnyRecord | null } => {
    const opened = base ?? record;
    return {
        conflict: {
            headline: conflictHeadline(info),
            changes: latest ? changedFields(opened, latest, info.labels) : [],
            // The record could not be re-read: the message endpoint's version still lets
            // "Save again" through, instead of re-sending the refused one forever.
            modified: latest?.modified ?? info.modified,
            unloaded: !latest,
        },
        opened,
        nextBase: latest ?? base,
    };
};

/** Window event asking every `useServerDataTable` list of `detail.doctype` to re-fetch. */
export const RECORD_CHANGED_EVENT = "nirmaan:record-changed";

interface Options {
    doctype: string;
    /** The record the form was loaded from. */
    record: AnyRecord | null | undefined;
    /** Whether the dialog is open; closing it forgets the conflict. */
    open: boolean;
    /** Extra refresh for a list whose SWR key does not name the doctype (e.g. commission keys). */
    onRefresh?: () => void;
}

/**
 * A comparable fingerprint of a form. PURE. A picked File compares by name, size and date; an
 * empty value (null / undefined / "") compares equal to any other empty value, so typing
 * something and deleting it again counts as no change.
 */
export const formSignature = (value: unknown): string =>
    JSON.stringify(value ?? null, (_key, v) => {
        if (typeof File !== "undefined" && v instanceof File) return { file: v.name, size: v.size, at: v.lastModified };
        if (v === undefined || v === null || v === "") return "";
        return v;
    });

/**
 * The record AS THE FORM LOADED IT, held for as long as the dialog stays open on that record.
 * PURE. Returns `prev` unchanged (same identity) unless the dialog closed or moved to another
 * record, so it is safe as an effect dependency.
 *
 * ⚠️ LOAD-BEARING: the `record` a dialog receives can be REPLACED by a newer version while the
 * form still shows the old values -- a live list update, an SWR re-fetch on window focus, a
 * re-fetch after someone else's refusal. Guarding a save with THAT version tells the server "I
 * saw the latest" when the user never did, and the save silently overwrites the other person's
 * change. The guard must carry the version the form was filled from: this one.
 */
export const pinOpenedRecord = <R extends { name?: string }>(
    prev: R | null,
    open: boolean,
    record: R | null | undefined
): R | null => {
    if (!open) return null;
    if (!record) return prev;
    if (!prev || prev.name !== record.name) return record;
    return prev;
};

export const useStaleConflict = ({ doctype, record: liveRecord, open, onRefresh }: Options) => {
    // Every use below reads the PINNED record, never the live prop (see `pinOpenedRecord`).
    const pinnedRef = useRef<AnyRecord | null>(null);
    pinnedRef.current = pinOpenedRecord(pinnedRef.current, open, liveRecord);
    const record = pinnedRef.current ?? liveRecord;

    const [conflict, setConflict] = useState<StaleConflict | null>(null);
    // The form as the warning left it (taken on the first `isSaveBlocked` call for that warning).
    const afterWarningRef = useRef<{ conflict: StaleConflict; signature: string } | null>(null);
    const { mutate } = useSWRConfig();
    const { call } = useContext(FrappeContext) as FrappeConfig;

    // The version the form currently reflects: null = the record itself; after a conflict, that
    // conflict's latest version. A ref -- it moves inside `handle` and is read there only.
    const baseRef = useRef<AnyRecord | null>(null);

    // Read inside `handle` only; a ref so an inline callback never re-creates it.
    const onRefreshRef = useRef(onRefresh);
    onRefreshRef.current = onRefresh;

    const name = record?.name;
    useEffect(() => {
        setConflict(null);
        baseRef.current = null;
        afterWarningRef.current = null;
    }, [open, name]);

    /**
     * For the "Save again" button's `disabled`: true while the warning is showing and nothing in
     * the form has changed since -- saving the latest data back unchanged would do nothing. Pass
     * everything the form holds (fields, picked file, attachment action, section toggles). The
     * first call after a warning takes the "as the warning left it" photo: `onLatest` refilled the
     * form in the same render batch as the warning, so that render already shows the latest data.
     * Never blocks when the latest could not be loaded: the form then still holds the user's own
     * work, which is exactly what they need to save.
     *
     * ⚠️ CALL IT FIRST, UNCONDITIONALLY: `disabled={stale.isSaveBlocked(form) || other}`, never
     * `other || stale.isSaveBlocked(form)`. Behind `||` it is skipped while `other` is true -- e.g. a
     * required field the latest data left empty -- so the photo is taken later, when the user has
     * already re-entered their change, and that change then counts as "unchanged" forever.
     * `useStaleConflict.test.ts` scans the screens for the wrong order.
     */
    const isSaveBlocked = (form: unknown): boolean => {
        if (!conflict || conflict.unloaded) return false;
        const signature = formSignature(form);
        if (afterWarningRef.current?.conflict !== conflict) afterWarningRef.current = { conflict, signature };
        return signature === afterWarningRef.current.signature;
    };

    /** Spread into the save payload. */
    const guard = useCallback(
        () => (conflict?.modified ? { modified: conflict.modified } : staleGuard(record)),
        [conflict, record]
    );

    /**
     * On a "someone else saved first" refusal: show the banner, hand the latest record and the
     * version the form was showing to `onLatest` (to refresh the untouched fields), and return
     * true -- keep the dialog open.
     */
    const handle = useCallback(
        async (error: unknown, onLatest?: (latest: AnyRecord, opened: AnyRecord) => void): Promise<boolean> => {
            if (!record || !isStaleRecordError(error)) return false;
            const [info, latest] = await Promise.all([
                fetchStaleInfo(call, doctype, record.name),
                // `frappe.client.get`, not the REST read: the REST read DROPS every null field, so a
                // field the other person cleared would be missing from `latest` -- never named in the
                // banner. This returns every field, nulls included, under the same read permission.
                call.get("frappe.client.get", { doctype, name: record.name })
                    .then((res: { message?: AnyRecord }) => res?.message ?? null)
                    .catch(() => null),
            ]);
            const next = resolveConflict(record, baseRef.current, latest, info);
            setConflict(next.conflict);
            baseRef.current = next.nextBase;
            if (latest) onLatest?.(latest, next.opened);
            // The lists / pages behind the dialog still show the old values: re-fetch them.
            window.dispatchEvent(new CustomEvent(RECORD_CHANGED_EVENT, { detail: { doctype } }));
            mutate((key) => String(key ?? "").includes(doctype));   // revalidate only, data kept
            onRefreshRef.current?.();
            return true;
        },
        [doctype, record, mutate, call]
    );

    /**
     * `opened`: the record the form was filled from (pinned while open). A dialog that refills its
     * form from its record must depend on `stale.opened`, not on the live record prop, or a live
     * update / focus re-fetch rewrites the form while the user is typing.
     */
    return { conflict, guard, handle, isSaveBlocked, opened: open ? record : null };
};

export const StaleConflictBanner = ({ conflict }: { conflict: StaleConflict | null }) => {
    if (!conflict) return null;
    return (
        <Alert variant="warning" className="text-sm">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{conflict.headline}</AlertTitle>
            <AlertDescription className="space-y-1">
                {conflict.changes.length > 0 && <p>Changed: {conflict.changes.join(", ")}</p>}
                <p>
                    {conflict.unloaded
                        ? "Their changes could not be loaded. Save again saves this form as shown."
                        : "The form now shows the latest. Make your changes again to save."}
                </p>
            </AlertDescription>
        </Alert>
    );
};
