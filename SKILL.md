---
name: seat-sniper
description: Watches full ASU classes and texts the student the moment a seat opens, then calls if they miss it, and opens the add-class page so they can hit enroll. Use whenever an ASU student says a class is full, asks to watch, track or snipe a seat or section, mentions a waitlist or a class number, or asks what classes they are watching, even if they don't say "watch". Checks ASU Class Search every 10 minutes. Never enrolls, drops or pays for the student.
---

# Seat Sniper

A full class is the worst part of registration: students refresh ASU Class Search for days and still miss the seat
that opens at 2 am and fills by 2:15. Seat Sniper watches a section (or any section of a course, optionally with a
specific professor) on a schedule and, the moment a seat opens, texts the student, calls if they don't answer, and
opens the add-class page in the browser so the student only has to sign in and click. It never enrolls for the
student. The student always does the final click.

ASU only. The data comes from the same public JSON feed the ASU Class Search page uses; no ASU login is needed to
watch.

## Setup

None. The script uses only the Python standard library. Run every command from this Skill's folder.

## Term codes

`2` + two-digit year + `1` spring, `4` summer, `7` fall. Spring 2027 is `2271`, Summer 2027 `2274`, Fall 2027 `2277`.
Default to the next term students are registering for: in September to December, the coming spring; in February to
July, the coming fall (or summer if the student says summer). Confirm it in the same message as the section.

## Add a watch

1. Find out what to watch. A class number (five digits, e.g. 14968) watches one section. A course ("CSE 355") can be
   any lecture section, optionally only one professor's, a campus, or online. If the student only gives a course, ask
   once: "Any section of CSE 355, or a specific professor or class number?"
2. Preview it. Pass the student's local date and time as `--now`:

   ```bash
   python3 scripts/check_seats.py add --term 2271 --class-number 14968 --now "2026-10-24T16:30"
   python3 scripts/check_seats.py add --term 2271 --subject CSE --catalog 355 --instructor Lee --now "2026-10-24T16:30"
   ```

   Optional course filters: `--campus Tempe`, `--online`, `--component LAB` (lectures by default).
   The output has `FOUND` lines (class number, professor, days and time, campus, seats) and ends in `PREVIEW`.
3. Show the student what was found in one or two lines and ask for a yes.
   - `ALREADY_OPEN` means a seat is open right now: tell them, send the link on its own line, and ask if they still
     want a watch (they might be waiting on something else first).
   - `ERROR reason=section_not_found` with "sections exist with: ..." means the professor filter matched nothing:
     offer the professors listed.
4. On yes, run the same command with `--confirm` added. It prints `SAVED w1 ...` with the expiry date.
5. Call preference, asked once ever. If the student's call preference isn't in memory yet, ask: "If a seat opens I'll
   text you. Want a call too if you don't answer in 5 minutes?" Save it in memory and in the script:
   `python3 scripts/check_seats.py prefs --calls yes` (or `no`). Default quiet hours are 22:00 to 07:30 (text only, no
   call); change with `--quiet 23:00-08:00` if the student asks. Never ask again for later watches.
6. Schedule the check, once for all watches. If there is no Seat Sniper check-in yet, create a recurring check-in
   every 10 minutes (the student can pick 5 or 15; save it with `prefs --every`). Word it so it makes sense days later:

   "Seat Sniper check: from the seat-sniper Skill folder run
   `python3 scripts/check_seats.py check --now <student's local date and time>`.
   Act on each line as the seat-sniper Skill says. If the output is only QUIET or NOCHANGE lines, send nothing.
   Never enroll, drop or pay for the student."

   Mention plainly that a 10-minute check can miss a seat that opens and fills in between.

## Reading `check` output

| Line | What to do |
| --- | --- |
| `ALERT w1 seats=N ... call=yes/no cls=... link=...` | Seat open. Follow "When a seat opens" below. |
| `QUIET_HOURS_HOLD w1` | Text only. Do not call, even if `call=yes` appeared earlier. |
| `CLOSED_AGAIN w1` | Text "It filled again. Still watching <label>." Nothing else; the watch re-arms itself. |
| `NOCHANGE`, `SNOOZED`, `QUIET` | Send nothing. |
| `WAITLIST_MOVED` | Only shown with `--waitlist`, if the student asked for waitlist news. One short text. |
| `ERROR w1 reason=...` | Send nothing yet; one bad check happens. |
| `NEEDS_HUMAN w1` | Three checks in a row failed. Tell the student the watch is blind right now and you'll keep trying. |
| `EXPIRED w1` | Text that the watch ended (30 days or the enrollment deadline) and offer to restart it. |
| `DONE ...` | Summary line; no message of its own. |

