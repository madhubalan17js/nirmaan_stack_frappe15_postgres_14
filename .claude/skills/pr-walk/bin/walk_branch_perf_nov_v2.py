"""Walk branch perf/nov-v2 (perf/nov's 21 speed and table fixes rebased on develop, plus the useListUpdate refactor):
after = the branch's env, before = GitHub develop's env (the rebase base). There is no PR; the claims are the commits.

    python3 walk_branch_perf_nov_v2.py <out dir> [function ...]     # from the checkout root; envs served

Not `python3 -I`: Playwright sits in the user's site-packages. Both envs must be served (`devenv.sh serve`) with
realtime: each env's SPA points at a Socket.IO server on its published `socketio_port` through an untracked
`frontend/.env.local` (`VITE_SOCKET_PORT`), the recipe in `.claude/skills/verify/bin/walk_live_refresh.py`. A walk of
a live claim stops when its page opens no socket.

Users come from `free_users.py`, one per role, with the walk's password set on both envs. Records are picked by
query on each env. The walk writes only on its throwaway envs: it seeds one rejected payment raised by the
Procurement Executive (once; a rerun finds it), adds notices marked seen afterwards, marks a reminder done and puts
it back, renames a user and puts the name back, holds a payment and releases it.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1] / "verify" / "bin"))
sys.path.insert(0, str(HERE))
from drive import base_url, login, session  # noqa: E402
from feature_walk import settle, sidebar_ready  # noqa: E402
from record import Walk, pin  # noqa: E402

AFTER, BEFORE = "madhu-perf-nov-v2-walk", "madhu-perf-nov-v2-walk-base"
SIDES = (("before", BEFORE), ("after", AFTER))
PW = "WalkPw-PerfNov2"
DEVENV = str(ROOT / "scripts" / "devenv" / "devenv.sh")
PROFILES = {
    "Admin": "Nirmaan Admin Profile",
    "Accountant": "Nirmaan Accountant Profile",
    "Procurement Executive": "Nirmaan Procurement Executive Profile",
    "PMO Executive": "Nirmaan PMO Executive Profile",
}
SEED_REASON = "Walk seed (perf/nov-v2): rejected to test the Open PO link"
LIVE_S = 15  # how long a live change may take to show

if len(sys.argv) < 2:
    sys.exit(__doc__)
out = Path(sys.argv[1]).expanduser()
only = set(sys.argv[2:])
os.environ["VERIFY_OUT"] = str(out / "drive")
HEAD, BASE = "790ea168f", "31d29db45"
author = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%an", HEAD], capture_output=True,
                        text=True).stdout.strip()
w = Walk(out, pr={"number": "perf/nov-v2", "title": "perf/nov-v2: speed and table fixes rebased on develop",
                  "url": "", "author": author, "head": HEAD, "base": BASE},
         envs={"after": AFTER, "before": BEFORE},
         data_note="each env is a copy of the production backup of 2026-11-25; the walk's writes stay in its env")

# --- Claims: the five places the rebase had conflicts, then the other screen claims, then those with no screen. ---
w.change("notif", "1. Sidebar notifications: newest 500, live, one row per change",
         "NewSidebar loads the newest 500 notifications with only the 4 fields the 'new' dots read; the badge counts "
         "the unread among them. A new or seen notification, from another tab or user, updates the badge live by "
         "reading that one row. Develop's bell dropdown still lists the notifications.")
w.change("rejected", "2a. Raised By Me › Rejected keeps develop's Open PO link",
         "Develop's 12 Nov 'Payment Raised By Me → Rejected' view shows an 'Open PO' / 'Open WO' link in the Against "
         "cell. The delete dialog's 'Created by' line shows a name (raised_by_name), not the users list: that part is "
         "not walked, as the line cannot show (Raised By Me lists only the viewer's own payments).")
w.change("pay_cheque", "2b. Payments: cheque chip opens for cheque roles; Raised by names from the row",
         "The cheque chip opens the Cheque only for CHEQUE_ACCESS_PROFILES, worked out once per page. 'Raised by' "
         "names come from the row (raised_by_name), not the users list, and read the same as on develop.")
w.change("vendor_pay", "3. Vendor › Payments sub-tabs keep develop's features",
         "Develop's 20 Nov sub-tabs Paid / Cheque Pending / Other Pending all work; Paid's UTR cell has the receipt "
         "link or paperclip. The cheque chip opens the Cheque for a cheque role and stays plain for a PMO Executive.")
w.change("reminders", "4. Action Center reminders update live",
         "The reminders panel refreshes by itself when a Reminder Schedule Log is marked done or raised, from another "
         "tab or user, without a reload.")
w.change("list_update", "5. useListUpdate over #1670's useDocTypeEventListener",
         "useListUpdate is a thin layer over develop's useDocTypeEventListener. Its three users still refresh live: "
         "notifications (1), reminders (4) and the users list (a name changed in one tab shows in another). Closing "
         "one screen that listens to a doctype does not stop another open screen's live refresh of it. The walk closes "
         "the Users list (a useDocTypeEventListener screen) and watches the app-wide useListUpdate listener of the same "
         "doctype. The direction the refactor fixed (closing a useListUpdate screen) has no screen pair: the only "
         "useListUpdate screen that closes, the reminders panel, shares its doctype with no other listener.")
w.change("tables_fit", "Tables fit the screen; thin brand-colour scrollbars",
         "A table's scroll box takes the screen space below it, leaving the pagination bar on screen; scrollbars are "
         "4 px in the brand colour (c411a1095).")
w.change("search", "Enter searches at once; the newest search wins",
         "Enter in a table's search box searches at once; the typing wait is 300 ms (was 500). Only the newest "
         "request may show its rows (476b0eaee): that half is not walked (a race no screen can stage on demand, and "
         "476b0eaee has no test).")
w.change("po_list", "PO list: owner's name from the row; no Projects / Vendors downloads",
         "The Release PO list's 'Approved By' reads owner_name from the row (30c4ac3d7) and the page no longer "
         "downloads the Projects and Vendors lists (537821205). Names are identical.")
w.change("popups", "Row pop-ups load only when opened",
         "The items hover card and the PO Remarks pop-up load their data only when opened (feb9396c5). The items card "
         "is opened; the Remarks pop-up is checked only for the closed rows' requests.")
w.change("edit_user", "Edit User: a mobile number already in use",
         "Saving a mobile number another user has shows 'This mobile number is already used by another user.' on "
         "the field, not the raw database text (5f17e557a).")
w.change("mark_paid", "Mark as Paid failure shows the server's reason",
         "When another user holds the payment first, Mark as Paid's failure names the hold instead of the SDK's "
         "generic text (47c2090fa).")
w.change("rupees", "Rupee figures print as before",
         "Dates and rupee figures use formatters built once; the text is unchanged (4eab23e41, 1cde21a4a).")
w.change("pay_fields", "Payments list fetches only the PO / WO fields it reads",
         "The Payments page's Procurement Orders and Service Requests downloads ask for the fields it reads, not "
         "'*' (6f876f01b).")
w.change("users_once", "Users list downloaded once per session",
         "useUsersList() is fetched once and shared; the logged-in user is worked out once (UserDataProvider) "
         "(f123772cc, 99e02b79a).")
w.change("po_summary", "Project PO summary in one query",
         "generate_po_summary reads all PO items in one query instead of a get_doc per PO (e7cc0d6a6).")
w.change("no_screen", "Speed fixes a screen cannot show",
         "Memoised rows (137738501), rows that do not force layout (96f12a444), virtual rows measuring their real "
         "height (c81e6e7a8), rows that read no user or toast state (2fe8d3572), 14 more tables naming from the row "
         "(7a4e78148) and the per-user list cache key (269af9a96).")


# --- Plumbing -------------------------------------------------------------------------------------------------------

def db(env: str, name: str, code: str, *args: str):
    """Run `code` on env's DB through `devenv.sh script` and return what it printed after `JSON `. Saved under db/."""
    src = out / "db" / f"{name}.{env}.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("import json, sys\nimport frappe\n\ndef emit(o):\n    print('JSON ' + json.dumps(o, default=str))\n\n"
                   + code)
    res = subprocess.run([DEVENV, "script", env, str(src), *args], capture_output=True, text=True, timeout=600,
                         cwd=ROOT)
    src.with_suffix(".txt").write_text(res.stdout + res.stderr)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("JSON ")), None)
    if line is None:
        raise RuntimeError(f"db script {src} printed no JSON line; see {src.with_suffix('.txt')}")
    return json.loads(line[5:])


class Api:
    """The Administrator through the env's REST API: the 'other user' of a live change."""

    def __init__(self, env: str):
        self.base = base_url(env)
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.open(urllib.request.Request(f"{self.base}/api/method/login",
                                            data=urllib.parse.urlencode({"usr": "Administrator", "pwd": PW}).encode()))

    def call(self, verb: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.base}{urllib.parse.quote(path)}", method=verb,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(self.op.open(req).read() or b"{}")

    def put(self, doctype: str, name: str, body: dict) -> dict:
        return self.call("PUT", f"/api/resource/{doctype}/{name}", body)["data"]

    def post(self, doctype: str, body: dict) -> dict:
        return self.call("POST", f"/api/resource/{doctype}", body)["data"]

    def method(self, method: str, body: dict) -> dict:
        return self.call("POST", f"/api/method/{method}", body)


