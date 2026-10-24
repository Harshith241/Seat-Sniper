#!/usr/bin/env python3
"""Seat Sniper: watch full ASU class sections and report when a seat opens.

The script only fetches, compares, saves state and prints. Cubbie does the
texting, calling, scheduling and browser. Standard library only. It never
enrolls, drops, swaps or pays.

Every line starts with a machine word (FOUND, SAVED, ALERT, CLOSED_AGAIN,
NOCHANGE, ERROR, NEEDS_HUMAN, QUIET, DONE...). Text after " | " is for humans.
"""
import argparse
import http.client
import json
import re
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

API = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"
UA = "seat-sniper/1.0 (Cubbie skill; one student's watch list, polls every 10 min)"
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_FILE = os.path.join(HERE, "..", "assets", "sample_seats.json")
MAX_WATCHES = 10
MAX_ERRORS = 3
EXPIRE_DAYS = 30
PAUSE = 1.0  # seconds between watches, to be polite
DEFAULT_PREFS = {"calls_ok": None, "quiet_start": "22:00", "quiet_end": "07:30", "check_minutes": 10}
DAYS = [("MON", "M"), ("TUES", "T"), ("WED", "W"), ("THURS", "Th"), ("FRI", "F"), ("SAT", "Sa"), ("SUN", "Su")]

SAMPLE = False
JSON_OUT = False
EVENTS = []


class FetchError(Exception):
    pass


# ---------- output ----------

def out(kind, wid=None, text="", **fields):
    ev = {"kind": kind}
    if wid:
        ev["id"] = wid
    ev.update(fields)
    if text:
        ev["text"] = text
    if SAMPLE:
        ev["sample"] = True
    EVENTS.append(ev)
    if JSON_OUT:
        return
    parts = (["SAMPLE"] if SAMPLE else []) + [kind] + ([wid] if wid else [])
    parts += ["%s=%s" % (k, v) for k, v in fields.items()]
    print(" ".join(parts) + (" | " + text if text else ""))


# ---------- state ----------

def state_path():
    base = os.environ.get("SEAT_SNIPER_HOME") or os.path.expanduser("~/.config/seat-sniper")
    return os.path.join(base, "watches.json")


def load():
    p = state_path()
    st = {}
    try:
        with open(p) as f:
            st = json.load(f)
        if not isinstance(st, dict) or not isinstance(st.get("watches"), list):
            raise ValueError("bad shape")
    except FileNotFoundError:
        st = {}
    except (ValueError, OSError):
        # Never silently drop a student's watches: keep the broken file aside.
        bad = p + ".corrupt-" + datetime.now().strftime("%Y%m%d%H%M%S")
        try:
            os.replace(p, bad)
        except OSError:
            pass
        out("WARN", text="watches.json was unreadable, moved to %s, starting empty" % bad)
        st = {}
    st.setdefault("version", 1)
    st.setdefault("next_id", 1)
    st.setdefault("watches", [])
    prefs = dict(DEFAULT_PREFS)
    prefs.update(st.get("prefs") or {})
    st["prefs"] = prefs
    return st


def save(st):
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, p)


def find(st, wid):
    for w in st["watches"]:
        if w["id"] == wid:
            return w
    return None


# ---------- data source ----------

def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _iso_date(x):
    d = (x or "")[:10]
    return d if re.match(r"^\d{4}-\d\d-\d\d$", d) else ""


def parse(c):
    C = c.get("CLAS", c)
    tot, cap = _int(C.get("ENRLTOT")), _int(C.get("ENRLCAP"))
    status = "open" if C.get("ENRLSTAT") == "O" else "closed"
    return {
        "class_number": str(C.get("CLASSNBR") or ""),
        "subject": (C.get("SUBJECT") or "").upper(),
        "catalog": (C.get("CATALOGNBR") or "").upper(),
        "title": C.get("TITLE") or "",
        "instructors": [n for n in (C.get("INSTRUCTORSLIST") or []) if n],
        "days": "".join(abbr for key, abbr in DAYS if C.get(key) == "Y"),
        "time": (C.get("STARTTIME") or "").split("<")[0].strip(),
        "campus": C.get("CAMPUS") or "",
        "mode": C.get("INSTRUCTIONMODE") or "",
        "component": C.get("COMPONENTPRIMARY") or "",
        "enroll_deadline": _iso_date(C.get("ENROLLDEADLINE")),
        # Closed sections can still show cap > total (reserved seats), so trust ENRLSTAT.
        "open": max(0, cap - tot) if status == "open" else 0,
        "total": cap,
        "waitlist": _int(C.get("WAITTOT")),
        "status": status,
    }


