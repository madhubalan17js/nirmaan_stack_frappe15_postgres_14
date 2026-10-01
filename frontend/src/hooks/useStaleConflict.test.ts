import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { fetchStaleInfo, isStaleRecordError, STALE_MESSAGE_METHOD, STALE_RECORD_MESSAGE } from "@/utils/frappeErrors";

import { changedFields, conflictHeadline, followAttachment, formSignature, resolveConflict, takeLatest } from "./useStaleConflict";

const base = {
    name: "EXP-1",
    modified: "2026-09-29 12:00:00.000000",
    status: "Requested",
    description: "Tea for site team",
    comment: null,
    amount: 20000,
    invoice_date: "2026-09-20",
    invoice_attachment: null,
    payment_attachment: "/files/old.pdf",
};

describe("changedFields (the banner names WHICH fields changed; the form shows the new values)", () => {
    it("names nothing when only Frappe's own bookkeeping changed", () => {
        const latest = { ...base, modified: "2026-09-29 12:40:00.000000", modified_by: "nitesh@nirmaan.app", idx: 3 };
        expect(changedFields(base, latest)).toEqual([]);
    });

    it("names a changed field by its label", () => {
        expect(changedFields(base, { ...base, amount: 45000 })).toEqual(["Amount"]);
    });

    it("treats a number stored as text and as a number as the same value", () => {
        expect(changedFields(base, { ...base, amount: "20000" })).toEqual([]);
    });

    it("names a field that was empty and is now filled, and one the other person cleared", () => {
        expect(changedFields(base, { ...base, comment: "Checked by site" })).toEqual(["Comment"]);
        expect(changedFields({ ...base, invoice_date: "2026-09-25" }, { ...base, invoice_date: null })).toEqual(["Invoice date"]);
    });

    it("treats null, undefined and empty text as the same empty value", () => {
        expect(changedFields(base, { ...base, comment: "" })).toEqual([]);
        expect(changedFields(base, { ...base, comment: undefined })).toEqual([]);
    });

    it("compares dates on the day, ignoring a time part", () => {
        expect(changedFields(base, { ...base, invoice_date: "2026-09-20 00:00:00" })).toEqual([]);
    });

    it("names an attachment that was added, removed or replaced", () => {
        expect(changedFields(base, { ...base, invoice_attachment: "/files/inv.pdf" })).toEqual(["Invoice attachment"]);
        expect(changedFields(base, { ...base, payment_attachment: null })).toEqual(["Payment attachment"]);
    });

    it("ignores fields the screen never loaded (a list fetches only some columns)", () => {
        expect(changedFields(base, { ...base, autofill_used: 0, autofill_extracted_amount: 0 })).toEqual([]);
    });

    it("skips child tables and private fields", () => {
        const latest = { ...base, items: [{ qty: 2 }], _comments: "[1]", _liked_by: "x" };
        expect(changedFields({ ...base, items: [{ qty: 1 }] }, latest)).toEqual([]);
    });

    it("names a field by the form's own label when the server sent one", () => {
        expect(changedFields(base, { ...base, amount: 45000, comment: "x" }, { amount: "Req. Amount" })).toEqual(["Comment", "Req. Amount"]);
    });

    it("lists several changes in the record's field order", () => {
        const latest = { ...base, status: "Approved", amount: 30000, description: "Tea and snacks" };
        expect(changedFields(base, latest)).toEqual(["Status", "Description", "Amount"]);
    });
});

describe("takeLatest (after a conflict the form shows the latest saved details; owner 2026-10-01)", () => {
    const opened = { description: "Tea", amount: "20000", comment: "" };

    it("takes the other person's value for a field the user did not touch", () => {
        const current = { ...opened, description: "Tea and snacks" };
        const latest = { ...opened, amount: "45000", comment: "Per bill" };
        expect(takeLatest(current, opened, latest)).toEqual({ description: "Tea", amount: "45000", comment: "Per bill" });
    });

    it("does NOT keep the user's typing: the latest saved value wins, the user enters it again", () => {
        // Retired rule: the typed 30000 used to be kept and Save again wrote it over 45000.
        const current = { ...opened, amount: "30000" };
        const latest = { ...opened, amount: "45000" };
        expect(takeLatest(current, opened, latest).amount).toBe("45000");
    });

    it("leaves fields outside the given subset alone", () => {
        const current = { ...opened, project_name: "Display only" };
        expect(takeLatest(current, { amount: "20000" }, { amount: "45000" })).toEqual({ ...current, amount: "45000" });
    });
});