class Net:
    """Every request a page sends, with its decoded URL and time."""

    def __init__(self, page):
        self.reqs: list[dict] = []
        page.on("request", lambda r: self.reqs.append({"url": urllib.parse.unquote_plus(r.url), "t": time.time(),
                                                        "body": urllib.parse.unquote_plus(r.post_data or ""), "r": r}))

    def mark(self) -> int:
        return len(self.reqs)

    def since(self, i: int, pattern: str) -> list[dict]:
        return [x for x in self.reqs[i:] if re.search(pattern, x["url"])]

    def matching(self, i: int, pattern: str) -> list[dict]:
        """Requests since i whose URL or body matches."""
        return [x for x in self.reqs[i:] if re.search(pattern, x["url"] + " " + x["body"])]


@contextlib.contextmanager
def live(env: str, user: str):
    """A logged-in page whose socket carries the user's session. The SPA opens its socket on the login page, as
    Guest, and keeps it after logging in, so the page is loaded again first, as a returning user's is."""
    with session(env, user, PW) as (page, _):
        sockets: list[str] = []
        page.on("websocket", lambda ws: sockets.append(ws.url))
        net = Net(page)
        page.reload()
        settle(page, 2500)
        sidebar_ready(page)
        if not any("socket.io" in s for s in sockets):
            raise RuntimeError(f"{env}: the page opened no Socket.IO connection, so a live claim cannot be walked")
        yield page, net


@contextlib.contextmanager
def other_tab(page, env: str, user: str):
    """A second browser context of the same browser (its own cookies), logged in as user and loaded again so its
    socket carries the session: another tab or another user beside `page`."""
    ctx = page.context.browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        tab = ctx.new_page()
        login(tab, base_url(env), user, PW)
        tab.reload()
        settle(tab, 2500)
        sidebar_ready(tab)
        yield tab
    finally:
        ctx.close()


def wait_until(page, read, ok, seconds: float = LIVE_S, since: float | None = None):
    """Poll read() until ok(value); return (value, seconds since `since` (else now) or None when it never held)."""
    t0 = since or time.time()
    while True:
        v = read()
        if ok(v):
            return v, round(time.time() - t0, 1)
        if time.time() - t0 > seconds:
            return v, None
        page.wait_for_timeout(250)


def guarded(cid: str, fn) -> None:
    for attempt in (1, 2):
        steps, checks = len(w.data["steps"]), len(w.data["checks"])
        try:
            fn()
            return
        except Exception as e:
            traceback.print_exc()
            del w.data["steps"][steps:], w.data["checks"][checks:]
            if attempt == 2:
                w.status(cid, "failed", f"walk stopped: {type(e).__name__}: {str(e).splitlines()[0][:200]}")


def menu(page, label: str):
    return page.locator('[data-sidebar="menu-button"]').get_by_text(label, exact=True)


def sub_menu(page, label: str):
    return page.locator('[data-sidebar="menu-sub-button"]').get_by_text(label, exact=True)


# --- Users and facts ------------------------------------------------------------------------------------------------

def pick_users() -> dict:
    users = {}
    for role, profile in PROFILES.items():
        res = subprocess.run([DEVENV, "script", AFTER, ".claude/skills/verify/bin/free_users.py", profile],
                             capture_output=True, text=True, cwd=ROOT, timeout=300)
        line = next(ln for ln in res.stdout.splitlines() if ln.startswith(profile))
        users[role] = line[len(profile):].split()[0]
    for _, env in SIDES:
        subprocess.run([DEVENV, "run", env, "set-admin-password", PW], capture_output=True, cwd=ROOT, timeout=300)
        for email in users.values():
            subprocess.run([DEVENV, "run", env, "set-password", email, PW], capture_output=True, cwd=ROOT, timeout=300,
                           check=True)
    (out / "users.json").write_text(json.dumps(users, indent=1))
    return users


USERS = json.loads((out / "users.json").read_text()) if (out / "users.json").exists() else pick_users()

# Read on each env, as each walk user: the records the walk uses and what that user may see of them.
FACTS = r'''
users = json.loads(sys.argv[1]); pe = users["Procurement Executive"]
q = frappe.db.sql
# Notices an earlier run left unseen would change the badge the walk starts from. A raw UPDATE fires no hook, which
# is right here: only the walk's own notices change, and nothing derives from their seen flag.
q("""UPDATE "tabNirmaan Notifications" SET seen = 'true' WHERE title LIKE 'Walk %%' AND seen = 'false'""")
frappe.db.commit()
pe_projects = tuple(frappe.get_all("User Permission", filters={"user": pe, "allow": "Projects"}, pluck="for_value"))
if not pe_projects:
    sys.exit(f"{pe} has no project, so the Procurement Executive cannot be walked")

# 2a: one rejected payment raised by the PE, seeded once on a PO of the PE's projects.
seed = frappe.get_all("Project Payments", filters={"owner": pe, "status": "Rejected", "rejection_reason": sys.argv[2]},
                      fields=["name", "document_name"], limit=1)
if not seed:
    po = q("""SELECT name, project, vendor FROM "tabProcurement Orders" WHERE project IN %s
              AND status IN ('PO Approved', 'Dispatched', 'Partially Delivered', 'Delivered')
              ORDER BY creation DESC LIMIT 1""", (pe_projects,), as_dict=True)[0]
    frappe.set_user(pe)
    doc = frappe.get_doc({"doctype": "Project Payments", "document_type": "Procurement Orders",
                          "document_name": po.name, "project": po.project, "vendor": po.vendor, "amount": 1,
                          "status": "Requested"}).insert()
    frappe.set_user("Administrator")
    # set_value skips Project Payments' doc_events on purpose: a throwaway seed on a walk env, and the walk reads only
    # this row's own fields (status, reason, PO link). The amount is ₹1 on a Rejected row, which no total counts.
    frappe.db.set_value("Project Payments", doc.name, {"status": "Rejected", "rejection_reason": sys.argv[2]})
    frappe.db.commit()
    seed = [{"name": doc.name, "document_name": po.name}]

# 3: a vendor with a cheque still pending and a paid payment in the PE's projects; one with other pending payments
# (the third sub-tab) first.
vendor = q("""SELECT vendor FROM "tabProject Payments" WHERE COALESCE(vendor, '') != '' AND project IN %s
    GROUP BY vendor
    HAVING MAX((mode_of_payment = 'Cheque' AND status = 'Reconciliation Pending' AND COALESCE(cheque, '') != '')::int) = 1
       AND MAX((status = 'Paid')::int) = 1
    ORDER BY MAX((status NOT IN ('Paid', 'Reconciliation Pending'))::int) DESC, COUNT(*) DESC, vendor LIMIT 1""",
    (pe_projects,))[0][0]

# 4: the reminder to mark done (a Pending log).
reminder = q("""SELECT name, reminder_schedule FROM "tabReminder Schedule Log" WHERE status = 'Pending'
                ORDER BY due_date, name LIMIT 1""", as_dict=True)

# 5: a vendor whose creator has a Nirmaan Users row (the overview names its creator from the users list).
named = q("""SELECT v.name, v.vendor_name, v.owner, nu.full_name, nu.first_name, nu.last_name
    FROM "tabVendors" v JOIN "tabNirmaan Users" nu ON nu.name = v.owner
    WHERE v.owner NOT IN ('Administrator', %s) AND COALESCE(nu.full_name, '') != ''
    ORDER BY v.creation DESC LIMIT 1""", users["Admin"], as_dict=True)[0]

# Edit User: another user's mobile number to try on a different user.
mobiles = q("""SELECT u.name, u.mobile_no FROM "tabUser" u JOIN "tabNirmaan Users" nu ON nu.name = u.name
    WHERE COALESCE(u.mobile_no, '') != '' AND u.enabled = 1 AND u.name NOT IN %s ORDER BY u.name LIMIT 2""",
    (tuple(users.values()),), as_dict=True)

# Mark as Paid: an Approved PO payment, not on hold, the only Approved one on its PO.
approved = q("""SELECT p.name, p.document_name FROM "tabProject Payments" p WHERE p.status = 'Approved'
    AND COALESCE(p.on_hold, 0) = 0 AND p.document_type = 'Procurement Orders'
    AND (SELECT COUNT(*) FROM "tabProject Payments" x WHERE x.document_name = p.document_name
         AND x.status = 'Approved') = 1 ORDER BY p.creation DESC LIMIT 1""", as_dict=True)

# PO summary: the project with the most POs.
big = q("""SELECT p.name, p.project_name, COUNT(po.name) AS pos FROM "tabProjects" p
    JOIN "tabProcurement Orders" po ON po.project = p.name GROUP BY p.name, p.project_name
    ORDER BY COUNT(po.name) DESC LIMIT 1""", as_dict=True)[0]

# Payments fields: the customer with the most POs (its Financials › All Orders lists them).
customer = q("""SELECT c.name, c.company_name, COUNT(po.name) AS pos FROM "tabCustomers" c
    JOIN "tabProjects" p ON p.customer = c.name JOIN "tabProcurement Orders" po ON po.project = p.name
    GROUP BY c.name, c.company_name ORDER BY COUNT(po.name) DESC LIMIT 1""", as_dict=True)[0]

# 2b: a paid cheque payment (Payments Done shows its cheque chip in the UTR / Ref cell), the only payment on its PO.
paid_cheque = q("""SELECT p.name, p.document_name FROM "tabProject Payments" p WHERE p.status = 'Paid'
    AND COALESCE(p.cheque, '') != '' AND p.document_type = 'Procurement Orders'
    AND (SELECT COUNT(*) FROM "tabProject Payments" x WHERE x.document_name = p.document_name) = 1
    ORDER BY p.payment_date DESC, p.name LIMIT 1""", as_dict=True)

def sees(user, doctype, filters):
    frappe.set_user(user)
    n = len(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=0))
    frappe.set_user("Administrator")
    return n

emit({"seed": seed[0], "vendor": vendor, "vendor_name": frappe.db.get_value("Vendors", vendor, "vendor_name"),
      "reminder": reminder[0] if reminder else None, "named": named, "mobiles": mobiles,
      "big": big, "customer": customer, "approved": approved[0] if approved else None, "paid_cheque": paid_cheque[0] if paid_cheque else None,
      "sees": {
          "PE: the seeded rejected payment": sees(pe, "Project Payments", {"name": seed[0]["name"]}),
          "PE: the vendor's cheque payments": sees(pe, "Project Payments", {"vendor": vendor, "mode_of_payment": "Cheque"}),
          "Accountant: the vendor's payments": sees(users["Accountant"], "Project Payments", {"vendor": vendor}),
          "PMO: the vendor's payments": sees(users["PMO Executive"], "Project Payments", {"vendor": vendor}),
          "Accountant: own notifications": sees(users["Accountant"], "Nirmaan Notifications", {"recipient": users["Accountant"]}),
          "Admin: Pending reminder logs": sees(users["Admin"], "Reminder Schedule Log", {"status": "Pending"}),
          "Admin: the vendor whose creator is renamed": sees(users["Admin"], "Vendors", {"name": named["name"]}),
          "Admin: the project with the most POs": sees(users["Admin"], "Projects", {"name": big["name"]}),
          "Admin: Purchase Orders": sees(users["Admin"], "Procurement Orders", {"project": big["name"]}),
          "PMO: the vendor's cheque payments": sees(users["PMO Executive"], "Project Payments",
                                                    {"vendor": vendor, "mode_of_payment": "Cheque"}),
      }})
'''
FACT = {env: db(env, "facts", FACTS, json.dumps(USERS), SEED_REASON) for _, env in SIDES}
# Which claim each "the user sees it" check belongs to; the rest are the vendor's (claim 3).
SEES_CHANGE = {"PE: the seeded rejected payment": "rejected", "Accountant: own notifications": "notif",
               "Admin: Pending reminder logs": "reminders",
               "Admin: the vendor whose creator is renamed": "list_update",
               "Admin: the project with the most POs": "po_summary", "Admin: Purchase Orders": "po_list"}