def fetch(term, class_number=None, subject=None, catalog=None):
    if SAMPLE:
        with open(SAMPLE_FILE) as f:
            raw = json.load(f)
    else:
        q = {"refine": "Y", "campusOrOnlineSelection": "A", "honors": "F", "promod": "F",
             "searchType": "all", "term": term}
        if class_number:
            q["keywords"] = class_number
        else:
            q["subject"], q["catalogNbr"] = subject, catalog
        # The public Class Search page sends this literal header for signed-out visitors.
        req = urllib.request.Request(API + "?" + urllib.parse.urlencode(q), headers={
            "Authorization": "Bearer null", "User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = json.load(r)
        except urllib.error.HTTPError as e:
            raise FetchError("http_%d" % e.code)
        except (ValueError, http.client.HTTPException):
            raise FetchError("bad_json")
        except OSError:
            raise FetchError("unreachable")
    try:
        secs = [parse(c) for c in raw.get("classes") or []]
    except (AttributeError, TypeError, KeyError):
        raise FetchError("bad_json")
    if class_number:
        return [s for s in secs if s["class_number"] == class_number]
    return [s for s in secs if s["subject"] == subject.upper() and s["catalog"] == catalog.upper()]


def matches(s, w):
    ins = w.get("instructor_contains")
    if ins and not any(ins.lower() in n.lower() for n in s["instructors"]):
        return False
    if w.get("campus") and w["campus"].lower() not in s["campus"].lower():
        return False
    if w.get("online_only") and not is_online(s):
        return False
    if w.get("component") and s["component"] != w["component"]:
        return False
    return True


def observe(w):
    """Return (open, total, waitlist, best_section) for a watch, or raise FetchError."""
    if w["mode"] == "section":
        secs = fetch(w["term"], class_number=w["class_number"])
    else:
        secs = [s for s in fetch(w["term"], subject=w["subject"], catalog=w["catalog"]) if matches(s, w)]
    if not secs:
        raise FetchError("section_not_found")
    best = max(secs, key=lambda s: s["open"])
    return sum(s["open"] for s in secs), sum(s["total"] for s in secs), sum(s["waitlist"] for s in secs), best


# ---------- wording ----------

def is_online(s):
    return s["mode"] == "OL" or s["campus"].upper() == "ICOURSE"


def prof(s):
    return "Prof " + s["instructors"][0].split()[-1] if s["instructors"] else "Staff"


def describe(s):
    where = "online" if is_online(s) else s["campus"].title()
    when = ("%s %s" % (s["days"], s["time"])).strip() or "no set time"
    return "%s %s, %s, %s, %s, %d of %d open, waitlist %d" % (
        s["subject"], s["catalog"], prof(s), when, where, s["open"], s["total"], s["waitlist"])


def add_link(term, nbr):
    return "https://go.oasis.asu.edu/addclass/?STRM=%s&ASU_CLASS_NBR=%s" % (term, nbr)


def in_quiet(now, p):
    s, e, t = p["quiet_start"], p["quiet_end"], now.strftime("%H:%M")
    return (s <= t < e) if s <= e else (t >= s or t < e)


def parse_now(s):
    if not s:
        return datetime.now().replace(second=0, microsecond=0)
    try:
        return datetime.fromisoformat(s.strip().replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        raise SystemExit("ERROR reason=bad_now | use local time like 2026-10-24T16:30")


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


# ---------- commands ----------

def cmd_add(a):
    now = parse_now(a.now)
    if not (a.term.isdigit() and len(a.term) == 4):
        out("ERROR", reason="bad_term", text="term is 4 digits, e.g. 2271 for Spring 2027")
        return 2
    if a.class_number:
        if not a.class_number.isdigit():
            out("ERROR", reason="bad_class_number")
            return 2
    elif not (a.subject and a.catalog and a.subject.isalpha() and a.catalog.isalnum()):
        out("ERROR", reason="need_class_number_or_subject_and_catalog")
        return 2

    st = load()
    w = {"term": a.term, "class_number": a.class_number, "subject": (a.subject or "").upper(),
         "catalog": (a.catalog or "").upper(), "instructor_contains": a.instructor,
         "campus": a.campus, "online_only": a.online, "component": a.component}
    try:
        if a.class_number:
            w["mode"] = "section"
            secs = fetch(a.term, class_number=a.class_number)
        else:
            w["mode"] = "course"
            allsecs = fetch(a.term, subject=w["subject"], catalog=w["catalog"])
            # Default to lectures so a recitation opening does not fire an alert.
            if not a.component and any(s["component"] == "LEC" for s in allsecs):
                w["component"] = "LEC"
            secs = [s for s in allsecs if matches(s, w)]
    except FetchError as e:
        out("ERROR", reason=str(e))
        return 4

    if not secs:
        if a.class_number:
            out("ERROR", reason="section_not_found", text="no class %s in term %s" % (a.class_number, a.term))
        else:
            nearby = sorted(set(prof(s) for s in allsecs)) if allsecs else []
            out("ERROR", reason="section_not_found",
                text=("sections exist with: " + ", ".join(nearby)) if nearby else "no such course this term")
        return 3

    deadline = min((s["enroll_deadline"] for s in secs if s["enroll_deadline"]), default="")
    if deadline and deadline < now.date().isoformat():
        out("ERROR", reason="enrollment_closed", deadline=deadline)
        return 3

    for s in secs[:8]:
        out("FOUND", text=s["class_number"] + " " + describe(s))
    if len(secs) > 8:
        out("FOUND", text="and %d more sections" % (len(secs) - 8))
    total_open = sum(s["open"] for s in secs)
    best = max(secs, key=lambda s: s["open"])
    if total_open:
        out("ALREADY_OPEN", seats=best["open"], cls=best["class_number"], link=add_link(a.term, best["class_number"]))

    if w["mode"] == "section":
        w["label"] = "%s %s (%s)" % (best["subject"], best["catalog"], prof(best))
    else:
        w["label"] = "%s %s" % (w["subject"], w["catalog"]) + (" (%s)" % a.instructor if a.instructor else "")
    keys = ("term", "mode", "class_number", "subject", "catalog", "instructor_contains",
            "campus", "online_only", "component")
    dup = [x for x in st["watches"] if all(x.get(k) == w.get(k) for k in keys)]
    if dup:
        out("ALREADY_WATCHING", dup[0]["id"], text=dup[0]["label"])
        return 0
    if len(st["watches"]) >= MAX_WATCHES:
        out("ERROR", reason="watch_limit", max=MAX_WATCHES, text="remove a watch first")
        return 5
    if not a.confirm:
        out("PREVIEW", text="not saved. Show the student, get a yes, then rerun with --confirm")
        return 0

    expires = now.date() + timedelta(days=EXPIRE_DAYS)
    if deadline:
        expires = min(expires, datetime.strptime(deadline, "%Y-%m-%d").date())
    w.update({"id": "w%d" % st["next_id"], "last_open": total_open,
              "last_total": sum(s["total"] for s in secs), "last_waitlist": sum(s["waitlist"] for s in secs),
              "last_checked": stamp(now), "alerted_at": None, "acked": False, "errors": 0,
              "snooze_until": None, "expires": expires.isoformat()})
    st["next_id"] += 1
    st["watches"].append(w)
    save(st)
    out("SAVED", w["id"], every=st["prefs"]["check_minutes"], expires=w["expires"], text=w["label"])
    return 0


def cmd_check(a):
    now = parse_now(a.now)
    st = load()
    p = st["prefs"]
    n = dict(alerts=0, closed=0, errors=0, needs_human=0, expired=0)
    fetched = 0
    for w in list(st["watches"]):
        if w["expires"] < now.date().isoformat():
            st["watches"].remove(w)
            n["expired"] += 1
            out("EXPIRED", w["id"], text=w["label"] + ", stopped watching")
            continue
        if w.get("snooze_until") and parse_now(w["snooze_until"]) > now:
            out("SNOOZED", w["id"], until=w["snooze_until"])
            continue
        if fetched and not SAMPLE:
            time.sleep(PAUSE)
        fetched += 1
        try:
            opn, total, wl, best = observe(w)
        except FetchError as e:
            w["errors"] = w.get("errors", 0) + 1
            n["errors"] += 1
            out("ERROR", w["id"], reason=str(e), text=w["label"])
            if w["errors"] >= MAX_ERRORS:
                n["needs_human"] += 1
                out("NEEDS_HUMAN", w["id"], errors=w["errors"], text=w["label"] + " is blind, tell the student")
            continue
        w["errors"] = 0
        prev = w.get("last_open", 0)
        if prev == 0 and opn > 0:
            quiet = in_quiet(now, p)
            call = "yes" if p.get("calls_ok") and not quiet else "no"
            out("ALERT", w["id"], seats=opn, total=total, waitlist=wl, call=call,
                cls=best["class_number"], link=add_link(w["term"], best["class_number"]),
                text=w["label"] + " " + describe(best))
            if quiet:
                out("QUIET_HOURS_HOLD", w["id"], text="text only, no call until " + p["quiet_end"])
            w["alerted_at"], w["acked"] = stamp(now), False
            n["alerts"] += 1
        elif prev > 0 and opn == 0:
            out("CLOSED_AGAIN", w["id"], text=w["label"] + ", still watching")
            w["alerted_at"], w["acked"] = None, False
            n["closed"] += 1
        elif a.waitlist and wl != w.get("last_waitlist"):
            out("WAITLIST_MOVED", w["id"], waitlist=wl, was=w.get("last_waitlist"), text=w["label"])
        else:
            out("NOCHANGE", w["id"], seats=opn, total=total, waitlist=wl)
        w.update(last_open=opn, last_total=total, last_waitlist=wl, last_checked=stamp(now))
    save(st)
    if any(n.values()):
        out("DONE", **n)
    else:
        out("QUIET")
    return 0


def prefs_line(p):
    calls = {True: "yes", False: "no", None: "not_asked"}[p.get("calls_ok")]
    out("PREFS", calls=calls, quiet="%s-%s" % (p["quiet_start"], p["quiet_end"]), every=p["check_minutes"])


def cmd_list(a):
    st = load()
    prefs_line(st["prefs"])
    if not st["watches"]:
        out("NONE", text="no active watches")
    for w in st["watches"]:
        extra = ""
        if w.get("snooze_until"):
            extra += ", snoozed until " + w["snooze_until"]
        if w.get("acked"):
            extra += ", student acknowledged"
        what = ("class " + w["class_number"]) if w["mode"] == "section" else "any matching section"
        out("WATCH", w["id"], term=w["term"], seats=w.get("last_open"), total=w.get("last_total"),
            waitlist=w.get("last_waitlist"), checked=w.get("last_checked"), expires=w["expires"],
            text="%s, %s%s" % (w["label"], what, extra))
    return 0


def with_watch(a, change, word, note=""):
    st = load()
    w = find(st, a.id)
    if not w:
        out("ERROR", a.id, reason="no_such_watch")
        return 3
    change(st, w)
    save(st)
    out(word, a.id, text=w["label"] + note)
    return 0


def cmd_remove(a):
    return with_watch(a, lambda st, w: st["watches"].remove(w), "REMOVED")


def cmd_ack(a):
    return with_watch(a, lambda st, w: w.update(acked=True), "ACKED", ", no call needed")


def cmd_snooze(a):
    until = stamp(parse_now(a.now) + timedelta(minutes=a.minutes))
    return with_watch(a, lambda st, w: w.update(snooze_until=until), "SNOOZED", ", until " + until)


def cmd_prefs(a):
    st = load()
    p = st["prefs"]
    if a.calls:
        p["calls_ok"] = a.calls == "yes"
    if a.quiet:
        try:
            # Normalize "7:30" to "07:30": in_quiet compares HH:MM strings.
            s, e = (datetime.strptime(x.strip(), "%H:%M").strftime("%H:%M") for x in a.quiet.split("-"))
        except ValueError:
            out("ERROR", reason="bad_quiet", text="use HH:MM-HH:MM, e.g. 22:00-07:30")
            return 2
        p["quiet_start"], p["quiet_end"] = s, e
    if a.every:
        if not 5 <= a.every <= 60:
            out("ERROR", reason="bad_every", text="pick 5 to 60 minutes")
            return 2
        p["check_minutes"] = a.every
    save(st)
    prefs_line(p)
    return 0


# ---------- entry ----------

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sample", action="store_true", help="use assets/sample_seats.json, no network, temp state")
    common.add_argument("--json", action="store_true", help="print events as JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", parents=[common], help="look up a section or course, save with --confirm")
    p.add_argument("--term", required=True, help="2 + YY + 1/4/7, e.g. 2271 = Spring 2027")
    p.add_argument("--class-number")
    p.add_argument("--subject")
    p.add_argument("--catalog")
    p.add_argument("--instructor", help="course mode: instructor name contains")
    p.add_argument("--campus", help="course mode: e.g. Tempe, Poly, Downtown")
    p.add_argument("--online", action="store_true", help="course mode: online sections only")
    p.add_argument("--component", help="course mode: LEC, LAB, REC... (default LEC when lectures exist)")
    p.add_argument("--now", help="student's local time, e.g. 2026-10-24T16:30")
    p.add_argument("--confirm", action="store_true", help="save the watch (after the student said yes)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("check", parents=[common], help="check every active watch once")
    p.add_argument("--now", required=True, help="student's local time, e.g. 2026-10-24T16:30")
    p.add_argument("--waitlist", action="store_true", help="also report waitlist changes")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("list", parents=[common], help="show watches and preferences")
    p.set_defaults(fn=cmd_list)

    for name, fn in (("remove", cmd_remove), ("ack", cmd_ack)):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("id")
        p.set_defaults(fn=fn)

    p = sub.add_parser("snooze", parents=[common])
    p.add_argument("id")
    p.add_argument("--minutes", type=int, required=True)
    p.add_argument("--now", help="student's local time")
    p.set_defaults(fn=cmd_snooze)

    p = sub.add_parser("prefs", parents=[common], help="save call and quiet-hour preferences")
    p.add_argument("--calls", choices=["yes", "no"])
    p.add_argument("--quiet", help="HH:MM-HH:MM, e.g. 22:00-07:30")
    p.add_argument("--every", type=int, help="minutes between checks, 5 to 60")
    p.set_defaults(fn=cmd_prefs)
    return ap


def main(argv=None):
    global SAMPLE, JSON_OUT
    a = build_parser().parse_args(argv)
    SAMPLE, JSON_OUT = a.sample, a.json
    del EVENTS[:]
    if SAMPLE:
        # ponytail: sample runs get a throwaway state dir so they never touch real watches.
        os.environ["SEAT_SNIPER_HOME"] = tempfile.mkdtemp(prefix="seat-sniper-sample-")
        if a.cmd == "check":
            with open(SAMPLE_FILE) as f:
                seed = json.load(f)
            save({"version": 1, "next_id": 2, "prefs": dict(DEFAULT_PREFS, calls_ok=True),
                  "watches": [seed["watch"]]})
    code = a.fn(a)
    if JSON_OUT:
        print(json.dumps({"events": EVENTS}, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