describe("resolveConflict (a second conflict merges against the first one's version)", () => {
    const record = { name: "ASSET-1", modified: "v0", asset_name: "Laptop", description: "" };
    const info = { message: "Priyanka changed this record at 01 Oct, 10:00 AM, after you opened it. Refresh and try again.", modified: "v-info" };

    it("first conflict: merges against the record and moves the base to the latest", () => {
        const p1 = { ...record, modified: "v1", description: "P1" };
        const r = resolveConflict(record, null, p1, info);
        expect(r.opened).toBe(record);
        expect(r.nextBase).toBe(p1);
        expect(r.conflict.changes).toEqual(["Description"]);
        expect(r.conflict.modified).toBe("v1");
        expect(r.conflict.headline).toBe("Priyanka changed this record at 01 Oct, 10:00 AM.");
    });

    it("second conflict: the banner lists only what changed since the first conflict (P1 → P2), the form shows P2", () => {
        const p1 = { ...record, modified: "v1", description: "P1" };
        const p2 = { ...record, modified: "v2", description: "P2" };
        const first = resolveConflict(record, null, p1, info);
        const second = resolveConflict(record, first.nextBase, p2, info);
        expect(second.opened).toBe(p1);
        expect(second.conflict.changes).toEqual(["Description"]);
        const pick = (r: typeof record) => ({ asset_name: r.asset_name, description: r.description });
        // whatever was in the form, it now shows the latest saved version
        expect(takeLatest({ asset_name: "Laptop (typed)", description: "P1" }, pick(p1), pick(p2))).toEqual({ asset_name: "Laptop", description: "P2" });
    });

    it("latest could not be read: the version from the message lets Save again through, the base stays", () => {
        const r = resolveConflict(record, null, null, info);
        expect(r.conflict.modified).toBe("v-info");
        expect(r.conflict.unloaded).toBe(true);
        expect(r.conflict.changes).toEqual([]);
        expect(r.nextBase).toBeNull();
    });

    it("nothing could be read: no version, so Save again keeps the guard it had", () => {
        const r = resolveConflict(record, null, null, { message: STALE_RECORD_MESSAGE });
        expect(r.conflict.modified).toBeUndefined();
        expect(r.conflict.headline).toBe("Someone else changed this record.");
    });
});

describe("fetchStaleInfo (through the SDK client, never a raw fetch)", () => {
    it("passes the form's field labels through, and resolveConflict names fields with them", async () => {
        const call = { get: async () => ({ message: { message: "x", modified: "m", by: "Priyanka", at: "01 Oct, 07:00 PM", labels: { utr: "UTR" } } }) };
        const info = await fetchStaleInfo(call, "Project Inflows", "PAYIN-1");
        expect(info.labels).toEqual({ utr: "UTR" });
        const record = { name: "PAYIN-1", modified: "a", utr: "OLD" };
        expect(resolveConflict(record, null, { ...record, modified: "m", utr: "NEW" }, info).conflict.changes).toEqual(["UTR"]);
    });

    it("calls the concurrent_edit endpoint and returns who changed it plus the current version", async () => {
        const calls: unknown[] = [];
        const call = { get: async (method: string, params?: Record<string, unknown>) => {
            calls.push([method, params]);
            return { message: { message: "Nitesh changed this record.", modified: "2026-10-01 10:00:00" } };
        } };
        expect(await fetchStaleInfo(call, "Asset Master", "ASSET-1")).toEqual({ message: "Nitesh changed this record.", modified: "2026-10-01 10:00:00", self: false });
        expect(calls).toEqual([[STALE_MESSAGE_METHOD, { doctype: "Asset Master", name: "ASSET-1" }]]);
        expect(STALE_MESSAGE_METHOD).toBe("nirmaan_stack.api.concurrent_edit.last_change.get_stale_message");
    });

    it("falls back to the plain message, with no version, when the call fails or says nothing", async () => {
        const failing = { get: async () => { throw new Error("offline"); } };
        expect(await fetchStaleInfo(failing, "Asset Master", "ASSET-1")).toEqual({ message: STALE_RECORD_MESSAGE });
        const empty = { get: async () => ({ message: {} }) };
        expect(await fetchStaleInfo(empty, "Asset Master", "ASSET-1")).toEqual({ message: STALE_RECORD_MESSAGE });
    });
});

describe("followAttachment (Save again never replaces an attachment the user has not seen)", () => {
    it("takes the other person's new attachment", () => {
        expect(followAttachment(null, "/private/files/proof.pdf")).toEqual({ url: "/private/files/proof.pdf", action: "keep" });
    });

    it("takes their replacement and their removal the same way", () => {
        expect(followAttachment("/files/a.pdf", "/files/b.pdf")).toEqual({ url: "/files/b.pdf", action: "keep" });
        expect(followAttachment("/files/a.pdf", null)).toEqual({ url: undefined, action: "keep" });
    });

    it("shows the latest attachment even when the other person did not touch it (the user's pick is dropped)", () => {
        // Retired rule: an unchanged attachment returned null and the user's picked file survived.
        expect(followAttachment("/files/a.pdf", "/files/a.pdf")).toEqual({ url: "/files/a.pdf", action: "keep" });
        expect(followAttachment(null, "")).toEqual({ url: undefined, action: "keep" });
    });

    it("has no 'the user picked a file' escape any more: theirs is always shown (caller drops the pick)", () => {
        // Retired rule (2026-10-01, owner): a picked file used to win silently, so a user replaced a
        // file they never saw. The helper no longer takes a pick flag at all.
        expect(followAttachment.length).toBe(2);
    });
});

