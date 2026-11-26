"""Walk branch redis/table-cache-v2 (perf/nov-v2 plus 9 commits: live table refresh through one shared list_update hub,
sidebar badge counts and table pages kept in Redis, the room-count fix): after = the branch's env, before = GitHub
develop's env (31d29db45). There is no PR; the claims are the commits. perf/nov-v2's own claims are not walked again.

    python3 walk_branch_redis_table_cache_v2.py <out dir> [function ...]     # from the checkout root; envs served

Not `python3 -I`: Playwright sits in the user's site-packages. Both envs must be served (`devenv.sh serve`) with
realtime: each env's SPA points at a Socket.IO server on its own port through an untracked `frontend/.env.local`
(`VITE_SOCKET_PORT`), the recipe in `.claude/skills/verify/bin/walk_live_refresh.py`. A live claim stops when its page
opens no socket. The after env runs with the table cache on (the default).

Users come from `free_users.py`, one per role, with the walk's password set on both envs; a second user of the same
access (any enabled user of that role profile) asks the API only. Records are picked from what each screen shows, or
by query on each env, never by a fixed id. The walk's writes stay on its throwaway envs: "another user" (the
Administrator, through the REST API) saves a record without changing a field (only `modified` moves, so the row moves
to the top of a list sorted by modified), renames a customer and puts the name back, moves a payment to Approved and
back to Requested, and turns `nc_table_cache` off and on again.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import re
import statistics
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

AFTER, BEFORE = "madhu-redis-table-cache-v2-walk", "madhu-rtc-v2-walk-base"
SIDES = (("before", BEFORE), ("after", AFTER))
PW = "WalkPw-Rtc2"
DEVENV = str(ROOT / "scripts" / "devenv" / "devenv.sh")
PROFILES = {
    "Admin": "Nirmaan Admin Profile",
    "Accountant": "Nirmaan Accountant Profile",
    "Procurement Executive": "Nirmaan Procurement Executive Profile",
    "Project Manager": "Nirmaan Project Manager Profile",
}
LIVE_S = 15  # how long a live change may take to show
TABLE_API = r"get_list_with_count_enhanced|get_pr_summary_list|get_approval_queue\.get_approval_queue(?!_counts)"
COUNTS_API = r"sidebar_counts\.sidebar_counts"

if len(sys.argv) < 2:
    sys.exit(__doc__)
out = Path(sys.argv[1]).expanduser()
only = set(sys.argv[2:])
os.environ["VERIFY_OUT"] = str(out / "drive")
HEAD, BASE = "b33c1cc0e", "31d29db45"
author = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%an", HEAD], capture_output=True,
                        text=True).stdout.strip()
w = Walk(out, pr={"number": "redis/table-cache-v2", "title": "redis/table-cache-v2: live table refresh, Redis table "
                  "pages and badge counts", "url": "", "author": author, "head": HEAD, "base": BASE},
         envs={"after": AFTER, "before": BEFORE},
         data_note="each env is a copy of the production backup of 2026-11-25; the walk's writes stay in its env")

w.change("live", "1. Tables refresh live, quietly",
         "Another user's save of a row of an open table (Procurement Requests, Purchase Orders, Work Orders, Payments › "
         "All Payments) updates the rows about 1 s later with no reload and no 'Data Updated' toast (develop shows the "
         "toast). While the viewer is busy (pointer on the rows, a row ticked, a dialog open) the refresh waits behind "
         "a 'New changes · Refresh' chip and applies once they stop; a hidden tab refreshes when shown "
         "(f3915e00f, 4d041dd93).")
w.change("no_loop", "2. Six tables no longer refetch without end",
         "Cheque Tracker, Payees, a project's TDS history, the GST Requests tab, Reimbursement Claims and a user's "
         "Reimbursements tab send no list request after their first load while nothing changes (634d9608b). "
         "Counted over 60 s on both envs. The loop came in with this branch's own f3915e00f, so develop never had "
         "it: its count is 0 too.")
w.change("spread", "3. Refreshes spread over a few seconds",
         "One change reaches every open screen at once; each table waits 1-2 s and each badge set 2-6 s, picked at "
         "random, so their requests do not all leave at the same instant (8eb785e5e).")
w.change("badges", "4. Badge counts: same numbers, live, read from Redis",
         "The sidebar and tab counts read the same numbers as develop for each role, move within seconds when a "
         "counted record changes state, and a second user of the same scope is answered from Redis (6d0e31373). "
         "Fixes develop's stale 'Approve PR' count: on develop it stays at the old number after a PR is approved "
         "elsewhere until a reload, while the list below already shows one PR fewer.")
w.change("table_cache", "5. Table pages kept in Redis, never stale, never across access",
         "The same table, page and filters return the same rows on both envs for each role. A second user with the "
         "same access is served from Redis. After a save the next page load shows the change at once (R2: the version "
         "moves before the live refresh goes out). A user with narrower access never sees another group's rows. "
         "`nc_table_cache: 0` turns the cache off (690493905, 1f6f4540c).")
w.change("r1729", "6. #1729 still holds: a renamed customer stays renamed",
         "walk_live_refresh.py step 02b: the Customers list after a rename, left and opened again, shows the new name. "
         "On this branch the live redraw (step 02) shows no toast.")
w.change("rooms", "7. Closing one screen does not cut another off a doctype room",
         "The hub holds each doctype room through #1670's shared room count (src/lib/socketRooms.ts), so a room is "
         "left only when its last holder lets go (b33c1cc0e).")
w.change("kept", "8. Develop's live features kept in the conflict files",
         "On this branch: the GST request decision refreshes the Payment Summary cards at once "
         "(RenderProjectPaymentsComponent); expense rows in the Payments tables refresh when an expense changes "
         "(liveRefreshDoctypes); the vendor categories form, project type pickers, new inflow dialog and invoice "
         "summary cards still refresh live. Not walked: the GST cards (no pending GST request in the data) and the "
         "vendor categories form (no screen uses it); see the checks.")


# --- Plumbing -------------------------------------------------------------------------------------------------------

def db(env: str, name: str, code: str, *args: str):
    """Run `code` on env's DB through `devenv.sh script` and return what it printed after `JSON `. Saved under db/."""
    src = out / "db" / f"{name}.{env}.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("import json, sys, time\nimport frappe\n\ndef emit(o):\n    print('JSON ' + json.dumps(o, default=str))\n\n"
                   + code)
    res = subprocess.run([DEVENV, "script", env, str(src), *args], capture_output=True, text=True, timeout=600,
                         cwd=ROOT)
    src.with_suffix(".txt").write_text(res.stdout + res.stderr)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("JSON ")), None)
    if line is None:
        raise RuntimeError(f"db script {src} printed no JSON line; see {src.with_suffix('.txt')}")
    return json.loads(line[5:])