for side, env in SIDES:
    for what, n in FACT[env]["sees"].items():
        w.check(SEES_CHANGE.get(what, "vendor_pay"), what.split(":")[0],
                f"{what} on {side}: at least 1 row", str(n), n > 0)


# --- 1. Notifications -----------------------------------------------------------------------------------------------

def bell(page):
    return page.locator('[data-sidebar="menu-button"]').filter(has_text="Notifications").first


def badge_box(page):
    return page.locator('[data-sidebar="menu-badge"]').first


def badge(page) -> int:
    t = page.locator('[data-sidebar="menu-badge"]').first.inner_text().strip()
    return int(t) if t.isdigit() else -1


def notif() -> None:
    role, user = "Accountant", USERS["Accountant"]
    for side, env in SIDES:
        api = Api(env)
        with live(env, user) as (page, net):
            first = [x for x in net.since(0, r"/api/resource/Nirmaan Notifications\?") if "limit=500" in x["url"]]
            fields = json.loads(re.search(r"fields=(\[[^&]*\])", first[0]["url"]).group(1)) if first else []
            w.check("notif", role, f"{side}: the sidebar's list asks for " + ("the 4 fields the dots read, newest 500"
                    if side == "after" else "every field it shows (develop)"),
                    f"{len(fields)} fields: {', '.join(fields)}; limit {re.search(r'limit=(\d+)', first[0]['url']).group(1) if first else '?'}",
                    (len(fields) == 4) if side == "after" else len(fields) > 4, kind="must" if side == "after" else "info")
            n0 = badge(page)
            w.step(page, "notif", side, role, user,
                   f"{'Develop' if side == 'before' else 'This branch'}: the Accountant's sidebar. The badge counts "
                   f"unread notifications: {n0}.", pins=[pin(badge_box(page), f"Badge {n0}", "info")])
            mark = net.mark()
            title = f"Walk notice {time.strftime('%H:%M:%S')}"
            made = api.post("Nirmaan Notifications", {"recipient": user, "title": title, "description":
                            "A notice the walk adds as another user (Administrator).", "seen": "false", "type": "info",
                            "action_url": "notifications"})
            try:
                n1, took = wait_until(page, lambda: badge(page), lambda v: v == n0 + 1)
                w.check("notif", role, f"{side}: a notice another user adds shows on the badge within {LIVE_S} s, no reload",
                        f"{n0} → {n1} in {took} s" if took is not None else f"still {n1} after {LIVE_S} s", took is not None)
                page.wait_for_timeout(1500)
                reads = net.since(mark, r"/api/resource/Nirmaan Notifications\?")
                one_row = len(reads) == 1 and f'["name","=","{made["name"]}"]' in reads[0]["url"] and re.search(r"limit=1(&|$)", reads[0]["url"])
                what = "; ".join(("one row by name, 4 fields" if f'["name","=","{made["name"]}"]' in x["url"]
                                  else f"the whole list (limit {re.search(r'limit=(\d+)', x['url']).group(1)})")
                                 for x in reads) or "no request"
                w.check("notif", role, f"{side}: the live change reads " + ("just that one row" if side == "after"
                        else "(develop) the whole list again"), what, one_row if side == "after" else bool(reads),
                        kind="must" if side == "after" else "info")
                w.step(page, "notif", side, role, user,
                       f"The Administrator adds a notice for the Accountant. The open page's badge goes {n0} → {n1} "
                       f"{'in ' + str(took) + ' s' if took is not None else '(did not change)'}, no reload. It read: {what}.",
                       pins=[pin(badge_box(page), f"Badge {n1}",
                                 "info" if took is not None else "must")])
                # Tab B, the same user: the bell dropdown (develop's) lists the notice; opening it marks it seen.
                with other_tab(page, env, user) as tab_b:
                    w.step(tab_b, "notif", side, role, user,
                           "A second tab of the same Accountant opens the bell dropdown.",
                           click=bell(tab_b))
                    settle(tab_b, 1500)
                    item = tab_b.get_by_text(title, exact=True)
                    w.check("notif", role, f"{side}: the bell dropdown lists the new notice", "listed" if item.count()
                            else "not listed", item.count() > 0)
                    w.step(tab_b, "notif", side, role, user,
                           "The dropdown lists the newest notifications, the walk's notice first. Opening it marks it seen.",
                           click=item if item.count() else None,
                           pins=[] if item.count() else [pin(tab_b.locator('[role="menu"]'), "The notice is not listed.",
                                                             "must")])
                    n2, took2 = wait_until(page, lambda: badge(page), lambda v: v == n0)
                    w.check("notif", role, f"{side}: a notice seen in another tab leaves the first tab's badge within "
                            f"{LIVE_S} s", f"{n1} → {n2} in {took2} s" if took2 is not None else f"still {n2}",
                            took2 is not None)
            finally:
                # Seen even when a step above failed, so a rerun starts from the same badge.
                api.put("Nirmaan Notifications", made["name"], {"seen": "true"})
            w.step(page, "notif", side, role, user,
                   f"Back in the first tab: the badge is {n2} again "
                   f"{'after ' + str(took2) + ' s' if took2 is not None else '(it did not change)'}, no reload.",
                   pins=[pin(badge_box(page), f"Badge {n2}", "info" if took2 is not None else "must")])


# --- 2. Project Payments ------------------------------------------------------------------------------------------

CHIP_OPENS = 'button[title$=": open the cheque"]'
CHIP_PLAIN = 'span[title^="Cheque "], span[title^="Credit Cheque "]'


def tab_button(page, label: str):
    """A tab drawn as a button (Project Payments, Purchase Orders), by the label before its count. A `/` in a regex Playwright sends to the page
    breaks it, so the label's `/` matches any character."""
    return page.get_by_role("button", name=re.compile(rf"^{re.escape(label).replace('/', '.')} \d+$"))


def open_payments(page, cid: str, side: str, role: str, user: str) -> None:
    sidebar_ready(page)
    w.step(page, cid, side, role, user, "Open Project Payment & Expense from the sidebar.",
           click=menu(page, "Project Payment & Expense"))
    settle(page, 2000)


