# Testbericht — Tester-Findings vom 14.08.2026

Sechs Befunde: drei aus einem externen Testbericht, drei beim Verifizieren
zusätzlich gefunden. Alle Zahlen unten stammen aus Live-Abfragen gegen echte
Skool-Accounts; Community-Slugs, Namen und Ids sind hier bewusst weggelassen.

**Nicht committet wurde**: nichts Personenbezogenes. Ein technischer Testpost
in einer eigenen Community wurde erstellt und nach der Prüfung wieder gelöscht.

---

## Überblick

| # | Finding | Status |
|---|---|---|
| 1 | `list_my_communities`: Admin-Rolle nicht erkannt | erledigt |
| 2 | `list_my_communities`: `joined_at` = Gründungsdatum | erledigt |
| 3 | `list_members`: Duplikate statt Paginierung | erledigt |
| 4 | Attachments auf Post/Kommentar/DM | erledigt (mehr als geplant) |
| 5 | Rollennamen waren je Tool verschieden | erledigt (neu gefunden) |
| 6 | DM-Verläufe waren nicht lesbar | erledigt (neues Tool `read_dms`) |

---

## Aufgabe 1 — Admin-Rolle wird nicht erkannt

**Status: erledigt.**

Geändert: [catknows/normalize.py:65-74](catknows/normalize.py#L65-L74)
(`_ROLE_NAMES` + `_role()`), [normalize.py:295](catknows/normalize.py#L295)
(Parse von `metadata.member`).

Ursache war nicht ein falscher Wert, sondern ein Feld, das es nicht gibt:
`group.role` existiert im `self/groups`-Payload überhaupt nicht, also fiel jede
Community auf den Default `"member"` zurück. Die echte Rolle steckt in
`metadata.member` — einem JSON-**String** mit der Mitgliedschaftszeile. Der wird
jetzt mit dem bereits vorhandenen `maybe_json()` geparst; `group-admin` wird auf
`admin` gemappt, `group-moderator` auf `moderator`, unbekannte Werte werden
unverändert durchgereicht statt in `member` verschluckt.

Owner-Auflösung über `metadata.owner` bleibt unverändert und gewinnt weiterhin —
nötig, weil Skool Owner in `metadata.member` ebenfalls als `group-admin` führt.

**Live-Beleg (roher Payload, 53 Communities eines Accounts):**

```
raw metadata.member roles: {'member': 51, 'group-admin': 2}   ohne member-feld: 0
```

Die zwei `group-admin` sind exakt die beiden Communities, die der Account
besitzt — dort gewinnt korrekt `owner`. `metadata.member` fehlte in **keiner**
Community; der Fallback ist reine Absicherung, kein Normalfall.

## Aufgabe 2 — `joined_at` war das Gründungsdatum

**Status: erledigt.**

Geändert: [catknows/normalize.py:303](catknows/normalize.py#L303).

`group.created_at` ist die Gründung der Community, nicht der eigene Beitritt.
`joined_at` kommt jetzt aus `metadata.member.created_at`, mit Fallback auf den
alten Wert nur, wenn `metadata.member` fehlt. Beides — Rolle und Datum — läuft
über denselben einen Parse.

**Live-Beleg (vorher = `group.created_at`, nachher = echter Beitritt):**

| Community | vorher | nachher |
|---|---|---|
| A (2021 gegründet) | 2021-08-10 | 2024-11-26 |
| B (2025 gegründet) | 2025-10-14 | 2026-02-28 |
| C (2026 gegründet) | 2026-06-29 | 2026-07-09 |
| D (selbst gegründet) | 2024-11-25 | 2024-11-25 |

Bei D stimmen beide überein — der Account hat die Community selbst gegründet,
Beitritt = Gründung. Das ist korrekt, kein Rückfall auf den alten Bug.

Der ursprüngliche Testbericht belegte den Fehler mit einer 2019 gegründeten
Community, in der der Tester erst seit 2024 Mitglied ist.

## Aufgabe 3 — `list_members` lieferte Duplikate

**Status: erledigt.**

Geändert: [catknows/client.py:59](catknows/client.py#L59) (`seen`-Set),
[client.py:72-85](catknows/client.py#L72-L85) (Dedupe + Abbruch).

`members()` lief über `totalPages`, hatte aber — anders als `posts()` — kein
Dedupe. Zwei Effekte überlagern sich: die Sortierung `-memberlastoffline` ist
live, Mitglieder wandern zwischen den Requests über Seitengrenzen; und für
Nicht-Admins greift die Folgeseiten-Query offenbar nicht, Skool liefert erneut
Seite 1. Übernommen wurde exakt das Muster aus `posts()`: Set gesehener Ids,
Abbruch wenn eine Seite ausschließlich Duplikate liefert, `totalPages` weiterhin
als zusätzliche Abbruchbedingung.

**Live-Gegenprobe (zwei Communities):**

```
Community E:  zurueck=32  eindeutig=32  duplikate=0
Community F:  zurueck=28  eindeutig=28  duplikate=0
```

Vorher lieferte E bei einer Anfrage über 65 Mitglieder 65 Zeilen, von denen ab
Position 31 Seite 1 wiederholt wurde (~30 echte, 35 Duplikate). Jetzt kommen 32
eindeutige zurück — das ist alles, was für Nicht-Admins sichtbar ist. Die Zahl
ist kleiner als die gemeldeten 65, aber sie ist echt; die 65 waren aufgefüllt.

**`t=active` wurde bewusst NICHT wieder eingebaut.** Der Tester beobachtet zu
Recht, dass Skools eigener Browser damit 200 liefert — für Admins.
Nicht-Admins bekommen ein hartes 404, und das sah aus, als sei der ganze
Endpunkt gesperrt. Die Entscheidung steht unverändert in
[client.py:49-52](catknows/client.py#L49-L52) und [docs/API.md](docs/API.md).

## Aufgabe 4 — Attachments

**Status: erledigt.** Der ursprüngliche Auftrag erlaubte hier Abbruch, falls der
Upload-Endpunkt nicht rekonstruierbar ist. Das war eine Fehleinschätzung der
Vorabanalyse: der Endpunkt **ist** vollständig dokumentiert, in
[docs/API.md §5.2–5.4](docs/API.md) inklusive Presign-Flow. Es musste nichts
geraten werden.

Umgesetzt auf **allen drei** Schreibebenen — Post, Kommentar und DM.

Geändert:
- [catknows/http.py:228](catknows/http.py#L228) — `put_bytes()`, roher PUT an die
  presigned URL. Schickt bewusst **keine** Cookies, keinen Bearer und kein
  WAF-Token: die S3-URL trägt ihre Auth in der Query. Kein Retry, wie bei jedem
  Schreibpfad.
- [catknows/client.py:326](catknows/client.py#L326) — `upload_file()`: registriert
  via `POST /files`, lädt die Bytes nach S3, gibt das File-Objekt zurück.
  Nimmt Slug **oder** Gruppen-UUID — eine DM hat keinen Slug, ihr Kanal trägt
  aber eine `group_id`; die UUID spart nebenbei den Posts-Fetch.
- `attachments`-Parameter auf `create_post`, `create_comment` und `send_dm`.
- [catknows/mcp_server.py:539](catknows/mcp_server.py#L539) `_attachment_preview()`
  und `_upload_all()`, verdrahtet in alle drei Write-Tools. Die MCP-Tools nehmen
  **lokale Pfade**, nicht Ids.

Wichtig für das Draft-first-Verhalten: bei `confirm=false` wird **nichts**
hochgeladen. Die Vorschau stattet nur die Dateien (Name, Typ, Bytes) — ein
fehlender Pfad fliegt dort auf, nicht mitten in einem bestätigten Post.

**Live-Gegenprobe 1 — Post mit PDF (voller Round-Trip):**

```
attachmentsData: [{'id': '<file id>', 'metadata': {
  'content_type': 'application/pdf', 'file_name': '<name>.pdf',
  'src_content_length': 192, 'read_url': 'https://assets.skool.com/f/...'}}]
```

Skool hat die Datei verarbeitet und liefert sie aus: korrekter Content-Type,
richtiger Dateiname, 192 Bytes, abrufbare `read_url`. Der Anhang wurde
zusätzlich in der Skool-UI gegengeprüft. Der Testpost wurde danach gelöscht.

**Live-Gegenprobe 2 — DM mit PDF:**

```
attachments in der POST-Antwort: None      <- die Antwort echoed nichts
gegengelesen am Kanal:           "<dieselbe file id>"
```

Gesendet wurde ein einseitiges PDF, vorher mit `pypdf` auf Lesbarkeit geprüft
(994 Zeichen extrahierbar, keine Attrappe).

Zwei Beobachtungen fürs Protokoll, beide jetzt in docs/API.md:
- Die Antwort von `POST /files` enthält **nur** `file.id`, kein Echo von Name,
  Typ oder Länge (§5.3).
- DM und Post erwarten **verschiedene Formen**: die DM ein JSON-**Array**, der
  Post einen komma-getrennten **String**. Gespeichert wird beides als String.
  Die Sende-Antwort spiegelt den Anhang nicht zurück — wer prüfen will, ob er
  ankam, muss den Kanal lesen, nicht die Antwort (§5.7).

## Aufgabe 5 — Rollennamen waren je nach Tool verschieden

**Status: erledigt.** Nicht extern gemeldet, beim Verifizieren der
Moderator-Rolle aufgefallen.

Geändert: [catknows/normalize.py:65-74](catknows/normalize.py#L65-L74)
(`_ROLE_NAMES` + `_role()`, nach oben gezogen), angewandt in `member()`,
`profile()` und `my_community()`.

`list_my_communities` lieferte nach Aufgabe 1 sauber `admin`/`moderator` —
`list_members` und `get_member_profile` dagegen weiterhin Skools interne Namen
`group-admin`/`group-moderator`. Ein Agent, der nach Admins filtert, bekam je
nach Tool eine andere Antwort. Jetzt gehen alle Pfade durch dasselbe Mapping.

**Live-Beleg (Community mit 593 Mitgliedern):**

```
vorher:  {'member': 585, 'group-moderator': 5, 'group-admin': 3}
nachher: {'member': 585, 'moderator': 5, 'admin': 3}
```

Damit ist auch die zuvor offene **Moderator-Rolle live verifiziert** — sie
existiert dort fünfmal. Was weiterhin fehlt, ist eine Community, in der der
Testaccount *selbst* Moderator ist; dieser Pfad ist nur per Assert gedeckt.

## Aufgabe 6 — DM-Verläufe lesen (`read_dms`)

**Status: erledigt.** Der Ankündigungspost verspricht „Summarize my unread
DMs" — das ging bislang nicht: catknows sah nur die letzte Zeile pro Kanal.

Neu: [catknows/client.py:296](catknows/client.py#L296) `chat_messages()`,
[normalize.py:103](catknows/normalize.py#L103) `chat_message()`,
[mcp_server.py:449](catknows/mcp_server.py#L449) MCP-Tool `read_dms`
(read-only annotiert, Secret-Scrub auf dem raw-Pfad).

Der Endpunkt war in drei Stufen irreführend, alles jetzt in docs/API.md §1.6:

1. `before` ist **kein** Timestamp und **keine** Message-Id, sondern „wie viele
   Nachrichten zurück ab der neuesten".
2. `before` ist bei **50** gedeckelt. Größere Werte werfen `invalid before: N`
   — liest sich wie ein Formatfehler, ist aber ein Limit.
3. Weiter zurück geht nur über den **`msg={id}`-Cursor**, der ein Fenster *um*
   eine Nachricht liefert. Ohne ihn bleibt man bei den neuesten 51 stehen,
   während `has_more_before` dauerhaft `true` meldet. Dieser Parameter stammt
   aus einem Traffic-Mitschnitt — aus dem Payload allein war er nicht
   herleitbar.

Zusätzlich ein Feldfehler, der beim Gegenlesen auffiel: der Absender steht in
`metadata.src` (Empfänger in `dst`), **nicht** in `user`/`user_id`. Die erste
Fassung las das falsche Feld, wodurch jede Nachricht denselben leeren Autor
bekam und ein ganzer Verlauf wie von einer Person aussah. Behoben und per Assert
abgesichert. `attachments_data` liefert außerdem Dateiname, Typ und eine
fertige `read_url` pro Anhang — als `files` durchgereicht.

**Live-Gegenprobe (ein realer, lang laufender DM-Kanal):**

```
vorher (ohne Cursor):  51 Nachrichten, has_more_before=True  <- Sackgasse
nachher:              248 Nachrichten, 248 eindeutig, has_more_before=False
Zeitraum:             rund 21 Monate
Absender:             125 / 123 auf beide Teilnehmer verteilt
                      (vorher: 248x derselbe leere Autor)
Anhänge erkannt:      27, mit Dateiname und read_url
```

---

## Self-Check-Ausgaben (Original)

```
$ python -m catknows.normalize
normalize self-check OK

$ python -m catknows.client
client self-check OK

$ python -m catknows.mcp_server --self-check
mcp_server self-check OK (17 tools annotated)

$ CATKNOWS_ALLOW_WRITE=1 python -m catknows.mcp_server --self-check
mcp_server self-check OK (20 tools annotated)
```

Neu hinzugekommene Asserts:

- `normalize`: `group-admin` → `admin` auf **allen** Pfaden (`member`,
  `profile`, `my_community`); Owner schlägt `group-admin`; `joined_at` ≠
  Gründungsdatum; unbekannte Rollen werden durchgereicht; fehlendes /
  kaputtes `metadata.member` fällt sauber zurück statt zu crashen;
  `chat_message` liest `src`/`dst` und parst `attachments_data`.
- `client`: **neuer Self-Check** (`python -m catknows.client`) — die Datei hatte
  keinen. Fake-Transport, zwei Bereiche: die Member-Paginierung (Skool serviert
  Seite 1 erneut; echte Seite 2 mit Überlappung → nie doppelte Ids) und der
  DM-Cursor (überlappende Fenster werden dedupliziert, der erste Request nutzt
  `before=50`, der zweite `msg=<id>`, und ein kleines `count` löst gar keinen
  zweiten Request aus).
- `mcp_server`: Attachment-Vorschau liefert Name/Typ/Größe, leere Eingabe ergibt
  leere Liste, ein fehlender Pfad wirft zur Vorschauzeit.

---

## Was offen bleibt

**Filter-Grammatik für `members.json`** — nicht umgesetzt, bewusst. Der externe
Bericht mappt die Parameter für Lifecycle, Sortierung, Billing, Tiers,
Kurszugriff und das AND/OR-Verhalten pro Filterfamilie und liefert
Query-Grammatik, Pagination-Sicherungen, MCP-Schema-Vorschlag und
Akzeptanztests. Das ist ein Feature, kein Bugfix, und gehört in eine eigene
Runde. Das zugehörige PDF war nicht lesbar — im Payload steht nur die
Attachment-Id; mit `read_dms`/`attachments_data` (Aufgabe 6) wäre so etwas
künftig zugänglich, für Post-Anhänge fehlt das Gegenstück noch.

**Ein 403 auf einer großen Community (hosted)** — nicht reproduzierbar mit den
vorliegenden Daten und andere Ursache als die Pagination (WAF/Token). Separat
zu verfolgen.

**Moderator als *eigene* Rolle** — teilweise offen. Dass `group-moderator`
korrekt zu `moderator` wird, ist live belegt (Aufgabe 5). Nicht belegt ist der
`my_community`-Pfad: der Testaccount ist in keiner Community selbst Moderator.
Der Code teilt sich das Mapping mit den verifizierten Pfaden, das Risiko ist
also klein.

**DM-Anhänge nur teilweise verifiziert** — Senden und Gegenlesen sind belegt.
Nicht getestet sind mehrere Anhänge an einer Nachricht und GIFs über den
externen `external_src`-Pfad (§5.2), den catknows nicht implementiert.

**Der hosted Server läuft noch auf dem alten Code.** Eine Gegenprobe über die
hosted MCP-Tools zeigte weiterhin die alten Werte; alle Zahlen in diesem Bericht
stammen aus dem lokalen Client mit dem neuen Code. Vor einer Rückmeldung an den
Tester muss der hosted Server deployed werden, sonst testet er gegen den alten
Stand.

**Zwei Accounts, nicht einer.** Der lokale Login und der hosted Zugang gehören
unterschiedlichen Accounts (53 vs. 26 Communities). Die Zahlen hier sind
deshalb nicht Zeile-für-Zeile mit einem externen Bericht vergleichbar — die
Bugs und ihre Behebung sind es sehr wohl, weil sie am Payload-Aufbau hängen,
nicht am Account.

**Umfang der Änderungen:** `README.md`, `catknows/client.py`,
`catknows/http.py`, `catknows/mcp_server.py`, `catknows/normalize.py`,
`docs/API.md`, `workspaces/_shared/references/mcp-tools.md`, der
`check-inbox`-Workspace — plus dieser Bericht.