class Api:
    """One user through the env's REST API: the Administrator is the 'other user' of a live change; a walk user asks
    the table and count APIs directly."""

    def __init__(self, env: str, user: str = "Administrator"):
        self.base = base_url(env)
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.open(urllib.request.Request(f"{self.base}/api/method/login",
                                            data=urllib.parse.urlencode({"usr": user, "pwd": PW}).encode()))

    def call(self, verb: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.base}{urllib.parse.quote(path)}", method=verb,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(self.op.open(req).read() or b"{}")

    def put(self, doctype: str, name: str, body: dict) -> dict:
        return self.call("PUT", f"/api/resource/{doctype}/{name}", body)["data"]

    def method(self, method: str, body: dict) -> dict:
        return self.call("POST", f"/api/method/{method}", body)

    def timed(self, method: str, body: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        r = self.method(method, body)
        return r, round((time.perf_counter() - t0) * 1000)


class Net:
    """Every request a page sends, with its decoded URL, body and time."""

    def __init__(self, page):
        self.reqs: list[dict] = []
        self.frames: list[tuple[float, str]] = []  # Socket.IO frames the page sent (room joins and leaves)
        page.on("websocket", lambda ws: ws.on("framesent", lambda p: self.frames.append((time.time(), str(p)))))
        page.on("request", lambda r: self.reqs.append({"url": urllib.parse.unquote_plus(r.url), "t": time.time(),
                                                        "body": urllib.parse.unquote_plus(r.post_data or ""), "r": r}))

    def mark(self) -> int:
        return len(self.reqs)

    def matching(self, i: int, pattern: str) -> list[dict]:
        """Requests since i whose URL or body matches."""
        return [x for x in self.reqs[i:] if re.search(pattern, x["url"] + " " + x["body"])]


def reply(req: dict) -> dict | None:
    """The JSON message a table request got back, or None while it has none."""
    try:
        resp = req["r"].response()
        if resp is None:
            return None
        msg = resp.json().get("message")
        return json.loads(msg) if isinstance(msg, str) else msg
    except Exception:
        return None


def rows_of(msg: dict | None) -> list[dict]:
    if not msg:
        return []
    for k in ("data", "rows", "items"):
        if isinstance(msg.get(k), list):
            return msg[k]
    return []


# Records what a live refresh looks like on the page: a toast ever shown, a page load, the socket.
WATCH_JS = """() => {
  window.__walkMark = window.__walkMark || Date.now();
  if (window.__walkToasts) return;
  window.__walkToasts = [];
  new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) {
      const t = (n.innerText || n.textContent || '');
      if (/Data Updated/.test(t)) window.__walkToasts.push(Date.now());
  }}).observe(document.body, {childList: true, subtree: true});
}"""

# Lets a walk mark the tab hidden: headless Chromium always reports a page visible, so the walk sets what the page
# reads (document.visibilityState) and fires the event a real tab switch fires.
HIDE_JS = """() => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => window.__walkHidden ? 'hidden' : 'visible'});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => !!window.__walkHidden});
}"""


def watch(page) -> int:
    page.evaluate(WATCH_JS)
    return page.evaluate("window.__walkMark")


def toasts_since(page, t_ms: float) -> int:
    return page.evaluate("t => (window.__walkToasts || []).filter(x => x >= t).length", t_ms)


def same_load(page, mark: int) -> bool:
    return page.evaluate("window.__walkMark") == mark


@contextlib.contextmanager
def live_page(env: str, user: str):
    """A logged-in page whose socket carries the user's session. The SPA opens its socket on the login page, as
    Guest, and keeps it after logging in, so the page is loaded again first, as a returning user's is."""
    with session(env, user, PW) as (page, _):
        sockets: list[str] = []
        page.on("websocket", lambda ws: sockets.append(ws.url))
        page.add_init_script(f"({HIDE_JS})()")
        net = Net(page)
        page.reload()
        settle(page, 2500)
        sidebar_ready(page)
        if not any("socket.io" in s for s in sockets):
            raise RuntimeError(f"{env}: the page opened no Socket.IO connection, so a live claim cannot be walked")
        yield page, net


@contextlib.contextmanager
def other_tab(page, env: str, user: str):
    """A second browser context of the same browser (its own cookies and socket), logged in as user and loaded again
    so its socket carries the session."""
    ctx = page.context.browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        tab = ctx.new_page()
        tab.add_init_script(f"({HIDE_JS})()")
        net = Net(tab)
        login(tab, base_url(env), user, PW)
        tab.reload()
        settle(tab, 2500)
        sidebar_ready(tab)
        yield tab, net
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
    """Run fn; on an error drop what it recorded and try once more; a second error fails the change."""
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


def tab_button(page, label: str):
    """A tab drawn as a button, by the label before its count; a `/` in the label matches any character."""
    return page.get_by_role("button", name=re.compile(rf"^{re.escape(label).replace('/', '.')}\s*\d+$"))


def tab_count(page, label: str) -> int | None:
    t = tab_button(page, label)
    if not t.count():
        return None
    m = re.search(r"(\d+)\s*$", t.first.inner_text().strip())
    return int(m.group(1)) if m else None


def first_row(page):
    return page.locator("tbody tr").first


def row_with(page, text: str):
    """The drawn row with a cell reading exactly `text` (its first line)."""
    return page.locator("tbody tr").filter(
        has=page.locator("td", has_text=re.compile(rf"^\s*{re.escape(text)}\s*(\n|$)"))).first


def dom_rank(page, text: str) -> int | None:
    """Where the row showing `text` sits in the drawn table (0 = top), or None when it is not drawn."""
    i = page.evaluate("""t => [...document.querySelectorAll('tbody tr')].findIndex(tr =>
        [...tr.querySelectorAll('td')].some(td => td.innerText.trim().split('\\n')[0].trim() === t))""", text)
    return None if i < 0 else i


def last_rows(net: Net, start: int = 0) -> list[dict]:
    """Rows of the newest table reply since `start` that has one."""
    for x in reversed(net.matching(start, TABLE_API)):
        rows = rows_of(reply(x))
        if rows:
            return rows
    return []


# --- Users and facts ------------------------------------------------------------------------------------------------

def pick_users() -> dict:
    users = {}
    for role, profile in PROFILES.items():
        res = subprocess.run([DEVENV, "script", AFTER, ".claude/skills/verify/bin/free_users.py", profile],
                             capture_output=True, text=True, cwd=ROOT, timeout=300)
        line = next(ln for ln in res.stdout.splitlines() if ln.startswith(profile))
        users[role] = line[len(profile):].split()[0]
    # A second user of the same access, for the Redis claims: any enabled user of the role profile with no user
    # permission (full access), other than the first.
    second = db(AFTER, "second_users", '''
users = json.loads(sys.argv[1]); res = {}
for role in ("Admin", "Accountant"):
    prof = {"Admin": "Nirmaan Admin Profile", "Accountant": "Nirmaan Accountant Profile"}[role]
    rows = frappe.db.sql("""SELECT nu.name FROM "tabNirmaan Users" nu JOIN "tabUser" u ON u.name = nu.name
        WHERE u.enabled = 1 AND nu.role_profile = %s AND nu.name != %s
          AND NOT EXISTS (SELECT 1 FROM "tabUser Permission" p WHERE p.user = nu.name)
        ORDER BY nu.name LIMIT 1""", (prof, users[role]))
    res[role + " 2"] = rows[0][0] if rows else None
emit(res)
''', json.dumps(users))
    users.update({k: v for k, v in second.items() if v})
    for _, env in SIDES:
        subprocess.run([DEVENV, "run", env, "set-admin-password", PW], capture_output=True, cwd=ROOT, timeout=300)
        for email in users.values():
            subprocess.run([DEVENV, "run", env, "set-password", email, PW], capture_output=True, cwd=ROOT, timeout=300,
                           check=True)
    (out / "users.json").write_text(json.dumps(users, indent=1))
    return users


out.mkdir(parents=True, exist_ok=True)
USERS = json.loads((out / "users.json").read_text()) if (out / "users.json").exists() else pick_users()

# Read on each env: the records the walk uses, and what each walk user may see of them.
FACTS = r'''
users = json.loads(sys.argv[1]); q = frappe.db.sql
def projects_of(u):
    return sorted(frappe.get_all("User Permission", filters={"user": u, "allow": "Projects"}, pluck="for_value"))
def sees(u, doctype, filters):
    frappe.set_user(u)
    n = len(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=0))
    frappe.set_user("Administrator")
    return n
reimb_user = q("""SELECT owner FROM "tabReimbursement" GROUP BY owner ORDER BY COUNT(*) DESC, owner LIMIT 1""")[0][0]
tds_project = q("""SELECT tdsi_project_id FROM "tabProject TDS Item List" WHERE COALESCE(tdsi_project_id, '') != ''
    GROUP BY tdsi_project_id ORDER BY COUNT(*) DESC, tdsi_project_id LIMIT 1""")[0][0]
pending_pr = q("""SELECT name FROM "tabProcurement Requests" WHERE workflow_state = 'Pending'
    ORDER BY creation DESC LIMIT 1""")[0][0]
customer = q("""SELECT name, company_name FROM "tabCustomers" WHERE COALESCE(company_name, '') != ''
    ORDER BY creation DESC LIMIT 1""", as_dict=True)[0]
project_type = q("""SELECT name FROM "tabProject Types" ORDER BY creation DESC LIMIT 1""")[0][0]
vendor_invoice = q("""SELECT name FROM "tabVendor Invoices" ORDER BY creation DESC LIMIT 1""")[0][0]
inflow = q("""SELECT i.name, i.project, p.project_name FROM "tabProject Inflows" i JOIN "tabProjects" p ON p.name = i.project
    ORDER BY i.creation DESC LIMIT 1""", as_dict=True)[0]
pm, pe = users["Project Manager"], users["Procurement Executive"]
emit({"reimb_user": reimb_user, "tds_project": tds_project, "pending_pr": pending_pr, "customer": customer,
      "project_type": project_type, "vendor_invoice": vendor_invoice, "inflow": inflow,
      "gst_pending": frappe.db.count("GST Payment Request", {"status": "Pending"}),
      "pm_projects": projects_of(pm), "pe_projects": projects_of(pe),
      "pm_prs": sees(pm, "Procurement Requests", {}), "pe_prs": sees(pe, "Procurement Requests", {}),
      "all_prs": frappe.db.count("Procurement Requests"),
      "sees": {
          "Admin: the user's reimbursements": sees(users["Admin"], "Reimbursement", {"owner": reimb_user}),
          "Admin: the project's TDS items": sees(users["Admin"], "Project TDS Item List", {"tdsi_project_id": tds_project}),
          "Admin: the pending PR": sees(users["Admin"], "Procurement Requests", {"name": pending_pr}),
          "Accountant: the customer": sees(users["Accountant"], "Customers", {"name": customer["name"]}),
          "Project Manager: PRs of their projects": sees(pm, "Procurement Requests", {}),
          "Procurement Executive: PRs of their projects": sees(pe, "Procurement Requests", {}),
      }})
'''
FACT = {env: db(env, "facts", FACTS, json.dumps(USERS)) for _, env in SIDES}
SEES_CHANGE = {"Admin: the user's reimbursements": "no_loop", "Admin: the project's TDS items": "no_loop",
               "Admin: the pending PR": "badges", "Accountant: the customer": "r1729"}
for side, env in SIDES:
    for what, n in FACT[env]["sees"].items():
        w.check(SEES_CHANGE.get(what, "table_cache"), what.split(":")[0], f"{what} on {side}: at least 1 row", str(n),
                n > 0)


# --- 1. Live, quiet table refresh -----------------------------------------------------------------------------------

# (label, role, sidebar entry, tab button or None, doctype)
LIVE_TABLES = [
    ("Procurement Requests › All PRs", "Procurement Executive", "Procurement Requests", "All PRs", "Procurement Requests"),
    ("Purchase Orders", "Procurement Executive", "Purchase Orders", None, "Procurement Orders"),
    ("Work Orders › All WO", "Admin", "Work Orders", "All WO", "Service Requests"),
]
HOLD_API = "nirmaan_stack.api.payments.payment_hold.set_payment_hold"


def open_table(page, net: Net, cid: str, side: str, role: str, user: str, entry: str, tab: str | None,
               record: bool = True, min_rows: int = 4) -> list[dict]:
    """Open a table the way its user does; return the rows of its first reply."""
    sidebar_ready(page)
    mark = net.mark()
    if record:
        w.step(page, cid, side, role, user, f"Open {entry} from the sidebar.", click=menu(page, entry))
    else:
        menu(page, entry).first.click()
    settle(page, 2500)
    if tab:
        tab_mark = net.mark()
        if record:
            w.step(page, cid, side, role, user, f"Open its {tab} tab.", click=tab_button(page, tab))
        else:
            tab_button(page, tab).first.click()
        settle(page, 2500)
        if net.matching(tab_mark, TABLE_API):
            mark = tab_mark  # else the tab was already open (the role's default) and sent no new request
    rows, _ = wait_until(page, lambda: last_rows(net, mark), lambda r: len(r) >= min_rows, 20)
    return rows


def key_of(row: dict, doctype: str) -> str:
    """The text the table draws for a row: its name, or for a payment the PO / WO it is against."""
    if doctype == "Project Payments":
        return row.get("document_name")
    if doctype == "Procurement Requests":
        return str(int(row["name"].split("-")[-1]))  # the list draws PR-00107-007055 as 7055
    if doctype == "Service Requests":
        return row["name"][-5:]  # and SR-00187-001191 as 01191
    return row["name"]


def target_row(rows: list[dict], doctype: str, skip: int = 2) -> dict:
    """A row below the top whose drawn key is unique on the page, so its place can be read on screen."""
    keys = [key_of(r, doctype) for r in rows]
    for r in rows[skip:]:
        if keys.count(key_of(r, doctype)) == 1:
            return r
    raise RuntimeError(f"no row with a unique key on the first page of {doctype}")


def live() -> None:
    api = {env: Api(env) for _, env in SIDES}
    for table in LIVE_TABLES:
        if not os.environ.get("WALK_SKIP_TABLES"):  # a rerun of the Payments part alone
            guarded("live", lambda: live_table(api, *table))
    guarded("live", lambda: live_payments(api))


def held(page, key: str) -> bool:
    """The payment row drawn for `key` shows 'On Hold' in its Mark as Paid slot."""
    return row_with(page, key).get_by_text("On Hold", exact=True).count() > 0


def quiet_checks(page, side: str, role: str, what: str, took, load: int, t_ms: float, mark: int, net: Net,
                 cid: str = "live") -> list:
    """The two checks of a live change (shown in time with no reload; no toast on this branch) and the toast's pin."""
    reqs, toasts = net.matching(mark, TABLE_API), toasts_since(page, t_ms)
    w.check(cid, role, f"{side}: {what} within {LIVE_S} s, no reload",
            f"shown in {took} s, same page load: {same_load(page, load)}" if took is not None else
            f"not shown after {LIVE_S} s", took is not None and same_load(page, load))
    w.check(cid, role, f"{side}: " + ("no 'Data Updated' toast" if side == "after" else
            "develop shows the 'Data Updated' toast"), f"{toasts} toast(s), {len(reqs)} table request(s)",
            toasts == 0 if side == "after" else True, kind="must" if side == "after" else "info")
    toast = page.get_by_text("Data Updated", exact=True)
    return [pin(toast.first, "Develop's 'Data Updated' toast" if side == "before" else "A toast on a quiet refresh",
                "info" if side == "before" else "must")] if toast.count() else []


def live_payments(api: dict) -> None:
    """Payments: the Accountant's 'Payment need to paid' tab (sorted by approval date, so a plain save moves no row).
    The Administrator holds a payment and releases it: the row shows, then drops, 'On Hold'. Then a ticked row."""
    role, user = "Accountant", USERS["Accountant"]
    for side, env in SIDES:
        with live_page(env, user) as (page, net):
            rows = open_table(page, net, "live", side, role, user, "Project Payment & Expense", "Payment need to paid",
                              min_rows=2)
            keys = [r.get("document_name") for r in rows]
            free = [r for r in rows if r.get("document_name") and keys.count(r["document_name"]) == 1
                    and not held(page, r["document_name"])]
            if not free:
                raise RuntimeError(f"{env}: no unheld payment with its own order in 'Payment need to paid'")
            row = free[0]
            key = row["document_name"]
            # The row the viewer ticks: any other drawn row (an expense row counts).
            other = page.locator("tbody tr").filter(has=page.get_by_role("checkbox")).filter(
                has_not=page.locator("td", has_text=re.compile(rf"^\s*{re.escape(key)}\s*(\n|$)"))).first
            load = watch(page)
            w.step(page, "live", side, role, user, f"Payment need to paid: the payment against {key} offers Mark as "
                   "Paid. The Administrator now puts it on hold in another session.",
                   pins=[pin(row_with(page, key), key, "info")])
            t_ms, mark, t0 = page.evaluate("Date.now()"), net.mark(), time.time()
            try:
                api[env].method(HOLD_API, {"name": row["name"], "hold": 1})
                _, took = wait_until(page, lambda: held(page, key), bool, since=t0)
                page.wait_for_timeout(2500)
                pins = quiet_checks(page, side, role, "Payments shows another user's hold", took, load, t_ms, mark, net)
                w.step(page, "live", side, role, user, f"{took} s later the row shows 'On Hold', no reload.",
                       pins=[pin(row_with(page, key).get_by_text("On Hold", exact=True) if took is not None
                                 else row_with(page, key), f"On Hold after {took} s" if took is not None else
                                 "Not held", "info" if took is not None else "must")] + pins)
            finally:
                api[env].method(HOLD_API, {"name": row["name"], "hold": 0})
            _, took = wait_until(page, lambda: not held(page, key), bool)
            w.check("live", role, f"{side}: the release shows too", f"'On Hold' gone in {took} s" if took is not None
                    else "still 'On Hold'", took is not None)
            # A ticked row: the viewer ticks another row, the Administrator holds the payment again. First the
            # release's own refreshes settle (a second one can follow 1-2 s after the first).
            page.wait_for_timeout(6000)
            box = other.get_by_role("checkbox")
            if not box.count():
                w.check("live", role, f"{side}: another row of the tab can be ticked", "no other row with a checkbox",
                        False)
                continue
            w.step(page, "live", side, role, user, "Busy viewer: tick another row of the tab.", click=box)
            page.wait_for_timeout(800)
            mark, t_ms = net.mark(), page.evaluate("Date.now()")
            try:
                t0 = time.time()
                api[env].method(HOLD_API, {"name": row["name"], "hold": 1})
                page.wait_for_timeout(5000)
                reqs = net.matching(mark, TABLE_API)
                chip = page.get_by_role("button", name=re.compile("New changes"))
                had_chip = chip.count() > 0
                what = ", ".join(f"{x['url'].split('/api/method/')[-1][:90]} {x['body'][:160]} at {x['t'] - t0:+.1f} s"
                                 for x in reqs)
                if side == "after":
                    w.check("live", role, "after: with a row ticked, the hold waits behind 'New changes · Refresh'",
                            f"chip {'shown' if chip.count() else 'not shown'}, {len(reqs)} table request(s) in 5 s "
                            f"({what or 'none'}), 'On Hold' drawn: {held(page, key)}",
                            chip.count() > 0 and not reqs and not held(page, key))
                else:
                    w.check("live", role, "before: develop refreshes at once with a row ticked",
                            f"{len(reqs)} table request(s), {toasts_since(page, t_ms)} toast(s), 'On Hold' drawn: "
                            f"{held(page, key)}", True, kind="info")
                w.step(page, "live", side, role, user, f"The Administrator holds {key} while a row is ticked. "
                       + ("5 s later nothing has moved; the chip offers the change. Click it." if chip.count() else
                          f"{len(reqs)} table request(s) in 5 s; 'On Hold' drawn: {held(page, key)}."),
                       click=chip if chip.count() else None,
                       pins=[] if chip.count() else [pin(row_with(page, key), f"Held: {held(page, key)}", "info")])
                if side == "after" and had_chip:
                    _, took = wait_until(page, lambda: held(page, key), bool, 8)
                    tick = other.get_by_role("checkbox").get_attribute("data-state")
                    w.check("live", role, "after: the chip applies the change and the tick stays",
                            f"'On Hold' in {took} s, tick {tick}", took is not None and tick == "checked")
                    w.step(page, "live", side, role, user, f"After 'Refresh': 'On Hold' shows; the ticked row is "
                           f"still ticked ({tick}).", pins=[pin(row_with(page, key), "On Hold", "info")])
            finally:
                api[env].method(HOLD_API, {"name": row["name"], "hold": 0})


def live_table(api: dict, label: str, role: str, entry: str, tab: str | None, doctype: str) -> None:
    user = USERS[role]
    for side, env in SIDES:
        with live_page(env, user) as (page, net):
            rows = open_table(page, net, "live", side, role, user, entry, tab)
            row = target_row(rows, doctype)
            key = key_of(row, doctype)
            load = watch(page)
            w.step(page, "live", side, role, user,
                   f"{label}: {key} is row {dom_rank(page, key) + 1}. The Administrator now saves it in another "
                   "session (no field changed, so it moves to the top of this list sorted by last change).",
                   pins=[pin(row_with(page, key), f"{key}", "info")])
            t_ms, mark, t0 = page.evaluate("Date.now()"), net.mark(), time.time()
            api[env].put(doctype, row["name"], {})
            _, took = wait_until(page, lambda: dom_rank(page, key), lambda r: r == 0, since=t0)
            page.wait_for_timeout(2500)  # a toast or a second request after the first redraw
            pins = quiet_checks(page, side, role, f"{label} shows another user's save on top", took, load, t_ms,
                                mark, net)
            w.step(page, "live", side, role, user,
                   f"{took} s later {key} is on top" + (", no reload; " if took is not None else " (not seen); ")
                   + f"{len(net.matching(mark, TABLE_API))} table request(s), {toasts_since(page, t_ms)} 'Data "
                   "Updated' toast(s).",
                   pins=[pin(first_row(page), f"{key} on top after {took} s", "info" if took is not None
                             else "must")] + pins)
            if label.startswith("Procurement Requests"):
                busy(page, net, api[env], side, role, user, doctype, rows)


def busy(page, net: Net, api: Api, side: str, role: str, user: str, doctype: str, rows: list[dict]) -> None:
    """The same table while the viewer is busy: pointer on the rows, then a hidden tab."""
    fresh = last_rows(net)
    row = target_row(fresh or rows, doctype, skip=3)
    key = key_of(row, doctype)
    box = page.locator("tbody tr").nth(1).bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2)
    page.wait_for_timeout(500)
    t_ms = page.evaluate("Date.now()")
    mark = net.mark()
    api.put(doctype, row["name"], {})
    page.wait_for_timeout(5000)
    while_busy = net.matching(mark, TABLE_API)
    chip = page.get_by_role("button", name=re.compile("New changes"))
    has_chip = chip.count() > 0
    rank = dom_rank(page, key)
    if side == "after":
        w.check("live", role, "after: with the pointer on the rows, the table waits and shows 'New changes · Refresh'",
                f"chip {'shown' if has_chip else 'not shown'}, {len(while_busy)} table request(s) in 5 s, {key} row "
                f"{None if rank is None else rank + 1}", has_chip and not while_busy)
    else:
        w.check("live", role, "before: develop refreshes at once, pointer or not",
                f"{len(while_busy)} table request(s) in 5 s, {toasts_since(page, t_ms)} toast(s)", True, kind="info")
    w.step(page, "live", side, role, user,
           f"Busy viewer: the pointer rests on the rows when the Administrator saves {key}. 5 s later: "
           + ("the table has not moved and offers 'New changes · Refresh'." if has_chip and not while_busy else
              f"{len(while_busy)} table request(s), {key} at row {None if rank is None else rank + 1}."),
           pins=[pin(chip, "Waiting for the viewer", "info")] if has_chip else
           [pin(page.locator("tbody tr").nth(1), "Pointer here", "info")])
    if side == "after":
        mark2 = net.mark()
        page.mouse.move(700, 30)
        r2, took = wait_until(page, lambda: dom_rank(page, key), lambda r: r == 0, 12)
        w.check("live", role, "after: the waiting change applies once the pointer leaves the rows (3 s idle)",
                f"{key} on top {took} s after the pointer left, {len(net.matching(mark2, TABLE_API))} request(s)"
                if took is not None else "not applied in 12 s", took is not None)
        w.step(page, "live", side, role, user, f"The pointer leaves the rows: {took} s later the change applies, "
               f"{key} on top.", pins=[pin(first_row(page), key, "info" if took is not None else "must")])
    # Hidden tab: the walk marks the tab hidden (headless Chromium cannot), a save comes in, then the tab is shown.
    page.mouse.move(700, 30)
    page.wait_for_timeout(3500)
    fresh = last_rows(net)
    row = target_row(fresh, doctype, skip=3)
    key = key_of(row, doctype)
    page.evaluate("() => { window.__walkHidden = true; document.dispatchEvent(new Event('visibilitychange')); }")
    mark = net.mark()
    api.put(doctype, row["name"], {})
    page.wait_for_timeout(5000)
    hidden_reqs = net.matching(mark, TABLE_API)
    mark2 = net.mark()
    page.evaluate("() => { window.__walkHidden = false; document.dispatchEvent(new Event('visibilitychange')); }")
    r2, took = wait_until(page, lambda: dom_rank(page, key), lambda r: r == 0, 10)
    shown_reqs = net.matching(mark2, TABLE_API)
    if side == "after":
        w.check("live", role, "after: a hidden tab sends no table request, and refreshes once when shown",
                f"hidden 5 s: {len(hidden_reqs)} request(s); shown: {len(shown_reqs)} request(s), {key} on top in "
                f"{took} s", not hidden_reqs and len(shown_reqs) >= 1 and took is not None)
    else:
        w.check("live", role, "before: develop refreshes a hidden tab at once",
                f"hidden 5 s: {len(hidden_reqs)} request(s); shown: {len(shown_reqs)}", True, kind="info")
    w.step(page, "live", side, role, user,
           f"Hidden tab (the walk sets the page's visibility to hidden, as a tab switch does): {key} is saved; "
           f"{len(hidden_reqs)} table request(s) while hidden; shown again, {len(shown_reqs)} request(s) and {key} on "
           f"top in {took} s.", pins=[pin(first_row(page), key, "info" if took is not None else "must")])