def dialog_opened(page) -> bool:
    try:
        page.get_by_role("dialog").first.wait_for(timeout=8000)
        return True
    except Exception:
        return False


def rejected() -> None:
    role, user = "Procurement Executive", USERS["Procurement Executive"]
    for side, env in SIDES:
        seed = FACT[env]["seed"]
        with session(env, user, PW) as (page, _):
            open_payments(page, "rejected", side, role, user)
            w.step(page, "rejected", side, role, user, "Open Payment Raised By Me.",
                   click=tab_button(page, "Payment Raised By Me"))
            settle(page)
            w.step(page, "rejected", side, role, user, "Pick its Rejected view.",
                   click=page.get_by_role("tab", name=re.compile(r"^Rejected \d+$")))
            settle(page, 2000)
            row = page.locator("tbody tr").filter(has_text=seed["document_name"]).filter(has_text=SEED_REASON).first
            link = row.get_by_role("link", name=re.compile(r"^Open (PO|WO)$"))
            has = link.count() > 0
            w.check("rejected", role, f"{side}: the rejected payment's Against cell has develop's 'Open PO' link",
                    "shown" if has else "not shown", has)
            w.step(page, "rejected", side, role, user,
                   f"The walk's rejected payment on {seed['document_name']}. Its Against cell has the 'Open PO' link "
                   "(develop, 12 Nov). First, its trash button.", click=row.get_by_role("button", name="Delete"),
                   pins=[pin(link, "Open PO", "info")] if has else
                   [pin(row, "No 'Open PO' link in the Against cell.", "must")])
            settle(page, 800)
            dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog")).first
            text = dialog.inner_text() if dialog.count() else ""
            created = re.search(r"Created by (.+)", text)
            w.check("rejected", role, f"{side}: the trash dialog names who raised it only when it is someone else",
                    f"'Created by {created.group(1)}'" if created else "no 'Created by' line (the PE raised it)",
                    created is None, kind="info")
            w.step(page, "rejected", side, role, user,
                   "The trash dialog. It has no 'Created by' line: this tab lists only the viewer's own payments "
                   "(owner rule of 23 Sep), so the line can never show here.",
                   pins=[pin(dialog, "The 'Created by' line (now raised_by_name) is unreachable: Raised By Me lists "
                                     "only the viewer's rows, and the line shows only for someone else's. Dead code "
                                     "on both sides; this part of the claim has no screen.", "nit")])
            page.keyboard.press("Escape")
            settle(page, 800)
            w.step(page, "rejected", side, role, user, "Cancel, then follow 'Open PO'.", click=link)
            settle(page, 2500)
            ok = seed["document_name"] in urllib.parse.unquote(page.url) or \
                seed["document_name"] in page.locator("body").inner_text()
            w.check("rejected", role, f"{side}: 'Open PO' opens {seed['document_name']}", urllib.parse.unquote(page.url), ok)
            w.step(page, "rejected", side, role, user, f"The PO page of {seed['document_name']}, where a rejected "
                   "payment is removed to free its term.")


def pay_cheque() -> None:
    role, user = "Accountant", USERS["Accountant"]
    names = {}
    for side, env in SIDES:
        with session(env, user, PW) as (page, _):
            po = FACT[env]["paid_cheque"]["document_name"]
            open_payments(page, "pay_cheque", side, role, user)
            w.step(page, "pay_cheque", side, role, user, "Open Payment Done / Reconciliation Done: its UTR / Ref "
                   "column shows a cheque payment's chip.", click=tab_button(page, "Payment Done / Reconciliation Done"))
            settle(page, 2500)
            page.get_by_placeholder(re.compile("^Search")).first.fill(po)
            page.keyboard.press("Enter")
            settle(page, 2500)
            chip = page.locator("tbody tr").filter(has_text=po).locator(CHIP_OPENS).first
            has = chip.count() > 0
            w.check("pay_cheque", role, f"{side}: the Accountant's cheque chip is a button that opens the Cheque",
                    "a button" if has else f"plain ({page.locator(CHIP_PLAIN).count()} plain chips)", has)
            if has:
                w.step(page, "pay_cheque", side, role, user, f"Search {po}, a PO paid by cheque: its UTR / Ref cell "
                       "carries the cheque chip.", click=chip)
                opened = dialog_opened(page)
                w.check("pay_cheque", role, f"{side}: the chip opens the Cheque", "opened" if opened else
                        "nothing opened", opened)
                w.step(page, "pay_cheque", side, role, user, "The Cheque opens over the table.",
                       pins=[] if opened else [pin(page.locator("main"), "Nothing opened.", "must")])
                page.keyboard.press("Escape")
                settle(page, 800)
            w.step(page, "pay_cheque", side, role, user, "Open All Payments, which has the 'Raised by' column.",
                   click=tab_button(page, "All Payments"))
            settle(page, 2000)
            box = page.get_by_placeholder(re.compile("^Search")).first
            if box.input_value():
                box.fill("")
                page.keyboard.press("Enter")
            settle(page, 3000)
            heads = [h.strip().lower() for h in page.get_by_role("columnheader").all_inner_texts()]
            idx = heads.index("raised by") if "raised by" in heads else None
            col = page.get_by_role("columnheader").nth(idx) if idx is not None else None
            names[side] = page.evaluate(
                """i => [...document.querySelectorAll('tbody tr')].slice(0, 25).map(tr => [...tr.children])
                     .filter(c => c.length > i).map(c => c[2].innerText.trim().slice(0, 40) + ' | ' + c[i].innerText.trim())""",  # c[2]: the Against cell
                idx) if idx is not None else []
            w.step(page, "pay_cheque", side, role, user, "All Payments: the 'Raised by' column names who raised each "
                   "payment" + (" (from the row's raised_by_name)." if side == "after" else " (from the users list)."),
                   pins=[pin(col, "Raised by", "info")] if col is not None else
                   [pin(page.locator("thead"), "No 'Raised by' column.", "must")])
    role_p, user_p = "PMO Executive", USERS["PMO Executive"]
    for side, env in SIDES:
        po = FACT[env]["paid_cheque"]["document_name"]
        with session(env, user_p, PW) as (page, _):
            sidebar_ready(page)
            menu(page, "Project Payment & Expense").click()
            settle(page, 2500)
            tab = tab_button(page, "Payment Done / Reconciliation Done")
            if not tab.count():
                # The PMO's Payments page is Raised By Me alone, and no PMO Executive has raised a cheque payment in
                # this data, so this page shows that role no chip; the plain chip is walked on Vendor › Payments (3).
                tabs = [t.split("\n")[0] for t in page.locator("main button").all_inner_texts()[:4]]
                w.check("pay_cheque", role_p, f"{side}: a cheque chip the PMO Executive can meet on the Payments page",
                        f"none: the page has only {', '.join(tabs[:1])}, listing the PMO's own payments, none by cheque",
                        True, kind="info")
                w.step(page, "pay_cheque", side, role_p, user_p, "The PMO Executive's Payments page: Raised By Me "
                       "only, so no cheque chip to open here. Claim 3 walks the PMO's plain chip on Vendor › Payments.")
                continue
            tab.first.click()
            settle(page, 2500)
            page.get_by_placeholder(re.compile("^Search")).first.fill(po)
            page.keyboard.press("Enter")
            settle(page, 2500)
            row = page.locator("tbody tr").filter(has_text=po)
            plain, button = row.locator(CHIP_PLAIN).first, row.locator(CHIP_OPENS).first
            w.check("pay_cheque", role_p, f"{side}: the PMO Executive's cheque chip is plain text, not a button",
                    "a button" if button.count() else "plain text" if plain.count() else "no chip",
                    plain.count() > 0 and not button.count())
            w.step(page, "pay_cheque", side, role_p, user_p, f"Payments Done as the PMO Executive, searched for {po}: "
                   "the cheque chip is plain text.", pins=[pin(plain, "Plain chip", "info")] if plain.count() else
                   [pin(page.locator("tbody"), "No plain cheque chip.", "must")])
    # This branch's tables draw only the rows in view, so compare the rows both sides drew.
    pairs = list(zip(names.get("before", []), names.get("after", [])))
    diff = [f"{a} / {b}" for a, b in pairs if a != b]
    same = len(pairs) >= 10 and not diff
    w.check("pay_cheque", role, "'Raised by' names of the newest rows are the same on develop and on this branch",
            f"{len(pairs)} rows compared, all the same" if same else f"{len(pairs)} rows compared; differ: {diff[:3]}",
            same)
    (out / "raised_by_names.json").write_text(json.dumps(names, indent=1))


# --- 3. Vendor › Payments -----------------------------------------------------------------------------------------

def open_vendor(page, cid: str, side: str, role: str, user: str, vendor: str, name: str, record: bool = True) -> None:
    def click(caption: str, target) -> None:
        if record:
            w.step(page, cid, side, role, user, caption, click=target)
        else:
            target.first.click()
            page.wait_for_load_state("networkidle")

    sidebar_ready(page)
    if menu(page, "Vendors").count():
        click("Open Vendors from the sidebar.", menu(page, "Vendors"))
    else:
        if not sub_menu(page, "Vendors").first.is_visible():
            click("Open Admin Options in the sidebar.", menu(page, "Admin Options"))
            settle(page, 600)
        click("Open Vendors under Admin Options.", sub_menu(page, "Vendors"))
    settle(page)
    page.get_by_placeholder(re.compile("^Search")).first.fill(name)
    settle(page, 2000)
    click("Find the vendor by name and open it.", page.locator(f'tbody a[href$="/vendors/{vendor}"]'))
    page.get_by_role("button", name="Overview", exact=True).first.wait_for(timeout=30_000)
    settle(page)


