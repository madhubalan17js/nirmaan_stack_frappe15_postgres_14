import { useDocTypeEventListener } from "@/lib/frappe";

/** What Frappe sends with `list_update` (`frappe/model/document.py` notify_update): on insert, save and delete. */
export interface ListUpdateMessage {
    doctype: string;
    name: string;
    user: string;
}

/**
 * Runs `onUpdate` for every `list_update` of `doctype`: a document of it was inserted, saved or deleted.
 *
 * A thin layer over `useDocTypeEventListener` (`@/lib/frappe`, #1670), which owns the socket work: it removes
 * only its own handler, picks up a new `onUpdate` without touching the socket, and leaves the doctype's room only
 * when its last listener on this socket unmounts. `list_update` arrives for every doctype room this tab is in, so
 * this keeps only the events of `doctype`. `onUpdate` may change on every render.
 */
export const useListUpdate = (doctype: string, onUpdate: (message: ListUpdateMessage) => void) => {
    useDocTypeEventListener(doctype, (message) => {
        if (message?.doctype === doctype) onUpdate(message);
    });
};