# --- 2. Six tables that refetched without end -----------------------------------------------------------------------

QUIET_S = 60


def six_screens(env: str) -> list[tuple[str, str, object]]:
    """(screen, its table's doctype, how the Admin opens it). Two are opened by their link: a project's TDS tab and a
    user's Reimbursements tab sit behind a search in a list, which is not what this claim is about."""
    f, base = FACT[env], base_url(env)

    def admin_option(p, label):
        menu(p, "Admin Options").first.click()
        p.wait_for_timeout(600)
        sub_menu(p, label).first.click()

    def payments_tab(p):
        menu(p, "Project Payment & Expense").first.click()
        settle(p, 2500)
        tab_button(p, "GST Requests").first.click()

    return [
        ("Cheque Tracker", "Cheques", lambda p: menu(p, "Cheque Tracker").first.click()),
        ("Admin Options › Payees", "Payee", lambda p: admin_option(p, "Payees")),
        ("A project's TDS Repository history", "Project TDS Item List",
         lambda p: p.goto(f"{base}/projects/{f['tds_project']}?page=tdsrepository")),
        ("Payments › GST Requests", "GST Payment Request", payments_tab),
        ("Reimbursements (claims)", "Reimbursement", lambda p: menu(p, "Reimbursements").first.click()),
        ("A user's Reimbursements tab", "Reimbursement",
         lambda p: p.goto(f"{base}/users/{f['reimb_user']}?tab=reimbursements")),
    ]