def group_tab(page, label: str):
    return page.get_by_role("tab", name=re.compile(rf"^{re.escape(label)} \d+$"))


def vendor_pay() -> None:
    plan = [("Accountant", True), ("PMO Executive", False), ("Procurement Executive", True)]
    for side, env in SIDES:
        vendor, vname = FACT[env]["vendor"], FACT[env]["vendor_name"]
        for role, opens in plan:
            user = USERS[role]
            full = role == "Accountant"
            with session(env, user, PW) as (page, _):
                open_vendor(page, "vendor_pay", side, role, user, vendor, vname, record=full)
                tab = page.get_by_role("button", name="Payments", exact=True)
                if full:
                    w.step(page, "vendor_pay", side, role, user, "Open the vendor's Payments tab.", click=tab)
                else:
                    tab.first.click()
                settle(page, 2500)
                want = ["Paid", "Cheque Pending", "Other Pending Payments"]
                got = [k for k in want if group_tab(page, k).count()]
                w.check("vendor_pay", role, f"{side}: develop's three sub-tabs are there", ", ".join(got) or "none",
                        got == want)
                if full:
                    utr = page.locator("tbody tr td").filter(has=page.locator(
                        'a[href*="/files/"], a[href*="/private/"], button[title*="receipt" i], '
                        'button[aria-label*="receipt" i]')).first
                    has_utr = utr.count() > 0
                    w.check("vendor_pay", role, f"{side}: Paid's UTR cell has the receipt link or paperclip",
                            "shown" if has_utr else "not found", has_utr)
                    w.step(page, "vendor_pay", side, role, user, "Paid: bank-confirmed payments. A UTR cell carries "
                           "the receipt link, or a paperclip to attach one.",
                           pins=[pin(utr, "Receipt link / paperclip", "info")] if has_utr else
                           [pin(page.locator("tbody"), "No receipt link or paperclip found.", "must")])
                    w.step(page, "vendor_pay", side, role, user, "Open Cheque Pending.",
                           click=group_tab(page, "Cheque Pending"))
                else:
                    group_tab(page, "Cheque Pending").first.click()
                settle(page, 2000)
                chip_btn = page.locator("tbody " + CHIP_OPENS).first
                chip_span = page.locator("tbody span[title^='Cheque ']").first
                is_btn = chip_btn.count() > 0
                w.check("vendor_pay", role, f"{side}: the cheque chip " + ("opens the Cheque" if opens else
                        "stays plain text (PMO views cheques but cannot open them)"),
                        "a button" if is_btn else ("plain text" if chip_span.count() else "no chip"), is_btn == opens and (is_btn or chip_span.count() > 0))
                if is_btn:
                    w.step(page, "vendor_pay", side, role, user, f"Cheque Pending as {role}: the chip is a button.",
                           click=chip_btn)
                    opened = dialog_opened(page)
                    w.check("vendor_pay", role, f"{side}: clicking it opens the Cheque", "opened" if opened else
                            "nothing", opened)
                    w.step(page, "vendor_pay", side, role, user, "The Cheque opens.")
                    page.keyboard.press("Escape")
                    settle(page, 800)
                else:
                    w.step(page, "vendor_pay", side, role, user, f"Cheque Pending as {role}: the chip is plain text, "
                           "not a button.", pins=[pin(chip_span, "Plain chip", "info" if not opens else "must")]
                           if chip_span.count() else [])
                if full:
                    w.step(page, "vendor_pay", side, role, user, "Open Other Pending Payments.",
                           click=group_tab(page, "Other Pending Payments"))
                    settle(page, 2000)
                    w.step(page, "vendor_pay", side, role, user, "Other Pending Payments: everything still in progress.")


# --- 4. Action Center reminders -----------------------------------------------------------------------------------

def reminder_count(page) -> int:
    t = page.get_by_role("button", name=re.compile(r"^Reminders \d+$")).first.inner_text()
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else -1


def reminders() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        log = FACT[env]["reminder"]
        api = Api(env)
        with live(env, user) as (page, net):
            settle(page, 1500)
            n0 = reminder_count(page)
            row = page.locator("div.rounded-md.border").filter(has_text=log["reminder_schedule"]).first
            w.step(page, "reminders", side, role, user,
                   f"The Admin's dashboard: the Action Center lists {n0} pending reminder(s), {log['reminder_schedule']} "
                   "among them.", pins=[pin(row, f"{log['reminder_schedule']}: pending", "info")])
            try:
                with other_tab(page, env, user) as tab_b:
                    row_b = tab_b.locator("div.rounded-md.border").filter(has_text=log["reminder_schedule"]).first
                    w.step(tab_b, "reminders", side, role, user, "A second tab of the same Admin marks it done.",
                           click=row_b.get_by_role("button", name=re.compile("Mark Done")))
                    settle(tab_b, 800)
                    tab_b.get_by_placeholder(re.compile("^e.g.")).fill("Marked done by the walk.")
                    t_click = time.time()
                    w.step(tab_b, "reminders", side, role, user, "Its remarks dialog: Mark as Done.",
                           click=tab_b.get_by_role("button", name="Mark as Done"))
                    n1, took = wait_until(page, lambda: reminder_count(page), lambda v: v == n0 - 1, since=t_click)
                    # The live panel is this branch's fix (76325a112); develop's panel waits for a reload.
                    w.check("reminders", role, f"{side}: the first tab's panel drops the reminder within {LIVE_S} s, "
                            "no reload" + ("" if side == "after" else " (develop has no live panel)"),
                            f"{n0} → {n1}, {took} s after the click" if took is not None else f"still {n1}",
                            took is not None if side == "after" else True, kind="must" if side == "after" else "info")
                bad = took is None and side == "after"
                w.step(page, "reminders", side, role, user,
                       f"Back in the first tab: the panel shows {n1} pending "
                       + (f"{took} s after the click, no reload." if took is not None else
                          "(it did not change: develop's panel refreshes only on a reload)." if side == "before" else
                          "(it did not change)."),
                       pins=[pin(page.get_by_role("button", name=re.compile(r"^Reminders \d+$")),
                                 f"Reminders {n1}", "must" if bad else "info")])
            finally:
                # Raised again by another user (the Administrator): the log is Pending once more.
                t_put = time.time()
                api.put("Reminder Schedule Log", log["name"], {"status": "Pending", "completed_by": None,
                                                              "completed_at": None, "remarks": None})
            if side == "before":
                continue  # develop's first tab never dropped it, so there is nothing to see come back
            n2, took2 = wait_until(page, lambda: reminder_count(page), lambda v: v == n0, since=t_put)
            w.check("reminders", role, f"{side}: a log raised again by another user shows within {LIVE_S} s",
                    f"{n1} → {n2}, {took2} s after the change" if took2 is not None else f"still {n2}",
                    took2 is not None)
            w.step(page, "reminders", side, role, user,
                   f"The Administrator puts the log back to Pending: the panel shows {n2} again"
                   f"{', ' + str(took2) + ' s after the change, no reload' if took2 is not None else ' only after a reload'}.",
                   pins=[pin(row, log["reminder_schedule"], "info" if took2 is not None else "must")])


# --- 5. useListUpdate: the users list ------------------------------------------------------------------------------

def open_user(page, cid: str, side: str, role: str, user: str, email: str, record: bool = True) -> None:
    def click(caption: str, target) -> None:
        if record:
            w.step(page, cid, side, role, user, caption, click=target)
        else:
            target.first.click()
            page.wait_for_load_state("networkidle")

    if not sub_menu(page, "Users").first.is_visible():
        click("Open Admin Options in the sidebar.", menu(page, "Admin Options"))
        settle(page, 600)
    click("Open Users under Admin Options.", sub_menu(page, "Users"))
    settle(page, 1500)
    page.get_by_placeholder(re.compile("^Search")).first.fill(email)
    settle(page, 2000)
    click("Find the user and open their profile.", page.locator("tbody a").first)
    page.get_by_text("User Profile", exact=True).wait_for(timeout=30_000)
    settle(page, 1500)


def edit_pencil(page):
    return page.locator("h2", has_text="User Profile").locator("xpath=following-sibling::button[1]")