describe("formSignature (Save again stays off until the user changes something after the warning)", () => {
    const afterWarning = { formState: { utr: "UTR-1", amount: "100", comment: "" }, newFile: null, action: "keep" };

    it("is the same for the same form", () => {
        expect(formSignature({ ...afterWarning })).toBe(formSignature(afterWarning));
    });

    it("changes when a field changes", () => {
        expect(formSignature({ ...afterWarning, formState: { ...afterWarning.formState, utr: "UTR-2" } })).not.toBe(formSignature(afterWarning));
    });

    it("typing something and deleting it again counts as no change (empty, null and undefined are equal)", () => {
        expect(formSignature({ ...afterWarning, formState: { ...afterWarning.formState, comment: null } })).toBe(formSignature(afterWarning));
        expect(formSignature({ ...afterWarning, formState: { ...afterWarning.formState, comment: undefined } })).toBe(formSignature(afterWarning));
    });

    it("changes when a file is picked or the attachment action changes", () => {
        const file = new File(["x"], "proof.pdf", { type: "application/pdf", lastModified: 1 });
        expect(formSignature({ ...afterWarning, newFile: file })).not.toBe(formSignature(afterWarning));
        expect(formSignature({ ...afterWarning, action: "remove" })).not.toBe(formSignature(afterWarning));
    });

    it("compares a picked file by name, size and date", () => {
        const a = new File(["x"], "proof.pdf", { lastModified: 1 });
        const b = new File(["x"], "proof.pdf", { lastModified: 1 });
        const c = new File(["x"], "other.pdf", { lastModified: 1 });
        expect(formSignature({ f: a })).toBe(formSignature({ f: b }));
        expect(formSignature({ f: a })).not.toBe(formSignature({ f: c }));
    });
});

describe("isStaleRecordError (someone else saved first -> the warning, never a generic 'Failed!')", () => {
    it("recognises Frappe's own version refusal", () => {
        expect(isStaleRecordError({ exc_type: "TimestampMismatchError" })).toBe(true);
        expect(isStaleRecordError({ exception: "frappe.exceptions.TimestampMismatchError: Document has been modified" })).toBe(true);
    });

    it("recognises the same-second database refusal (both saves in the same second)", () => {
        expect(isStaleRecordError({ exc_type: "SerializationFailure" })).toBe(true);
        expect(isStaleRecordError({ exc: "psycopg2.errors.SerializationFailure: could not serialize access due to concurrent update" })).toBe(true);
    });

    it("leaves every other error alone", () => {
        expect(isStaleRecordError({ exc_type: "ValidationError" })).toBe(false);
        expect(isStaleRecordError(null)).toBe(false);
    });
});

describe("conflictHeadline (names only the LAST saver; with 3+ users the changes may be several people's)", () => {
    it("says the record changed and who saved it last", () => {
        expect(conflictHeadline({ message: "x", by: "Priyanka Sharma", at: "01 Oct, 04:10 PM" }))
            .toBe("Updated by Priyanka Sharma at 01 Oct, 04:10 PM, after you opened it.");
    });

    it("says so when the last save was the user's own, in another tab", () => {
        expect(conflictHeadline({ message: "x", by: "Nitesh Kumar", at: "01 Oct, 04:10 PM", self: true }))
            .toBe("You updated this record in another tab or window at 01 Oct, 04:10 PM.");
    });

    it("falls back to the server sentence when the structured fields are missing", () => {
        expect(conflictHeadline({ message: "Priyanka changed this record at 01 Oct, 10:00 AM, after you opened it. Refresh and try again." }))
            .toBe("Priyanka changed this record at 01 Oct, 10:00 AM.");
    });
});

describe("Save again buttons call isSaveBlocked FIRST (its photo must be taken at the warning)", () => {
    // Only the folders that hold screens; read once and shared by both checks (the tree is large).
    const srcDir = join(fileURLToPath(new URL(".", import.meta.url)), "..");
    const files = (dir: string): string[] =>
        readdirSync(dir).flatMap((entry) => {
            const path = join(dir, entry);
            if (statSync(path).isDirectory()) return files(path);
            return entry.endsWith(".tsx") ? [path] : [];
        });
    let sources: Array<{ path: string; text: string }> | null = null;
    const screens = () =>
        (sources ??= ["pages", "components"]
            .flatMap((dir) => files(join(srcDir, dir)))
            .map((path) => ({ path: path.slice(srcDir.length + 1), text: readFileSync(path, "utf8") }))
            .filter(({ text }) => text.includes("isSaveBlocked")));

    it("no screen puts another condition in front of stale.isSaveBlocked(...)", () => {
        expect(screens().filter(({ text }) => /\|\|\s*stale\.isSaveBlocked\(/.test(text)).map(({ path }) => path)).toEqual([]);
    }, 60_000);

    it("the scan sees the real call sites (guards against a scan that finds nothing)", () => {
        expect(screens().length).toBeGreaterThanOrEqual(18);
    }, 60_000);
});