## When a seat opens

1. Text right away, one message: the label first, the seats, then the link on its own line.
2. Open the link in the browser and hand it to the student ("tap to take over"). If ASU asks for a sign-in, the
   student signs in themselves. Never type the student's ASU password and never ask for it in chat.
3. If the line says `call=yes`, create a one-time check-in for 5 minutes later: "Seat Sniper follow-up: if the
   student has not replied since the seat alert for <label>, call them and say: A seat just opened in <label>, check
   your texts and enroll now." Skip it if `call=no`.
4. When the student replies "got it", "on it" or anything that shows they saw it, run
   `python3 scripts/check_seats.py ack w1` and cancel that follow-up.
5. If they say they enrolled, run `python3 scripts/check_seats.py remove w1` and congratulate them in one line. If no
   watches are left, remove the recurring check-in too.

## Other requests

| Student says | Run, then reply in a line |
| --- | --- |
| "what am I watching?" | `list` (shows each watch with last seats, plus preferences) |
| "stop watching CSE 355" | `list` to find the id, then `remove <id>` |
| "pause it for an hour" / "not now" | `snooze <id> --minutes 60 --now <local time>` |
| "check every 5 minutes" | `prefs --every 5`, then change the recurring check-in to match |
| "no calls" / "calls are fine" | `prefs --calls no` (or `yes`), and update memory |
| "only Prof Lee" on an existing course watch | `remove <id>`, then add again with `--instructor Lee` |

A student can have up to 10 watches. Watches end on their own after 30 days or at the enrollment deadline, whichever
comes first, so nothing is watched forever.

## Replies

Short, plain text, one idea per message, links on their own line. No Markdown. Lead with the class.

```text
Watching CSE 355, Prof Lee, MW 1:30 PM. 0 of 150 open right now, waitlist 3.
I'll check every 10 minutes and text you the second a seat opens.
```

```text
A seat just opened in CSE 355 (Prof Lee). 1 of 150.
Tap to take over and hit enroll:
https://go.oasis.asu.edu/addclass/?STRM=2271&ASU_CLASS_NBR=25636
```

```text
It filled again. Still watching CSE 355.
```

```text
Heads up: I can't reach ASU Class Search right now, so your CSE 355 watch is blind. I'll keep trying.
```

## Boundaries

- Never enroll, drop, swap or pay. The student does the final click in the add-class page.
- Never ask for the student's ASU password, ASURITE, or any key in chat. ASU sign-in happens in the browser, by the
  student.
- Watches are for the student's own registration. Cubbie texts and calls only its own student.
- Checks are every 5 to 15 minutes, not faster, to be polite to ASU's servers.
- Say plainly that a seat can open and fill between two checks.

## When something fails

| Problem | What to do |
| --- | --- |
| `ERROR reason=bad_term` / `bad_class_number` | Ask again for the term or the five-digit class number. |
| `ERROR reason=section_not_found` | Check the term. Offer the closest match from the "sections exist with" text, or ask for the class number from Class Search. |
| `ERROR reason=enrollment_closed` | Registration for that term is over. Say so and suggest the next term. |
| `ERROR reason=http_401` / `unreachable` on `add` | Say ASU Class Search isn't answering right now; try again in a few minutes. If it keeps failing, open https://catalog.apps.asu.edu/catalog/classes in the browser, read the seat numbers on the page, and tell the student, but say it isn't automatic. |
| `ERROR reason=watch_limit` | Ten watches already. Ask which one to drop. |
| `WARN ... unreadable` | The saved watches file was damaged and set aside. Tell the student the watches need re-adding and offer to add them again. |
| Phone calls not available | Text only and tell the student once that calls aren't on. |

## Trying it without ASU

`python3 scripts/check_seats.py check --sample --now "2026-10-24T16:30"` runs on fake data in `assets/sample_seats.json`
and prints a `SAMPLE ALERT`. It never touches the student's real watches. If you show a sample alert to anyone,
call it a sample.