def list_update() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        named = FACT[env]["named"]
        api = Api(env)
        new_first = "Walkrenamed"
        new_full = " ".join(x for x in (new_first, named["last_name"]) if x)
        with live(env, user) as (page, net):
            # Tab A opens the Users list first (its table listens to Nirmaan Users), then leaves it for the vendor.
            w.step(page, "list_update", side, role, user, "Open Admin Options in the sidebar.",
                   click=menu(page, "Admin Options"))
            settle(page, 600)
            w.step(page, "list_update", side, role, user, "Open Users: its table listens to Nirmaan Users too.",
                   click=sub_menu(page, "Users"))
            settle(page, 2000)
            open_vendor(page, "list_update", side, role, user, named["name"], named["vendor_name"])
            line = page.locator("p").filter(has_text=re.compile(r"^Created by\s*:")).first
            w.check("list_update", role, f"{side}: the vendor's overview names its creator", line.inner_text()
                    if line.count() else "no line", line.count() > 0 and named["full_name"] in line.inner_text())
            w.step(page, "list_update", side, role, user,
                   "The Users list is closed now (its listener gone). The vendor's overview names its creator from "
                   "the shared users list.", pins=[pin(line, line.inner_text() if line.count() else "", "info")])
            try:
                with other_tab(page, env, user) as tab_b:
                    open_user(tab_b, "list_update", side, role, user, named["owner"])
                    w.step(tab_b, "list_update", side, role, user, "A second tab of the Admin edits that user.",
                           click=edit_pencil(tab_b))
                    settle(tab_b, 1000)
                    tab_b.locator('input[name="first_name"]').fill(new_first)
                    t_click = time.time()
                    w.step(tab_b, "list_update", side, role, user, f"First name '{new_first}' (a made-up name), then "
                           "Update User.", click=tab_b.get_by_role("button", name="Update User"))
                    seen, took = wait_until(page, lambda: line.inner_text() if line.count() else "",
                                            lambda v: new_full in v, since=t_click)
                    w.check("list_update", role,
                            f"{side}: the first tab's overview shows the new name within {LIVE_S} s, no reload"
                            + ("" if side == "after" else " (develop has no live users list)"),
                            f"{took} s after the click: {seen}" if took is not None else f"still: {seen}",
                            took is not None if side == "after" else True, kind="must" if side == "after" else "info")
                w.step(page, "list_update", side, role, user,
                       ("The new name shows in the first tab " + f"{took} s after the click, no reload: closing the Users list did "
                        "not cut this screen out of the Nirmaan Users room (#1670)." if took is not None else
                        "The first tab still shows the old name: " + ("develop downloads the users list once and "
                        "never refreshes it live." if side == "before" else "the live refresh did not arrive.")),
                       pins=[pin(line, seen, "info" if took is not None or side == "before" else "must")])
            finally:
                api.put("User", named["owner"], {"first_name": named["first_name"]})
            back, took2 = wait_until(page, lambda: line.inner_text() if line.count() else "",
                                     lambda v: named["full_name"] in v)
            if side == "after":
                w.check("list_update", role, f"{side}: the name put back through the API shows within {LIVE_S} s",
                        f"in {took2} s" if took2 is not None else f"still: {back}", took2 is not None)


# --- Edit User: a mobile number in use ------------------------------------------------------------------------------

def edit_user() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        target, other = FACT[env]["mobiles"]
        with session(env, user, PW) as (page, _):
            sidebar_ready(page)
            open_user(page, "edit_user", side, role, user, target["name"])
            w.step(page, "edit_user", side, role, user, "Edit the user.", click=edit_pencil(page))
            settle(page, 1000)
            page.locator('input[name="mobile_no"]').fill(other["mobile_no"])
            w.step(page, "edit_user", side, role, user, "Give them a mobile number another user already has, then "
                   "Update User.", click=page.get_by_role("button", name="Update User"))
            settle(page, 2500)
            field_msg = page.get_by_text("This mobile number is already used by another user.")
            toasts = " / ".join(t.strip() for t in page.locator("li[role='status'], [data-sonner-toast], "
                                                                 "ol li").all_inner_texts() if t.strip())[:300]
            raw = "duplicate key" in toasts or "unique constraint" in toasts
            if side == "after":
                w.check("edit_user", role, "after: the Mobile field says it is already used by another user",
                        "shown" if field_msg.count() else f"not shown; toast: {toasts}", field_msg.count() > 0)
                w.check("edit_user", role, "after: no raw database text", toasts or "no toast", not raw)
            else:
                w.check("edit_user", role, "before (develop): what the refusal shows", toasts or "no toast", True,
                        kind="info")
            pins = [pin(field_msg, "The field's own message", "info")] if field_msg.count() else []
            if raw:
                pins.append(pin(page.locator("li[role='status'], ol li").first, "Raw database text in the toast.",
                                "info" if side == "before" else "must"))
            w.step(page, "edit_user", side, role, user, "After Update User: " + ("the field says the number is taken; "
                   "the form stays open." if field_msg.count() else f"the toast reads: {toasts or 'nothing'}."),
                   pins=pins)
            page.keyboard.press("Escape")
            now = Api(env).call("GET", f"/api/resource/User/{target['name']}")["data"].get("mobile_no")
            w.check("edit_user", role, f"{side}: the refused number was not saved", now or "empty",
                    now == target["mobile_no"])
            if now != target["mobile_no"]:
                Api(env).put("User", target["name"], {"mobile_no": target["mobile_no"]})


# --- Mark as Paid: the server's reason ------------------------------------------------------------------------------

def mark_paid() -> None:
    role, user = "Accountant", USERS["Accountant"]
    for side, env in SIDES:
        pay = FACT[env]["approved"]
        if not pay:
            w.check("mark_paid", role, f"{side}: an Approved PO payment, the only one on its PO", "none", False)
            continue
        api = Api(env)
        with session(env, user, PW) as (page, _):
            open_payments(page, "mark_paid", side, role, user)
            w.step(page, "mark_paid", side, role, user, "Open Payment need to paid.",
                   click=tab_button(page, "Payment need to paid"))
            settle(page, 2500)
            row = page.locator("tbody tr").filter(has_text=pay["document_name"]).first
            w.step(page, "mark_paid", side, role, user, f"The approved payment on {pay['document_name']}: Mark as Paid.",
                   click=row.get_by_role("button", name=re.compile("Mark as Paid")))
            settle(page, 1000)
            dialog = page.get_by_role("dialog").or_(page.get_by_role("alertdialog")).first
            try:
                api.method("nirmaan_stack.api.payments.payment_hold.set_payment_hold", {"name": pay["name"], "hold": 1})
                w.step(page, "mark_paid", side, role, user, "Meanwhile the Administrator puts this payment on hold. "
                       "The Accountant confirms.", click=dialog.get_by_role("button").filter(
                           has_text=re.compile("Mark|Confirm|Paid")).last)
                settle(page, 2500)
                body = page.locator("body").inner_text()
                names_hold = "is on hold" in body
                generic = "There was an error while updating the document" in body
                w.check("mark_paid", role, f"{side}: the failure " + ("names the hold (the server's reason)"
                        if side == "after" else "(develop) reads"),
                        "names the hold" if names_hold else ("the SDK's generic text" if generic else "neither"),
                        names_hold if side == "after" else True, kind="must" if side == "after" else "info")
                msg = page.get_by_text(re.compile("is on hold|There was an error while updating")).first
                w.step(page, "mark_paid", side, role, user, "The failure: " + ("the server's own reason." if names_hold
                       else "the SDK's generic text." if generic else "no reason shown."),
                       pins=[pin(msg, "The reason shown", "info" if names_hold or side == "before" else "must")]
                       if msg.count() else [])
            finally:
                api.method("nirmaan_stack.api.payments.payment_hold.set_payment_hold", {"name": pay["name"], "hold": 0})
                status = api.call("GET", f"/api/resource/Project Payments/{pay['name']}")["data"]["status"]
                w.check("mark_paid", role, f"{side}: the held payment stays Approved", status, status == "Approved")
                if status != "Approved":
                    api.put("Project Payments", pay["name"], {"status": "Approved"})


# --- The PO list: tables fit, Enter searches, owner names, no Projects / Vendors downloads, lazy pop-ups ------------

LIST_API = r"get_list_with_count_enhanced"
NUMBERS: dict = {}


def table_metrics(page) -> dict:
    """Where the pagination bar sits, the table scroll box's scrollbar width, and the scrollbar thumb rule."""
    return page.evaluate("""() => {
        const rows = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 &&
            /^Rows per page$/.test((e.textContent || '').trim()));
        const bar = rows ? rows.getBoundingClientRect() : null;
        const table = document.querySelector('table');
        let box = table, w = 0;
        while (box && box !== document.body) {
            const s = getComputedStyle(box);
            if (/(auto|scroll)/.test(s.overflowY) && box.scrollHeight > box.clientHeight) break;
            box = box.parentElement;
        }
        if (box && box !== document.body) w = box.offsetWidth - box.clientWidth - parseFloat(getComputedStyle(box).borderLeftWidth) - parseFloat(getComputedStyle(box).borderRightWidth);
        // Headless Chromium hides scrollbars, so their size and colour are read from the page's own rules (the last wins).
        let thumb = '', width = '';
        for (const sheet of document.styleSheets) {
            try {
                for (const r of sheet.cssRules) {
                    if (r.selectorText === '::-webkit-scrollbar-thumb' && r.style.backgroundColor) thumb = r.style.backgroundColor;
                    if (r.selectorText === '::-webkit-scrollbar' && r.style.width) width = r.style.width;
                }
            } catch (e) {}
        }
        return {bar_bottom: bar ? Math.round(bar.bottom) : null, viewport: innerHeight,
                page_scrolls: document.scrollingElement.scrollHeight > innerHeight + 1, scrollbar_rule: width, thumb};
    }""")