def no_loop() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        with session(env, user, PW) as (page, _):
            opened = []
            for i, (label, doctype, how) in enumerate(six_screens(env)):
                p = page if i == 0 else page.context.new_page()
                net = Net(p)
                if i:
                    p.goto(base_url(env) + "/")
                sidebar_ready(p)
                how(p)
                settle(p, 5000)
                opened.append((label, doctype, p, net, net.mark()))
            page.wait_for_timeout(QUIET_S * 1000)
            for label, doctype, p, net, mark in opened:
                # Each of these pages asks its own list method (get_cheques, get_payees, get_claims, ...), so a list
                # request is any method call whose body names the table's doctype.
                first = [x for x in net.matching(0, r"/api/method/") if f'"doctype":"{doctype}"' in x["body"]
                         and net.reqs.index(x) < mark]
                later = [x for x in net.matching(mark, r"/api/method/") if f'"doctype":"{doctype}"' in x["body"]]
                ok = not later if side == "after" else True
                w.check("no_loop", role, f"{side}: {label} sends no {doctype} list request in {QUIET_S} s after its "
                        "first load", f"first load {len(first)}, next {QUIET_S} s: {len(later)}",
                        ok and bool(first), kind="must" if side == "after" else "info")
                table = p.locator("table").first
                w.step(p, "no_loop", side, role, user,
                       f"{label}: {len(first)} {doctype} list request(s) to open it, then {len(later)} in the next "
                       f"{QUIET_S} s with nothing changing.",
                       pins=[pin(table, f"{len(later)} requests in {QUIET_S} s",
                                 "info" if ok and first else "must")])


