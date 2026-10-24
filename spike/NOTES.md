# Data source spike (2026-10-24, Phoenix)

Result: fallback rung A works. Exact numbers from the same JSON API the public Class Search page uses. No login, no cookies.

## Request

```
GET https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes
    ?refine=Y&campusOrOnlineSelection=A&honors=F&promod=F&searchType=all
    &term=2271&subject=CSE&catalogNbr=355      (a course)
    &term=2271&keywords=14968                  (one class number)
Authorization: Bearer null
```

- Without the `Authorization` header: `401 Unauthorized`. This is the 401 the student's Cubbie hit.
- With the literal `Bearer null`: `200`, full JSON. It is what the public page sends for signed-out visitors.
- A custom polite User-Agent is accepted.
- No token to fetch, nothing expires (as of this date).

## Term codes

`2` + two-digit year + `1` spring / `4` summer / `7` fall. Confirmed live: `2267` = Fall 2026, `2271` = Spring 2027.

## Fields used (in `classes[].CLAS`)

| Meaning | Field |
| --- | --- |
| class number | `CLASSNBR` |
| subject / catalog | `SUBJECT`, `CATALOGNBR` |
| title | `TITLE` |
| instructors | `INSTRUCTORSLIST` (list of names) |
| days / time | `MON`..`SUN` (`Y`/`N`), `STARTTIME`, `ENDTIME` |
| campus / mode | `CAMPUS`, `INSTRUCTIONMODE` (`P`, `HY`, `OL`...) |
| enrolled / capacity | `ENRLTOT`, `ENRLCAP` |
| waitlist | `WAITTOT` |
| status | `ENRLSTAT` (`O` open, `C` closed) |
| component | `COMPONENTPRIMARY` (`LEC`, `REC`, `LAB`...) |

Seats open = `ENRLCAP - ENRLTOT` when `ENRLSTAT` is `O`, else 0 (a section can be closed with reserved seats, or over-enrolled: 151/150 seen).

## Checked against the website

CSE 355 Fall 2026: 66447 150/150 closed, 66448 93/150 open, 74584 79/150 open. Trimmed real response: `tests/fixtures/cse355_real.json`.

## Links handed to the student

- Add class (student signs in and clicks): `https://go.oasis.asu.edu/addclass/?STRM=<term>&ASU_CLASS_NBR=<nbr>`
- Class Search view: `https://catalog.apps.asu.edu/catalog/classes/classlist?keywords=<nbr>&searchType=all&term=<term>`

## Still to check on Cubbie

Run `python3 scripts/check_seats.py add --term 2271 --class-number 14968` from Cubbie's sandbox. If it prints `ERROR reason=...`, Cubbie's network differs; use rung C (Cubbie's browser reads the page).