def po_list() -> None:
    role, user = "Admin", USERS["Admin"]
    owners = {}
    for side, env in SIDES:
        with session(env, user, PW) as (page, _):
            net = Net(page)
            sidebar_ready(page)
            start = net.mark()
            w.step(page, "po_list", side, role, user, "Open Purchase Orders from the sidebar.",
                   click=menu(page, "Purchase Orders"))
            settle(page, 2500)
            mark = net.mark()
            w.step(page, "po_list", side, role, user, "Open All POs: the Release PO list (release-po-select).",
                   click=tab_button(page, "All POs"))
            settle(page, 3500)
            # Whole lists only: the CEO Hold lookup (useCEOHoldProjects, a few names) is not the Projects download.
            whole = lambda dt, i: [x for x in net.since(i, rf"/api/resource/{dt}\?") if "CEO Hold" not in x["url"]]
            projects, vendors = whole("Projects", start), whole("Vendors", start)
            on_tab = whole("Projects", mark) + whole("Vendors", mark)
            NUMBERS.setdefault("po_list", {})[side] = {"Projects": [x["url"].split("?")[1][:120] for x in projects],
                                                       "Vendors": [x["url"].split("?")[1][:120] for x in vendors],
                                                       "on the All POs click": len(on_tab)}
            w.check("po_list", role, f"{side}: the Release PO list (All POs click) downloads no Projects / Vendors "
                    "list", f"{len(on_tab)} requests", not on_tab, kind="must" if side == "after" else "info")
            w.check("po_list", role, f"{side}: opening Purchase Orders downloads the Vendors list" +
                    (" no more" if side == "after" else " (develop)"),
                    f"Vendors {len(vendors)}, Projects {len(projects)} whole-list requests",
                    not vendors if side == "after" else bool(vendors), kind="must")
            heads = [h.strip() for h in page.get_by_role("columnheader").all_inner_texts()]
            idx = heads.index("Approved By") if "Approved By" in heads else None
            owners[side] = page.evaluate(
                """i => [...document.querySelectorAll('tbody tr')].slice(0, 20).map(tr => [...tr.children])
                     .filter(c => c.length > i).map(c => c[0].innerText.trim().split('\\n')[0] + ' | ' + c[i].innerText.trim())""",
                idx) if idx is not None else []
            m = table_metrics(page)
            NUMBERS.setdefault("tables", {})[side] = m
            fits = m["bar_bottom"] is not None and m["bar_bottom"] <= m["viewport"]
            w.check("tables_fit", role, f"{side}: the pagination bar is on screen without scrolling the page",
                    f"bar bottom {m['bar_bottom']} px, viewport {m['viewport']} px", fits if side == "after" else True,
                    kind="must" if side == "after" else "info")
            w.check("tables_fit", role, f"{side}: the scrollbar rule is " + ("4 px, brand colour" if side == "after"
                    else "(develop)") + " (headless Chromium hides scrollbars, so the page's CSS rule is read)",
                    f"width {m['scrollbar_rule']}; thumb {m['thumb']}",
                    m["scrollbar_rule"] == "4px" and "--primary" in m["thumb"] if side == "after" else True,
                    kind="must" if side == "after" else "info")
            col = page.get_by_role("columnheader", name="Approved By")
            w.step(page, "po_list", side, role, user,
                   f"The Release PO list. Opening Purchase Orders downloaded Vendors {len(vendors)} / Projects "
                   f"{len(projects)} whole lists; the All POs click {len(on_tab)}. 'Approved By' "
                   + ("reads owner_name from the row." if side == "after" else "is looked up in the users list."),
                   pins=([pin(col, "Approved By", "info")] if col.count() else []) +
                   ([pin(page.get_by_text("Rows per page", exact=True), f"Pagination bar ends at {m['bar_bottom']} px "
                     f"of {m['viewport']}", "info")] if fits else
                    [pin(page.locator("table").first, f"The pagination bar is off screen, at {m['bar_bottom']} px of "
                     f"a {m['viewport']} px window: the page must scroll to reach it.",
                     "info" if side == "before" else "must")]))
            w.step(page, "tables_fit", side, role, user,
                   f"{'Develop' if side == 'before' else 'This branch'}: scrollbars are {m['scrollbar_rule']} wide "
                   f"({m['thumb'] or 'no thumb rule'}); the pagination bar ends at {m['bar_bottom']} px of a "
                   f"{m['viewport']} px window.", pins=[pin(page.locator("table").first, "The table's scroll box",
                                                            "info")])
            # Row pop-ups: what the closed rows fetched, then what one opened pop-up fetches.
            remarks = net.matching(mark, r"po_remarks\.get_po_remarks")
            docs = net.since(mark, r"/api/resource/Procurement Orders/PO")
            NUMBERS.setdefault("popups", {})[side] = {"remarks lists": len(remarks), "PO docs": len(docs)}
            w.check("popups", role, f"{side}: closed rows fetch no remarks list and no PO document" +
                    ("" if side == "after" else " (develop)"),
                    f"{len(remarks)} remarks lists, {len(docs)} PO documents for {len(owners[side])}+ rows drawn",
                    (not remarks and not docs) if side == "after" else True, kind="must" if side == "after" else "info")
            mark2 = net.mark()
            w.step(page, "popups", side, role, user,
                   f"On opening, the closed rows made {len(docs)} PO document and {len(remarks)} remarks-list "
                   "request(s). Open one row's items card.",
                   click=page.locator("tbody tr").first.get_by_role("button", name="View items"))
            settle(page, 2000)
            opened = net.since(mark2, r"/api/resource/Procurement Orders/PO")
            card = page.locator("[data-radix-popper-content-wrapper]").first
            w.check("popups", role, f"{side}: opening the items card loads its PO", f"{len(opened)} PO document "
                    f"request(s); card {'open' if card.count() else 'not open'}", card.count() > 0)
            w.step(page, "popups", side, role, user, f"The items card is open: {len(opened)} PO document request(s) "
                   "made on opening it.", pins=[pin(card, "The items card", "info")] if card.count() else [])
            page.keyboard.press("Escape")
            settle(page, 500)
            # Enter searches at once.
            term = owners[side][0].split(" | ")[0] if owners[side] else "PO/"
            box = page.get_by_placeholder(re.compile("^Search")).first
            box.click()
            mark3 = net.mark()
            box.fill(term)
            t_enter = time.time()
            box.press("Enter")
            page.wait_for_timeout(1500)
            hits = [x for x in net.matching(mark3, LIST_API) if term in x["url"] + x["body"]]
            ms = round((hits[0]["t"] - t_enter) * 1000) if hits else None
            NUMBERS.setdefault("search", {})[side] = ms
            w.check("search", role, f"{side}: Enter sends the search " + ("at once (under 150 ms)" if side == "after"
                    else "(develop: after its 500 ms typing wait)"),
                    f"{ms} ms after Enter" if ms is not None else "no search request", (ms is not None and ms < 150)
                    if side == "after" else True, kind="must" if side == "after" else "info")
            settle(page, 1500)
            w.step(page, "search", side, role, user, f"Search '{term}' and press Enter: the list request left {ms} ms "
                   "after Enter.", pins=[pin(box, f"{ms} ms", "info")])
    pairs = list(zip(owners.get("before", []), owners.get("after", [])))
    diff = [f"{a} / {b}" for a, b in pairs if a != b]
    w.check("po_list", role, "'Approved By' names of the newest POs are the same on develop and on this branch",
            f"{len(pairs)} rows compared, all the same" if pairs and not diff else f"{len(pairs)} compared; {diff[:3]}",
            bool(pairs) and not diff)
    (out / "numbers.json").write_text(json.dumps(NUMBERS, indent=1))


# --- Payments list fields, the users list, rupees, the PO summary ----------------------------------------------------

def open_project(page, cid: str, side: str, role: str, user: str, big: dict) -> None:
    """Admin Options › Projects, search the project by name, open it (the open is a recorded step)."""
    sidebar_ready(page)
    if not sub_menu(page, "Projects").first.is_visible():
        menu(page, "Admin Options").click()
        settle(page, 600)
    sub_menu(page, "Projects").first.click()
    settle(page, 2000)
    page.get_by_placeholder(re.compile("^Search")).first.fill(big["project_name"])
    settle(page, 2500)
    w.step(page, cid, side, role, user, f"Admin Options › Projects: open the project with the most POs ({big['pos']}).",
           click=page.locator(f'tbody a[href*="{big["name"]}"]').first)