# --- 3. Refreshes spread over a few seconds ------------------------------------------------------------------------

def spread() -> None:
    role, user = "Admin", USERS["Admin"]
    numbers = {}
    for side, env in SIDES:
        api = Api(env)
        with live_page(env, user) as (page, net), other_tab(page, env, user) as (tab2, net2), \
                other_tab(page, env, user) as (tab3, net3):
            tabs = [(page, net), (tab2, net2), (tab3, net3)]
            for i, (p, n) in enumerate(tabs):
                open_table(p, n, "spread", side, role, user, "Procurement Requests", None, record=(i == 0), min_rows=1)
            p0 = page
            p0.mouse.move(700, 30)
            for p, _ in tabs[1:]:
                p.mouse.move(700, 30)
            page.wait_for_timeout(4000)
            marks = [n.mark() for _, n in tabs]
            t0 = time.time()
            api.put("Procurement Requests", FACT[env]["pending_pr"], {})
            page.wait_for_timeout(9000)
            rows = []
            for i, (p, n) in enumerate(tabs):
                tbl = n.matching(marks[i], TABLE_API)
                cnt = n.matching(marks[i], COUNTS_API)
                rows.append({"tab": i + 1, "table": [round(x["t"] - t0, 2) for x in tbl],
                             "counts": [round(x["t"] - t0, 2) for x in cnt]})
            numbers[side] = rows
            firsts = [r["table"][0] for r in rows if r["table"]]
            cfirsts = [r["counts"][0] for r in rows if r["counts"]]
            text = "; ".join(f"tab {r['tab']}: table at {r['table'] or '-'} s, badges at {r['counts'] or '-'} s"
                             for r in rows)
            if side == "after":
                spread_t = max(firsts) - min(firsts) if len(firsts) == 3 else 0
                w.check("spread", role, "after: each of 3 tabs refreshes its table once, 1-2 s after the save, not all at "
                        "the same instant", text, len(firsts) == 3 and all(0.9 <= t <= 3.5 for t in firsts)
                        and spread_t >= 0.05)
                w.check("spread", role, "after: each tab's badges refresh 2-6 s after the save, at different moments",
                        text, len(cfirsts) == 3 and all(1.8 <= t <= 7 for t in cfirsts)
                        and max(cfirsts) - min(cfirsts) >= 0.05)
            else:
                w.check("spread", role, "before: develop's 3 tabs ask at once", text, True, kind="info")
            w.step(page, "spread", side, role, user,
                   f"Three tabs of the Admin show this list; the Administrator saves one PR. When each tab asked "
                   f"(seconds after the save): {text}.",
                   pins=[pin(page.locator("table").first, "Tab 1 of 3", "info")])
    (out / "spread.json").write_text(json.dumps(numbers, indent=1))


# --- 4. Badge counts ------------------------------------------------------------------------------------------------

COUNTS_METHOD = "nirmaan_stack.api.sidebar_counts.sidebar_counts"
BADGE_PAGES = {"Admin": ("Procurement Requests", "Approve PR"), "Procurement Executive": ("Procurement Requests", None),
               "Project Manager": (None, None),  # no counted page in a PM's sidebar: the JSON is compared alone
               "Accountant": ("Project Payment & Expense", None)}

# Counts the queries and time of sidebar_counts for two users of one scope; on this branch the record type's version
# is moved first, so the first call counts afresh and the second may read it back from Redis.
COUNTS_SQL = r'''
from nirmaan_stack.api.sidebar_counts import sidebar_counts
a, b = sys.argv[1], sys.argv[2]
try:
    from nirmaan_stack.services import sidebar_counts_cache
    sidebar_counts_cache.bump_version("Procurement Requests")
    cached = True
except ImportError:
    cached = False
n = [0]; real = frappe.db.sql
def counting(*args, **kw):
    n[0] += 1
    return real(*args, **kw)
frappe.db.sql = counting
res = []
for u in (a, b, a):
    frappe.set_user(u); n[0] = 0; t = time.perf_counter()
    out = sidebar_counts(u)
    res.append({"user": u, "queries": n[0], "ms": round((time.perf_counter() - t) * 1000, 1), "reply": out})
emit({"cached": cached, "calls": res})
'''


def tab_texts(page) -> list[str]:
    return [t.replace("\n", " ").strip() for t in page.locator("main button").all_inner_texts()
            if re.search(r"\d+\s*$", t.strip())][:12]


def badges() -> None:
    replies = {}
    for role, (entry, _) in BADGE_PAGES.items():
        user = USERS[role]
        drawn = {}
        for side, env in SIDES:
            replies.setdefault(role, {})[side] = json.loads(Api(env, user).method(COUNTS_METHOD, {"user": user})["message"])
            if entry is None:
                continue
            with session(env, user, PW) as (page, _):
                sidebar_ready(page)
                w.step(page, "badges", side, role, user, f"Open {entry} from the sidebar: its tabs carry the counts.",
                       click=menu(page, entry))
                settle(page, 3000)
                drawn[side] = tab_texts(page)
                w.step(page, "badges", side, role, user, f"{role}'s counts: " + ", ".join(drawn[side]) + ".",
                       pins=[pin(page.locator("main button").filter(has_text=re.compile(r"\d+\s*$")).first,
                                 "Counts", "info")])
        if entry is not None:
            w.check("badges", role, "the tab counts read the same on develop and on this branch",
                    f"{len(drawn['after'])} tabs; " + ("all the same" if drawn["before"] == drawn["after"] else
                    f"before {drawn['before']} / after {drawn['after']}"), drawn["before"] == drawn["after"])
        same = replies[role]["before"] == replies[role]["after"]
        diff = {k: (replies[role]["before"].get(k), replies[role]["after"].get(k)) for k in replies[role]["after"]
                if replies[role]["before"].get(k) != replies[role]["after"].get(k)}
        w.check("badges", role, "sidebar_counts gives the same JSON on develop and on this branch",
                "identical" if same else f"differs: {json.dumps(diff)[:300]}", same)
    (out / "sidebar_counts.json").write_text(json.dumps(replies, indent=1))
    badges_live()
    badges_redis()


def pr_state(env: str, name: str, state: str) -> dict:
    """The Administrator moves a PR's workflow state in a console (db_set: no approval side effects; it sends the
    realtime update and, on this branch, moves the cache versions)."""
    return db(env, f"pr_state_{state}", '''
doc = frappe.get_doc("Procurement Requests", sys.argv[1])
doc.db_set("workflow_state", sys.argv[2], notify=True)
frappe.db.commit()
emit({"name": doc.name, "workflow_state": frappe.db.get_value("Procurement Requests", doc.name, "workflow_state")})
''', name, state)


def badges_live() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        pr = FACT[env]["pending_pr"]
        with live_page(env, user) as (page, net):
            sidebar_ready(page)
            w.step(page, "badges", side, role, user, "Open Procurement Requests: 'Approve PR' counts the PRs waiting.",
                   click=menu(page, "Procurement Requests"))
            settle(page, 3000)
            n0 = tab_count(page, "Approve PR")
            mark = net.mark()
            t0 = time.time()
            try:
                pr_state(env, pr, "Approved")
                n1, took = wait_until(page, lambda: tab_count(page, "Approve PR"), lambda v: v == n0 - 1, since=t0)
                seen = (f"{n0} → {n1} in {took} s ({len(net.matching(mark, COUNTS_API))} count request(s))"
                        if took is not None else f"still {n1}")
                if side == "after":
                    w.check("badges", role, f"after: a PR approved elsewhere takes 'Approve PR' {n0} → {n0 - 1} "
                            f"within {LIVE_S} s, no reload (develop leaves it at {n0})", seen, took is not None)
                else:
                    # develop's stale count is the bug this branch fixes: recorded as the baseline, not a failure
                    w.check("badges", role, f"before: develop leaves 'Approve PR' at {n0} for {LIVE_S} s after a PR "
                            "is approved elsewhere, until a reload (the stale count this branch fixes)", seen,
                            took is None, kind="info")
                w.step(page, "badges", side, role, user,
                       f"The Administrator approves PR {pr} in another session. 'Approve PR' goes {n0} → {n1} "
                       + (f"in {took} s, no reload." if took is not None else
                          f"(it did not change in {LIVE_S} s; the list below already has one PR fewer)."),
                       pins=[pin(tab_button(page, "Approve PR"),
                                 f"{n1}" if took is not None else f"Still {n1}: develop does not move it until a "
                                 "reload. This branch moves it live.", "info" if took is not None or
                                 side == "before" else "must")])
            finally:
                pr_state(env, pr, "Pending")
            n2, took2 = wait_until(page, lambda: tab_count(page, "Approve PR"), lambda v: v == n0)
            w.check("badges", role, f"{side}: back to Pending, the count returns to {n0}",
                    f"{n2} in {took2} s" if took2 is not None else f"still {n2}", took2 is not None,
                    kind="must" if side == "after" else "info")


def badges_redis() -> None:
    a, b = USERS["Admin"], USERS.get("Admin 2")
    if not b:
        w.check("badges", "Admin", "a second Admin-profile user with full access", "none in this data", False)
        return
    for side, env in SIDES:
        res = db(env, "counts_queries", COUNTS_SQL, a, b)
        calls = res["calls"]
        text = "; ".join(f"{c['user'].split('@')[0]}: {c['queries']} queries, {c['ms']} ms" for c in calls)
        same = len({c["reply"] for c in calls}) == 1
        if side == "after":
            w.check("badges", "Admin", "after: a second Admin (same scope) is answered from Redis: fewer queries, faster "
                    "(measured: frappe.db.sql calls counted around sidebar_counts, after moving the PR version so the "
                    "first call counts afresh)", text + f"; same numbers: {same}",
                    calls[1]["queries"] < calls[0]["queries"] and calls[1]["ms"] < calls[0]["ms"] and same)
        else:
            w.check("badges", "Admin", "before: develop counts afresh for every user", text, True, kind="info")
    (out / "counts_queries.json").write_text(json.dumps({"note": "see db/counts_queries.*.txt"}))


# --- 5. Table pages in Redis ----------------------------------------------------------------------------------------

TABLE_METHOD = "nirmaan_stack.api.data-table.get_list_with_count_enhanced"
STATS = r'''
from frappe.utils import today
c = frappe.cache()
def get(kind):
    v = c.get(c.make_key(f"nc:dt:stats:{today()}:{kind}"))
    return int(v) if v else 0
emit({"hit": get("hit"), "miss": get("miss"), "conf": frappe.conf.get("nc_table_cache")})
'''


def ask(api: Api, doctype: str, fields: list[str], order: str, length: int, filters: list | None = None):
    body = {"doctype": doctype, "fields": json.dumps(fields), "filters": json.dumps(filters or []),
            "order_by": order, "limit_start": 0, "limit_page_length": length}
    r, ms = api.timed(TABLE_METHOD, body)
    return r["message"], ms


def stats(env: str, label: str) -> dict:
    return db(env, f"stats_{label}", STATS)


ROLE_TABLES = {"Admin": "Procurement Requests", "Procurement Executive": "Procurement Orders",
               "Project Manager": "Procurement Requests", "Accountant": "Project Payments"}


def table_cache() -> None:
    for part in (tc_same_rows, tc_second_user, tc_r2, tc_narrow, narrow_screens, tc_off):
        guarded("table_cache", part)


def tc_same_rows() -> None:
    names = {}
    for role, doctype in ROLE_TABLES.items():
        for side, env in SIDES:
            msg, _ = ask(Api(env, USERS[role]), doctype, ["name"], "creation desc", 50)
            names.setdefault(role, {})[side] = [r["name"] for r in msg["data"]] + [f"total {msg['total_count']}"]
        same = names[role]["before"] == names[role]["after"]
        w.check("table_cache", role, f"{doctype}, page 1 of 50 by creation: the same row names on develop and on this "
                "branch", f"{len(names[role]['after']) - 1} rows, {names[role]['after'][-1]}; " +
                ("identical" if same else "differ"), same and len(names[role]["after"]) > 1)
    (out / "table_rows.json").write_text(json.dumps(names, indent=1))



def tc_second_user() -> None:
    """A second user with the same access is served from Redis: the site's hit / miss counters around each request."""
    for role, doctype in (("Admin", "Procurement Requests"), ("Accountant", "Project Payments")):
        a, b = USERS[role], USERS.get(f"{role} 2")
        if not b:
            w.check("table_cache", role, f"after: a second {role} with the same access exists to ask the same page",
                    "none found", False)
            continue
        api_a, api_b = Api(AFTER, a), Api(AFTER, b)
        # A page size no screen and no other part of this walk asks (20, 41, 50), so the first ask is a miss.
        length = 51 + int(time.time()) % 40
        s0 = stats(AFTER, f"{role}_0")
        msg_a, ms_a = ask(api_a, doctype, ["name"], "creation desc", length)
        s1 = stats(AFTER, f"{role}_1")
        msg_b, ms_b = ask(api_b, doctype, ["name"], "creation desc", length)
        s2 = stats(AFTER, f"{role}_2")
        da = (s1["hit"] - s0["hit"], s1["miss"] - s0["miss"])
        dbb = (s2["hit"] - s1["hit"], s2["miss"] - s1["miss"])
        w.check("table_cache", role, f"after: a second {role} asking the same {doctype} page is served from Redis "
                "(measured: the site's daily hit / miss counters read before and after each request)",
                f"{a.split('@')[0]}: +{da[0]} hit, +{da[1]} miss, {ms_a} ms; {b.split('@')[0]}: +{dbb[0]} hit, "
                f"+{dbb[1]} miss, {ms_b} ms; same rows: {msg_a['data'] == msg_b['data']}",
                da == (0, 1) and dbb == (1, 0) and msg_a["data"] == msg_b["data"])



def tc_r2() -> None:
    """R2: after a save the next page load shows the change at once. Five saves, each followed at once by a load."""
    for side, env in SIDES:
        admin, viewer = Api(env), Api(env, USERS["Admin"])
        msg, _ = ask(viewer, "Procurement Requests", ["name"], "modified desc", 20)
        picks = [r["name"] for r in msg["data"][5:10]]
        stale = []
        for name in picks:
            admin.put("Procurement Requests", name, {})
            top, _ = ask(viewer, "Procurement Requests", ["name"], "modified desc", 20)
            if top["data"][0]["name"] != name:
                stale.append(f"{name} saved, top {top['data'][0]['name']}")
        w.check("table_cache", "Admin", f"{side}: a page load right after each of 5 saves shows the saved PR on top "
                "(never the kept page). The R2 order itself (version moved before list_update goes out) is pinned by "
                "test_table_cache's two '..._moves_the_version_before_the_live_refresh_goes_out' tests", "5 of 5 fresh" if not stale else "; ".join(stale), not stale,
                kind="must" if side == "after" else "info")



def tc_narrow() -> None:
    """Narrower access: the Admin's page is kept first; a PM and a PE asking the same question get their projects."""
    for side, env in SIDES:
        fields = ["name", "project"]
        admin_msg, _ = ask(Api(env, USERS["Admin"]), "Procurement Requests", fields, "creation desc", 50)
        for role, key in (("Project Manager", "pm"), ("Procurement Executive", "pe")):
            msg, _ = ask(Api(env, USERS[role]), "Procurement Requests", fields, "creation desc", 50)
            allowed = set(FACT[env][f"{key}_projects"])
            outside = [r["name"] for r in msg["data"] if r["project"] not in allowed]
            w.check("table_cache", role, f"{side}: right after the Admin's page is kept, the {role} gets only PRs of "
                    "their projects, and their own total", f"{len(msg['data'])} rows, total {msg['total_count']} "
                    f"(their get_list: {FACT[env][key + '_prs']}; the Admin's total {admin_msg['total_count']}); "
                    f"outside their projects: {outside[:3] or 'none'}",
                    not outside and msg["total_count"] == FACT[env][key + "_prs"] < admin_msg["total_count"])