def pay_fields() -> None:
    """project-payments-list (6f876f01b) has no way in from the app: 'PO Wise' has no tab button (15 Sep), the project
    page's 'All Orders' and the customer page's Financials tab are commented out. Only a typed URL reaches it, so its
    numbers are recorded and the claim stays not walked."""
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        with session(env, user, PW) as (page, _):
            net = Net(page)
            sidebar_ready(page)
            mark = net.mark()
            page.goto(f"{base_url(env)}/project-payments?tab=PO%20Wise")
            settle(page, 6000)
            got = {}
            for dt in ("Procurement Orders", "Service Requests"):
                # The list's own downloads filter by status; the summary cards' unfiltered ones are not this change.
                reqs = [x for x in net.since(mark, rf"/api/resource/{dt}\?") if '"status",' in x["url"]]
                if not reqs:
                    continue
                x = reqs[-1]
                fields = re.search(r"fields=(\[[^&]*\])", x["url"])
                try:
                    size = len(x["r"].response().body())
                except Exception:
                    size = -1
                got[dt] = {"fields": json.loads(fields.group(1)) if fields else "?", "bytes": size}
            NUMBERS.setdefault("pay_fields", {})[side] = got
            for dt, g in got.items():
                star = g["fields"] == ["*"]
                w.check("pay_fields", role, f"{side} (typed URL): the PO Wise list's {dt} download asks for " +
                        ("the fields it reads, not '*'" if side == "after" else "(develop) every field"),
                        f"{'*' if star else str(len(g['fields'])) + ' fields'}, {g['bytes'] / 1e6:.1f} MB",
                        True, kind="info")
            w.step(page, "pay_fields", side, role, user, "Typed URL ?tab=PO Wise (no button leads here). Its PO / WO "
                   "downloads: " + ("; ".join(
                       f"{dt} {('* (every field)' if g['fields'] == ['*'] else str(len(g['fields'])) + ' fields')}, "
                       f"{g['bytes'] / 1e6:.1f} MB" for dt, g in got.items()) or "none seen") + ".")
    sizes = {s: {dt: g["bytes"] for dt, g in NUMBERS.get("pay_fields", {}).get(s, {}).items()} for s in ("before", "after")}
    w.status("pay_fields", "not_walked",
             "No screen leads to the changed list (project-payments-list): 'PO Wise' has no tab button since 15 Sep, the "
             "project page's 'All Orders' and the customer page's Financials tab are commented out. Reached by typed "
             f"URL only, for the numbers: bytes per download {sizes}. No test pins the field list.")
    (out / "numbers.json").write_text(json.dumps(NUMBERS, indent=1))


def users_once() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        named = FACT[env]["named"]
        with session(env, user, PW) as (page, _):
            net = Net(page)
            page.reload()
            settle(page, 3000)
            sidebar_ready(page)
            open_vendor(page, "users_once", side, role, user, named["name"], named["vendor_name"], record=False)
            menu(page, "Purchase Orders").click()
            settle(page, 2500)
            menu(page, "Work Orders").click()
            settle(page, 2500)
            lists = net.since(0, r"/api/resource/Nirmaan Users\?")
            mine = net.since(0, r"/api/resource/Nirmaan Users/")
            w.check("users_once", role, f"{side}: Nirmaan Users list downloads over load + 4 screens" +
                    (" (once)" if side == "after" else " (develop)"), str(len(lists)),
                    len(lists) == 1 if side == "after" else bool(lists), kind="must")
            w.check("users_once", role, f"{side}: the logged-in user's own record read" +
                    (" once (UserDataProvider)" if side == "after" else " (develop)"), str(len(mine)),
                    len(mine) == 1 if side == "after" else bool(mine), kind="nit" if side == "after" else "info")
            NUMBERS.setdefault("users_once", {})[side] = {"list": len(lists), "own record": len(mine)}
            w.step(page, "users_once", side, role, user,
                   f"After loading the app and opening Vendors, a vendor, Purchase Orders and Work Orders: "
                   f"{len(lists)} Nirmaan Users list download(s) and {len(mine)} read(s) of the user's own record.")
    (out / "numbers.json").write_text(json.dumps(NUMBERS, indent=1))


def po_summary() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        big = FACT[env]["big"]
        with session(env, user, PW) as (page, _):
            net = Net(page)
            mark = net.mark()
            open_project(page, "po_summary", side, role, user, big)
            settle(page, 3000)
            calls = net.matching(mark, r"generate_po_summary")
            if not calls:
                tab = page.get_by_role("button", name=re.compile("Material Usage")).or_(
                    page.get_by_role("tab", name=re.compile("Material Usage"))).first
                if tab.count():
                    tab.click()
                page.wait_for_timeout(500)
            for _ in range(120):
                calls = net.matching(mark, r"generate_po_summary")
                if calls and calls[0]["r"].response() is not None:
                    break
                page.wait_for_timeout(500)
            ms = None
            if calls and calls[0]["r"].response() is not None:
                calls[0]["r"].response().finished()
                ms = round(calls[0]["r"].timing["responseEnd"])
            NUMBERS.setdefault("po_summary", {})[side] = ms
            w.check("po_summary", role, f"{side}: generate_po_summary for {big['pos']} POs took", f"{ms} ms", True,
                    kind="info")
            settle(page, 1500)
            w.step(page, "po_summary", side, role, user, f"The project page. Its PO summary request took {ms} ms "
                   f"({'one query' if side == 'after' else 'one get_doc per PO, develop'}).")
    if all(NUMBERS.get("po_summary", {}).get(s) for s in ("before", "after")):
        b, a = NUMBERS["po_summary"]["before"], NUMBERS["po_summary"]["after"]
        w.check("po_summary", role, "this branch's PO summary is faster than develop's", f"{b} ms → {a} ms", a < b,
                kind="should")
    (out / "numbers.json").write_text(json.dumps(NUMBERS, indent=1))


def rupees() -> None:
    role, user = "Accountant", USERS["Accountant"]
    seen = {}
    for side, env in SIDES:
        vendor, vname = FACT[env]["vendor"], FACT[env]["vendor_name"]
        with session(env, user, PW) as (page, _):
            open_vendor(page, "rupees", side, role, user, vendor, vname, record=False)
            page.get_by_role("button", name="Payments", exact=True).first.click()
            settle(page, 3000)
            texts = page.locator("main").inner_text()
            seen[side] = {"rupees": re.findall(r"-?₹[\d,]+(?:\.\d+)?", texts),
                          "dates": re.findall(r"\b\d{2}-[A-Z][a-z]{2}-\d{4}\b", texts)}
            w.step(page, "rupees", side, role, user, f"The vendor's Paid payments: {len(seen[side]['rupees'])} rupee "
                   f"figures and {len(seen[side]['dates'])} dates on screen.",
                   pins=[pin(page.get_by_text(re.compile(r"^₹[\d,]+")).first, "A rupee figure", "info")])
    for k in ("rupees", "dates"):
        same = seen["before"][k] == seen["after"][k] and bool(seen["after"][k])
        w.check("rupees", role, f"every {k[:-1]} on the screen reads the same on develop and on this branch",
                f"{len(seen['after'][k])} {k}, all the same" if same else
                f"differ: {[x for x in zip(seen['before'][k], seen['after'][k]) if x[0] != x[1]][:3]}", same)
    (out / "rupee_text.json").write_text(json.dumps(seen, indent=1))


NO_SCREEN = {
    "137738501 memoised rows": "frontend/src/components/data-table/new-data-table.dom.test.tsx (5 redraw tests)",
    "96f12a444 rows do not force layout": "new-data-table.dom.test.tsx (no row height read while drawing) and "
                                          "tableHeightModel.test.ts (virtualRowSize)",
    "c81e6e7a8 virtual rows measure their real height": "tableHeightModel.test.ts (virtualRowSize); no test of its own",
    "2fe8d3572 rows read no user or toast state": "no test; frontend/CODING_STANDARDS.md records the rule",
    "7a4e78148 14 more tables name from the row": "no test; 'Raised by' (2b) and 'Approved By' (PO list) are compared "
                                                  "here, name for name",
    "269af9a96 per-user list cache key": "no test, and no screen: every useServerDataTable caller leaves shouldCache "
                                         "false, so the server cache is never used",
    "4eab23e41 formatters built once": "FormatDate.test.ts and FormatPrice.test.ts (compare with toLocaleString); "
                                       "the printed text is compared here under 'Rupee figures'",
}


def no_screen() -> None:
    w.status("no_screen", "not_walked", "No screen can show these; the tests that pin each: " +
             "; ".join(f"{k}: {v}" for k, v in NO_SCREEN.items()) + ".")


PLAN = [("notif", notif), ("rejected", rejected), ("pay_cheque", pay_cheque), ("vendor_pay", vendor_pay),
        ("reminders", reminders), ("list_update", list_update), ("edit_user", edit_user), ("mark_paid", mark_paid),
        ("po_list", po_list), ("pay_fields", pay_fields), ("users_once", users_once), ("po_summary", po_summary),
        ("rupees", rupees), ("no_screen", no_screen)]

for cid, fn in PLAN:
    if not only or fn.__name__ in only:
        guarded(cid, fn)

for c in w.data["changes"]:
    if c["status"] == "failed" or (c["status"] == "not_walked" and c["reason"]):
        continue
    if not any(x["change"] == c["id"] for x in w.data["steps"]):
        continue
    bad = [k for k in w.data["checks"] if k["change"] == c["id"] and not k["ok"] and k["kind"] == "must"]
    musts = [p for s in w.data["steps"] if s["change"] == c["id"] for p in s["pins"] if p["kind"] == "must"]
    w.status(c["id"], "failed" if bad or musts else "seen",
             "; ".join(f"{k['role']}: expected {k['expect']}, saw {k['seen']}" for k in bad))
print(w.save())