def tc_off() -> None:
    """The off switch: nc_table_cache 0 in site config; put back after."""
    run =subprocess.run([DEVENV, "run", AFTER, "set-config", "nc_table_cache", "0"], capture_output=True, text=True,
                         cwd=ROOT, timeout=300)
    (out / "db" / "set_config_off.txt").write_text(run.stdout + run.stderr)
    try:
        api = Api(AFTER, USERS["Admin"])
        s0 = stats(AFTER, "off_0")
        for _ in range(2):
            ask(api, "Procurement Requests", ["name"], "creation desc", 41)
        s1 = stats(AFTER, "off_1")
        w.check("table_cache", "Admin", "after: with nc_table_cache 0 the same page asked twice is neither kept nor "
                "served from Redis", f"conf {s0['conf']}; hit +{s1['hit'] - s0['hit']}, miss +{s1['miss'] - s0['miss']}",
                str(s0["conf"]) == "0" and s1["hit"] == s0["hit"] and s1["miss"] == s0["miss"])
    finally:
        back = db(AFTER, "set_config_back", '''
from frappe.installer import update_site_config
update_site_config("nc_table_cache", "None")  # "None" removes the key: the site is back to its default (on)
emit({"conf": frappe.get_site_config().get("nc_table_cache")})
''')
        w.check("table_cache", "Admin", "after: the cache switch is put back (key removed)", str(back),
                back["conf"] is None)


def narrow_screens() -> None:
    """The scoped users' own lists on both envs: the PE's All PRs and the PM's Projects show their projects only."""
    for role, entry, tab, key in (("Procurement Executive", "Procurement Requests", "All PRs", "pe"),
                                  ("Project Manager", "Projects", None, "pm")):
        user = USERS[role]
        for side, env in SIDES:
            with session(env, user, PW) as (page, _):
                net = Net(page)
                rows = open_table(page, net, "table_cache", side, role, user, entry, tab, min_rows=1)
                allowed = set(FACT[env][f"{key}_projects"])
                seen = {r.get("project") or r.get("name") for r in rows}
                outside = sorted(seen - allowed)
                w.check("table_cache", role, f"{side}: the {role}'s {entry} rows are all of their projects",
                        f"{len(rows)} rows over {len(seen)} project(s); outside their {len(allowed)}: "
                        f"{outside[:3] or 'none'}", not outside and bool(rows))
                w.step(page, "table_cache", side, role, user,
                       f"The {role}'s {entry}{' › ' + tab if tab else ''}: {len(rows)} row(s) on this page, all of "
                       f"their {len(allowed)} project(s).",
                       pins=[pin(page.locator("table").first, f"{len(rows)} rows, none outside",
                                 "info" if not outside else "must")])


# --- 6. #1729 -------------------------------------------------------------------------------------------------------

def r1729() -> None:
    role, user = "Accountant", USERS["Accountant"]
    for side, env in SIDES:
        cust = FACT[env]["customer"]
        new = cust["company_name"] + " Walk"
        api = Api(env)
        with live_page(env, user) as (page, net):
            sidebar_ready(page)
            w.step(page, "r1729", side, role, user, "Open Customers from the sidebar.", click=menu(page, "Customers"))
            settle(page, 2500)
            box = page.get_by_placeholder(re.compile("^Search")).first
            box.fill(cust["company_name"])
            page.keyboard.press("Enter")
            settle(page, 2500)
            load = watch(page)
            w.step(page, "r1729", side, role, user, f"Search the newest customer: {cust['company_name']}.",
                   pins=[pin(page.get_by_text(cust["company_name"], exact=True), "Before the rename", "info")])
            page.mouse.move(700, 30)
            t_ms, mark, t0 = page.evaluate("Date.now()"), net.mark(), time.time()
            try:
                api.put("Customers", cust["name"], {"company_name": new})
                _, took = wait_until(page, lambda: page.get_by_text(new, exact=True).count() > 0, bool, since=t0)
                page.wait_for_timeout(2500)
                pins = quiet_checks(page, side, role, "the open Customers list shows the rename", took, load, t_ms,
                                    mark, net, cid="r1729")
                w.step(page, "r1729", side, role, user, f"02: the Administrator renames it to '{new}'; the open list "
                       f"shows it in {took} s, no reload.",
                       pins=[pin(page.get_by_text(new, exact=True) if took is not None else page.locator("table"),
                                 new, "info" if took is not None else "must")] + pins)
                w.step(page, "r1729", side, role, user, "Leave for the Dashboard.", click=menu(page, "Dashboard"))
                settle(page, 2000)
                w.step(page, "r1729", side, role, user, "Open Customers again.", click=menu(page, "Customers"))
                settle(page, 3000)
                box = page.get_by_placeholder(re.compile("^Search")).first
                if box.input_value() != new:
                    box.fill(new)
                    page.keyboard.press("Enter")
                    settle(page, 2500)
                shown = page.get_by_text(new, exact=True).count() > 0
                old = page.get_by_text(cust["company_name"], exact=True).count() > 0
                w.check("r1729", role, f"{side}: 02b, back on Customers the list still shows the new name",
                        f"new name shown: {shown}; old name shown: {old}", shown and not old)
                w.step(page, "r1729", side, role, user, "02b: back on Customers, the list shows the new name.",
                       pins=[pin(page.get_by_text(new, exact=True) if shown else page.locator("table"),
                                 "New name" if shown else "Old name shown", "info" if shown else "must")])
            finally:
                api.put("Customers", cust["name"], {"company_name": cust["company_name"]})
            back = db(env, "customer_back", '''
emit(frappe.db.get_value("Customers", sys.argv[1], "company_name"))
''', cust["name"])
            w.check("r1729", role, f"{side}: 03, the name is put back", back, back == cust["company_name"])


# --- 7. Rooms -------------------------------------------------------------------------------------------------------

def rooms() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        api = Api(env)
        with live_page(env, user) as (page, net):
            open_table(page, net, "rooms", side, role, user, "Procurement Requests", None, min_rows=1)
            page.wait_for_timeout(2000)
            mark_f = len(net.frames)
            w.step(page, "rooms", side, role, user, "Close the Procurement Requests list: go to the Dashboard. Its table "
                   "let go of the 'Procurement Requests' room; the badges still hold it.", click=menu(page, "Dashboard"))
            settle(page, 3000)
            left = [f for _, f in net.frames[mark_f:] if "doctype_unsubscribe" in f and "Procurement Requests" in f]
            mark = net.mark()
            t0 = time.time()
            api.put("Procurement Requests", FACT[env]["pending_pr"], {})
            page.wait_for_timeout(9000)
            counts = net.matching(mark, COUNTS_API)
            if side == "after":
                w.check("rooms", role, "after: closing the list sends no doctype_unsubscribe for the room the badges "
                        "still hold (hub with hub)", f"{len(left)} unsubscribe frame(s): {left[:1]}", not left,
                        kind="info")
                w.check("rooms", role, "after: a PR saved after the list closed still reaches the open screen (its "
                        "badges ask again)", f"{len(counts)} count request(s), first at "
                        f"{round(counts[0]['t'] - t0, 1) if counts else '-'} s", bool(counts), kind="info")
            else:
                w.check("rooms", role, "before: develop's list leaves the room when it closes (no other holder)",
                        f"{len(left)} unsubscribe frame(s); {len(counts)} count request(s) after a PR save", True,
                        kind="info")
            w.step(page, "rooms", side, role, user,
                   f"On the Dashboard: {len(left)} 'doctype_unsubscribe Procurement Requests' frame(s) sent when the "
                   f"list closed; a PR saved now gives {len(counts)} badge request(s).",
                   pins=[pin(menu(page, "Procurement Requests"), "Room still held" if not left else "Room left",
                             "info")])


    w.status("rooms", "not_walked", "no screen calls develop's safe listener (useDocTypeEventListener): only its "
             "definition in lib/frappe.ts is left, so a safe listener and the hub on one room cannot be opened side by "
             "side. Pinned by listUpdateHub.test.ts 'shares the room count with the safe listeners of @/lib/frappe "
             "(#1670)'. The steps show the hub-with-hub case only (list closed, badges still hold the room).")


# --- 8. Develop's live features kept --------------------------------------------------------------------------------

def kept() -> None:
    for part, why in NOT_WALKED_KEPT.items():
        w.check("kept", "-", f"{part}: not walked", why, True, kind="info")
    for side, env in SIDES:
        w.check("kept", "-", f"{side}: pending GST Payment Requests to decide (none: the GST cards part is not walked)",
                str(FACT[env]["gst_pending"]), FACT[env]["gst_pending"] == 0, kind="info")
    if os.environ.get("WALK_ONLY_INFLOW"):  # a trial of the inflow dialog alone
        return guarded("kept", kept_inflow)
    guarded("kept", kept_expenses)
    guarded("kept", lambda: kept_screen("Admin", "project type picker (New Project)", "/projects/new-project",
                                        "Project Types", lambda env: FACT[env]["project_type"], r"Project Types",
                                        enter=lambda p: ("New Project: Continue to the wizard with the project "
                                                         "type picker.",
                                                         p.get_by_text("Continue").first)))
    guarded("kept", lambda: kept_screen("Admin", "invoice summary cards (Vendor Invoice Recon)",
                                        "/invoice-reconciliation", "Vendor Invoices",
                                        lambda env: FACT[env]["vendor_invoice"], r"invoice_dashboard_stats"))
    guarded("kept", kept_inflow)


def console_save(env: str, doctype: str, name: str) -> dict:
    """The Administrator saves a record unchanged in a bench console (only `modified` moves)."""
    return db(env, "console_save", '''
doc = frappe.get_doc(sys.argv[1], sys.argv[2])
doc.save(ignore_permissions=True)
frappe.db.commit()
emit({"name": doc.name, "modified": doc.modified})
''', doctype, name)


def kept_expenses() -> None:
    role, user = "Accountant", USERS["Accountant"]
    for side, env in SIDES:
        api = Api(env)
        with live_page(env, user) as (page, net):
            rows = open_table(page, net, "kept", side, role, user, "Project Payment & Expense", "Payment need to paid",
                              record=False, min_rows=1)
            exp = next((r for r in rows if r.get("doctype") == "Project Expenses"), None)
            if exp is None:
                raise RuntimeError(f"{env}: no expense row in the Accountant's 'Payment need to paid'")
            page.mouse.move(700, 30)
            page.wait_for_timeout(3500)
            t_ms, mark, t0 = page.evaluate("Date.now()"), net.mark(), time.time()
            # The REST API refuses the Administrator a Project Expenses save (403), so it is saved in a console.
            console_save(env, "Project Expenses", exp["name"])
            page.wait_for_timeout(6000)
            reqs = net.matching(mark, TABLE_API)
            w.check("kept", role, f"{side}: Payments › Payment need to paid asks again when an expense row is saved "
                    "elsewhere (liveRefreshDoctypes)", f"{len(reqs)} table request(s), first at "
                    f"{round(reqs[0]['t'] - t0, 1) if reqs else '-'} s, {toasts_since(page, t_ms)} toast(s)", bool(reqs),
                    kind="must" if side == "after" else "info")
            w.step(page, "kept", side, role, user, f"Payment need to paid: the Administrator saves the expense row "
                   f"({exp['name']}) in another session; the table asked {len(reqs)} time(s).",
                   pins=[pin(page.locator("table").first, f"{len(reqs)} refresh(es)", "info" if reqs or side == "before"
                             else "must")])


def kept_screen(role: str, label: str, path: str, doctype: str, pick, pattern: str, enter=None) -> None:
    """A screen opened by its link (setup: the claim is the refresh, not the way there); `enter` then clicks on to the
    part of it that listens."""
    user = USERS[role]
    for side, env in SIDES:
        api = Api(env)
        with live_page(env, user) as (page, net):
            page.goto(base_url(env) + path)
            settle(page, 4000)
            if enter:
                caption, target = enter(page)
                w.step(page, "kept", side, role, user, caption, click=target)
                settle(page, 3000)
            quiet = net.mark()
            page.wait_for_timeout(5000)  # with nothing changing, the screen asks nothing (so a request below is the save's)
            before_save = net.matching(quiet, pattern)
            mark, t0 = net.mark(), time.time()
            api.put(doctype, pick(env), {})
            page.wait_for_timeout(6000)
            reqs = net.matching(mark, pattern)
            w.check("kept", role, f"{side}: the {label} asks again when a {doctype} record is saved elsewhere",
                    f"{len(before_save)} request(s) in the 5 s before the save; {len(reqs)} after it, first at "
                    f"{round(reqs[0]['t'] - t0, 1) if reqs else '-'} s", bool(reqs) and not before_save,
                    kind="must" if side == "after" else "info")
            w.step(page, "kept", side, role, user, f"The {label}, opened by its link: the Administrator saves a "
                   f"{doctype} record in another session; the screen asked again {len(reqs)} time(s).",
                   pins=[pin(page.locator("main").first, f"{len(reqs)} refresh(es)", "info" if reqs or side == "before"
                             else "must")])


def kept_inflow() -> None:
    role, user = "Admin", USERS["Admin"]
    for side, env in SIDES:
        inflow = FACT[env]["inflow"]
        api = Api(env)
        with live_page(env, user) as (page, net):
            sidebar_ready(page)
            menu(page, "In-Flow Payments").first.click()
            settle(page, 2500)
            w.step(page, "kept", side, role, user, "In-Flow Payments: open the new inflow dialog.",
                   click=page.get_by_role("button", name=re.compile("New Inflow|Add|Record", re.I)).first)
            settle(page, 1500)
            dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog")).first
            dialog.get_by_role("combobox").first.click(force=True)
            page.keyboard.type(inflow["project_name"][:20])
            page.wait_for_timeout(1200)
            page.keyboard.press("Enter")
            settle(page, 2500)
            quiet = net.mark()
            page.wait_for_timeout(5000)  # with nothing changing, the dialog asks nothing (so a request below is the save's)
            before_save = net.matching(quiet, r"Project Inflows")
            mark, t0 = net.mark(), time.time()
            api.put("Project Inflows", inflow["name"], {})
            page.wait_for_timeout(6000)
            reqs = net.matching(mark, r"Project Inflows")
            table_reqs = net.matching(mark, TABLE_API)
            chip = page.get_by_role("button", name=re.compile("New changes"))
            if side == "after":
                w.check("live", role, "after: with a dialog open, the In-Flow Payments list behind it waits and shows "
                        "'New changes · Refresh'", f"chip {'shown' if chip.count() else 'not shown'}, "
                        f"{len(table_reqs)} table request(s) in 6 s", chip.count() > 0 and not table_reqs)
            what = "; ".join(f"{x['url'].split('/api/')[-1][:80]} at {x['t'] - t0:+.1f} s" for x in reqs)
            w.check("kept", role, f"{side}: the new inflow dialog re-reads the project's inflows when one is saved "
                    "elsewhere", f"{len(before_save)} request(s) in the 5 s before the save; {len(reqs)} after it "
                    f"({what or 'none'})", bool(reqs) and not before_save, kind="must" if side == "after" else "info")
            w.step(page, "kept", side, role, user, f"The dialog with project {inflow['project_name']}: the "
                   f"Administrator saves one of its inflows; the dialog asked again {len(reqs)} time(s).",
                   pins=[pin(dialog, f"{len(reqs)} refresh(es)", "info" if reqs or side == "before" else "must")])


NOT_WALKED_KEPT = {
    "GST request decision → Payment Summary cards": "the data has no pending GST Payment Request to decide (1 "
                                                    "Approved, 1 Paid); paymentSummaryView.test.ts pins the cards' "
                                                    "GST figures, but no test pins RenderProjectPaymentsComponent's "
                                                    "useListUpdate('GST Payment Request') listener",
    "vendor categories form": "AddVendorCategories (components/forms/addvendorcategories.tsx) is imported by no screen",
}



# --- Run ------------------------------------------------------------------------------------------------------------

PLAN = [("live", live), ("no_loop", no_loop), ("spread", spread), ("badges", badges), ("table_cache", table_cache),
        ("r1729", r1729), ("rooms", rooms), ("kept", kept)]

for cid, fn in PLAN:
    if not only or fn.__name__ in only:
        guarded(cid, fn)

for c in w.data["changes"]:
    if c["status"] == "failed" or (c["status"] == "not_walked" and c["reason"]):
        continue
    if not any(x["change"] == c["id"] for x in w.data["steps"] + w.data["checks"]):
        continue
    bad = [k for k in w.data["checks"] if k["change"] == c["id"] and not k["ok"] and k["kind"] == "must"]
    musts = [p for s in w.data["steps"] if s["change"] == c["id"] for p in s["pins"] if p["kind"] == "must"]
    w.status(c["id"], "failed" if bad or musts else "seen",
             "; ".join(f"{k['role']}: expected {k['expect']}, saw {k['seen']}" for k in bad))
print(w.save())
