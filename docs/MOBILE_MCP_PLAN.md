# Plan: catknows als Connector in der Claude-App (Mobile)

Ziel: Du (oder ein Nutzer) fügt catknows in der Claude-App als Connector hinzu
und kann vom Handy aus Skool-Daten abfragen — und, wenn eingeschaltet, posten.

Dieser Plan trennt zwei grundverschiedene Fragen, die vorher vermischt waren:
**wessen Skool-Account** hängt dran (Modell A vs. B), und **welche Technik**
braucht der Weg dahin (Transport, Auth, Hosting).

---

## 0. Der Account-Punkt zuerst (dein berechtigter Einwand)

Es gibt zwei Betriebsmodelle. Sie sehen technisch ähnlich aus, sind aber
strategisch das Gegenteil voneinander.

### Modell A — jeder hostet selbst (dezentral, „catknows-Philosophie")
Jeder Nutzer betreibt seinen eigenen catknows-Server, loggt sich mit **seinem
eigenen** Skool-Account ein, und verbindet **seine** Claude-App mit **seiner**
URL. Es gibt keinen geteilten Account. Genau das, was catknows verspricht:
„läuft auf DEINEM Rechner, mit DEINEM Login".
→ Meine frühere „offene Steckdose"-Warnung galt nur für **deinen** privaten
PC-zu-Handy-Fall in diesem Modell: dann hängt **dein** Account hinter der URL,
und die URL muss geschützt sein — aber nur vor Fremden, nicht vor „anderen
Nutzern", weil es keine gibt.

### Modell B — du hostest für alle (zentral, SaaS)
Du betreibst einen Server, viele Nutzer verbinden sich dahin. Dann bräuchte
jeder Nutzer einen **eigenen** Skool-Login auf deinem Server, und die Sessions
müssten strikt getrennt sein (sonst sieht Nutzer 1 die Daten von Nutzer 2).
→ Das macht dich wieder zum Mittelsmann — genau das, wogegen catknows antritt.
Mehr Aufwand, mehr Haftung (fremde personenbezogene Daten auf deinem Server,
DSGVO), und ein Bruch mit der Marke. **Nicht empfohlen.**

**Entscheidung, die dieser Plan trifft:** Wir bauen **Modell A**. „catknows als
Connector am Handy" heißt: der Nutzer hostet seinen eigenen kleinen Server
(lokal + Tunnel, oder auf einer eigenen Mini-Cloud-Instanz) und trägt dessen URL
in seine Claude-App ein. catknows liefert die Software + eine narrensichere
Anleitung; es gibt keinen catknows-Server, den wir betreiben.

---

## 1. Das Zielbild (was der Nutzer am Ende tut)

Wichtige Einschränkung aus der Recherche: **In der Handy-App kann man keinen
neuen Connector anlegen.** Man fügt ihn **einmal auf claude.ai (Web)** hinzu,
dann synct er automatisch auf Handy und Desktop.

Nutzer-Flow (Modell A):
1. catknows im Serve-Modus starten (ein Befehl, s. u.).
2. Tunnel starten → bekommt eine öffentliche HTTPS-URL.
3. Auf **claude.ai** (einmalig, am Rechner): Settings → Connectors → Custom
   Connector → URL + Bearer-Token eintragen.
4. Ab jetzt ist catknows in der **Handy-App** verfügbar. Fragen wie
   „hol mir meine 10 aktivsten Member" funktionieren direkt.

Der erste Skool-Login (Browser-Fenster) passiert **einmalig am Rechner**, bevor
der Server öffentlich geht — die Session liegt dann in `~/.catknows/`.

---

## 2. Die Technik (was WIR bauen)

Bestätigt: das installierte `mcp`-SDK kann `run_streamable_http_async` /
`streamable_http_app()` — der Transport-Umbau ist ein Schalter, keine
Neuentwicklung.

### 2a. HTTP-Transport-Modus (klein)
`mcp_server.py` bekommt einen Modus-Schalter: stdio (wie jetzt, für Desktop) vs.
streamable-http (für remote). Per Env/Flag, z. B. `CATKNOWS_HTTP=1` +
`CATKNOWS_PORT`. SSE bewusst **nicht** — ist deprecated, und Cloudflare
Quick-Tunnels unterstützen es nicht.

### 2b. Auth-Schicht (die eigentliche Arbeit, NICHT optional)
Ein öffentlich erreichbarer Server **ohne** Auth ist deine Skool-Daten (und bei
Write: dein Account) offen im Netz. Zwei Wege, in Reihenfolge des Aufwands:

- **Minimal — Bearer-Token (`static_headers`).** Claude unterstützt ein fixes
  Token als Request-Header (`Authorization: Bearer …`), das man beim Anlegen des
  Custom Connectors einträgt. catknows prüft bei jedem Request, ob das Token zu
  einem lokal gesetzten Secret passt (`CATKNOWS_BEARER`), sonst `401`. Wenige
  Zeilen ASGI-Middleware. **Das ist der realistische Minimal-Auth für Modell A.**
  - Caveat: `static_headers` ist bei Anthropic noch **Beta** — kann sich ändern.
  - Regel aus der Auth-Spec: Token **niemals** in die URL (`?token=`) — nur Header.
- **Sauber — OAuth 2.1.** Der Standard-Weg für Connectors (PKCE S256, DCR/CIMD,
  Resource Indicators gegen „confused deputy"). Deutlich mehr Aufwand, eigener
  Auth-Endpoint. Für einen Einzelnutzer-Selbsthoster Overkill; sinnvoll erst,
  wenn catknows mal breiter verteilt wird. **Phase 2, nicht jetzt.**

### 2c. Write bleibt hinter DOPPELtem Schloss
Wenn der Server öffentlich ist, reicht das bisherige `CATKNOWS_ALLOW_WRITE`
nicht als alleiniger Schutz. Remote + Write nur zusammen mit Bearer-Auth, und
der Draft-first-confirm-Flow bleibt. Default: Read-only.

---

## 3. Hosting / Erreichbarkeit

Der Server muss aus Anthropics Cloud erreichbar sein (die Verbindung kommt von
dort, **nicht** vom Handy) — reines localhost/VPN reicht nicht.

| Option | Für wen | Caveats |
|---|---|---|
| **cloudflared** (benannter Tunnel + eigene Domain) | Empfohlen | Feste URL nur mit benanntem Tunnel; Quick-Tunnel-URL wechselt bei Neustart → Connector neu eintragen |
| **Tailscale Funnel** | Tailscale-Nutzer | Fester Hostname, einfach |
| **ngrok** | Nur Dev/Test | Free-Tier zeigt Interstitial-Warnseite (bricht ggf. UI-Widgets); zufällige URL |
| **Echte Cloud-Instanz** (kleiner VPS / Cloudflare Worker) | „Always-on" ohne PC | Mehr Setup, aber PC muss nicht laufen |

Für den typischen catknows-Nutzer, der den PC nicht 24/7 laufen lassen will, ist
langfristig eine kleine Always-on-Instanz das ehrlichere Zielbild als ein
Tunnel zum Heim-PC. Für den ersten Test reicht cloudflared.

---

## 4. Risiken (ehrliche Liste)

**Sicherheit**
1. **Offener Server ohne Auth** = Daten (und bei Write: dein Account) frei im
   Netz. → Bearer-Auth ist Pflicht, nicht Kür. Kein Authless-Deploy.
2. **Token-Leakage** → Token nur im Header, nie in URL/Logs; rotierbar halten.
3. **Prompt-Injection über Tool-Ergebnisse** → Skool-Inhalte (Posts, Kommentare,
   Bios) sind fremd-geschriebener Text. Eine KI mit **Write** könnte durch
   versteckte Instruktionen in einem Post manipuliert werden, etwas zu posten.
   → Der Draft-first-confirm-Flow ist genau dagegen die Bremse: nichts geht ohne
   menschliches „ja" raus. Bei Remote+Write nicht aufweichen.
4. **Verwundbare Proxy-Tools** (z. B. CVE-2025-6514 in `mcp-remote`) → keine
   dubiosen Adapter dazwischen; Dependencies aktuell halten.

**Betrieb / Produkt**
5. **PC muss laufen** (bei Tunnel-Variante) — sonst ist der Connector tot.
6. **Wechselnde Tunnel-URL** (free tier) → Connector regelmäßig neu eintragen.
   Nervig; feste Domain löst es.
7. **Skool-ToS / DSGVO** — unverändert aus [LEGAL.md](../LEGAL.md). Remote ändert
   nichts an der rechtlichen Lage, aber ein *öffentlicher* Endpunkt mit
   personenbezogenen Daten erhöht die Sorgfaltspflicht.

**Abhängigkeiten von Anthropic**
8. `static_headers` ist Beta. Connector-Anlegen nur über Web (nicht Mobile).
   Free-Plan: nur **1** Custom Connector. Kann sich ändern.

---

## 5. Umsetzung in Phasen

- **Phase 1 — HTTP + Bearer (kleinster echter Schritt).**
  `mcp_server.py`: HTTP-Modus-Schalter + Bearer-Middleware (`CATKNOWS_BEARER`).
  Lokal testen mit MCP-Inspector, dann cloudflared, dann einmal auf claude.ai
  eintragen und am Handy read-only ausprobieren. **Das erfüllt dein Zielbild.**
- **Phase 2 — Doku + Härtung.** Narrensichere Schritt-für-Schritt-Anleitung
  (README-Sektion „catknows am Handy"), Write remote nur mit Bearer, Hinweis auf
  feste Domain. Optional: kleines `catknows serve`-CLI-Kommando statt Env-Wirrwarr.
- **Phase 3 (später, nur wenn breit verteilt) — OAuth 2.1.** Richtiger
  Consent-Flow, damit auch Nicht-Techniker es ohne Token-Copy-Paste einrichten,
  und für Multi-Device sauberer.

**Aufwand grob:** Phase 1 ist ein halber Tag (Transport-Schalter + ~30 Zeilen
Auth-Middleware + lokaler Test). Der Tunnel/Connector-Teil ist Konfiguration,
kein Code. Phase 3 (OAuth) ist ein eigenes, größeres Stück.

---

## 5b. NACHTRAG: Der bestehende cat-knows.com / KAS-Server ändert das Bild

Du hattest recht — es gibt bereits eine Server-Infrastruktur, und sie ist **kein
Plan, sondern läuft in Produktion**. Aus dem Vault (`E:\vault\wiki\catknows*`):

- **„kass"** = **KAS-Server** (kasserver.com), das All-Inkl-Shared-Hosting, auf
  dem **cat-knows.com** liegt. SSH `w016728f@w016728f.kasserver.com`.
- Das ist die **volle catknows.-SaaS**: PHP-Server (~180 Endpoints) + MySQL
  (~50 Tabellen), **multi-tenant** — jede Datenzeile hat `team_id`,
  Tenant-Isolation ist eingebaut. Ein Nim-„Fetcher" auf dem Kunden-PC macht nur
  Skool-Login + Datenabruf und lädt roh zum Server; **alle Intelligenz + Speicher
  liegen serverseitig**. Es gibt bereits Bearer-Token-Auth, License-System,
  Team-Model, sogar einen AI-Proxy (`server/core/ai.php`, Claude + OpenAI).

**Das ist also genau Modell B — und es existiert schon.** Damit ändert sich die
Empfehlung: Für „catknows am Handy als Connector, für echte Nutzer" ist der
KAS-Server der **richtige Host**, nicht ein Tunnel zum Heim-PC. Die harte Arbeit
(Multi-Tenancy, Auth, Datentrennung, DSGVO-Download/Delete-Surface) ist dort
**schon erledigt**. Ein MCP-Endpoint wäre nur eine **neue Fassade vor der
bestehenden Logik**, kein neues System.

### Wie ein MCP-Server auf dem KAS-Server konkret aussähe

- **Nicht** den Python-`mcp_server.py` dort hinstellen — der Server ist PHP, und
  das würde die ganze fertige Multi-Tenant-/Auth-/DB-Schicht umgehen.
- Stattdessen: ein **MCP-Endpoint in PHP** (`server/endpoints/mcp/…`), der das
  streamable-http-Protokoll spricht und die MCP-Tools auf die **schon
  existierenden** Endpoints/Functions mappt (members, posts, export, ai-proxy …).
  Jeder Tool-Call läuft durch das bestehende `require_team_id()` → automatische
  Tenant-Isolation, kein neuer Daten-Leak-Vektor.
- Auth: Claude-Custom-Connector mit **Bearer-Token** (`static_headers`, Beta)
  gegen das **schon vorhandene** Session-/License-Token-System. Sauberer wäre
  perspektivisch OAuth, aber der Bearer-Weg nutzt, was da ist.
- Erreichbarkeit ist gelöst: cat-knows.com ist eine echte öffentliche HTTPS-URL —
  **kein Tunnel nötig, PC muss nicht laufen.** Das räumt Risiken 5 + 6 (PC-an,
  wechselnde URL) komplett ab.

### Wichtige Konsequenz für „wessen Account"

Beim KAS-Server-Weg meldet sich **jeder Nutzer mit seinem eigenen catknows.-
Konto** an (das gibt es schon, mit License/Team). Deine ursprüngliche Frage
„jeder mit seinem eigenen Account" ist hier also **von Haus aus erfüllt** — die
Multi-Tenancy trennt die Daten. Es ist NICHT dein Account für alle. Genau
richtig, wie du es dir vorgestellt hast.

### Aber (ehrlich): zwei catknows, ein Name

Es gibt jetzt **zwei Dinge namens catknows**:
1. **Dieses Repo** (`skool-api`, Python, MIT, „free & open source data tap") —
   das dezentrale, für-jeden-forkbar Werkzeug.
2. **cat-knows.com** (PHP-SaaS auf KAS) — das gehostete Analytics/CRM-Produkt
   mit Konten, Teams, Lizenzen.

Der MCP-Server hier im Repo passt zu (1): der Nutzer hostet selbst. Ein
MCP-Endpoint auf cat-knows.com passt zu (2): du hostest, Nutzer loggt sich ein.
**Beide sind legitim, aber es sind verschiedene Produkte.** Bevor gebaut wird,
muss die Produktfrage klar sein: Soll „catknows am Handy" das **offene Werkzeug**
sein (Repo-MCP, Selbst-Hosting, technische Nutzer) oder das **Produkt-Feature**
von cat-knows.com (KAS-MCP, Konto-Login, alle Nutzer)? Das ist keine Technik-,
sondern eine Strategie-Entscheidung — und sie gehört dir.

---

## 5c. Deine zweite Frage: Write über die Claude-App — was tut der Nutzer?

Wichtigste Unterscheidung: **Den Write-Schalter kann der Nutzer NICHT aus dem
Chat heraus umlegen.** Ob posten erlaubt ist, ist eine **Server-Einstellung**,
kein Chat-Befehl — genau das ist der Sinn: die KI kann sich die Rechte nicht
selbst geben.

Es hängt davon ab, welcher der zwei Wege oben:

### Repo-MCP (Selbst-Hosting, Modell A)
- Write an-/ausschalten = **Env-Variable am Server**, nicht im Chat. Der Nutzer
  editiert seine Server-Config (`CATKNOWS_ALLOW_WRITE=1`) und **startet den
  Server neu**. Danach muss der Connector einmal neu verbunden werden, damit die
  Claude-App die zwei neuen Tools (`create_post`, `send_dm`) sieht.
- Das geht **am Rechner**, nicht am Handy — Config-Datei + Neustart.
- **Im Chat** (Handy oder Desktop) tut der Nutzer dann nur noch: „poste X".
  Ablauf, wenn Write aktiv ist:
  1. KI ruft `create_post` mit `confirm=false` → bekommt nur den **Entwurf**
     zurück, nichts geht raus.
  2. Claude zeigt dir den Entwurf. Zusätzlich fragt die App selbst „Tool
     `create_post` ausführen? Erlauben?" (eingebauter MCP-Permission-Prompt).
  3. Du sagst „ja, senden" → KI ruft nochmal mit `confirm=true` → **jetzt** wird
     gepostet.
- `notify_members` (Mail an alle) ist ein **extra** Schalter im selben Aufruf —
  wird nur gesetzt, wenn du es ausdrücklich sagst.

### KAS-MCP (cat-knows.com, Modell B)
- Hier wäre Write eine **Account-/Rollen-Einstellung** im cat-knows.com-Konto
  (analog zum bestehenden `download_access`-Grant im Team-Tab). Der Nutzer
  aktiviert „Posten erlauben" **einmal im Web-Dashboard**, nicht im Chat.
- Danach identisch: im Chat „poste X" → Entwurf → Bestätigung → gesendet.

### In beiden Fällen, was der Nutzer in der App tatsächlich TUT
- **Nichts umstellen im Chat.** Er formuliert nur die Absicht („schreib einen
  Willkommens-Post"), sieht den Entwurf, bestätigt.
- Die **eine** bewusste Entscheidung („darf meine KI überhaupt posten?") trifft
  er vorher, einmal, an einem sicheren Ort (Config bzw. Dashboard) — nie
  beiläufig im Gespräch.

Das ist Absicht: Write ist ein geladenes Werkzeug (postet als du, an echte
Mitglieder). Der Schalter gehört außerhalb des Chats, die Bestätigung in den
Chat.

---

## 5d. FINALE RICHTUNG: MCP-Endpoint auf cat-knows.com (löst alles auf einmal)

Entscheidung getroffen: „catknows am Handy" = **MCP-Endpoint auf dem
bestehenden cat-knows.com / KAS-Server**, nicht Selbst-Hosting. Grund: es löst
jedes offene Problem in einem Zug, weil die schwere Infrastruktur schon läuft.

**Was damit automatisch gelöst ist:**

| Problem (aus §4) | Weg damit, weil KAS es schon hat |
|---|---|
| Server muss aus dem Netz erreichbar sein | cat-knows.com ist bereits eine öffentliche HTTPS-URL |
| PC muss laufen / Tunnel-Gebastel | Entfällt komplett — der Server läuft eh 24/7 |
| Wechselnde Tunnel-URL | Feste Domain, ändert sich nie |
| „Wessen Account?" / Datentrennung | Multi-Tenancy (`team_id`) ist schon eingebaut — jeder sieht nur seins |
| Login pro Nutzer | Konto-/License-/Team-System existiert bereits |
| Auth vor dem Server | Bearer-Token-System ist schon da |
| DSGVO / Datenhoheit | Download/Delete-Surface (R5) ist schon gebaut |

Der MCP-Endpoint ist damit **nur eine dünne neue Fassade** vor Logik, die es
schon gibt — kein neues System, kein neuer Daten-Leak-Vektor (jeder Tool-Call
läuft durch das bestehende `require_team_id()`).

**Der Nutzer-Prozess im Drei-Schritte-Prinzip** (Feuerstein-einfach, so auf die
Bilder/Doku):

> **1. Anmelden.** Auf cat-knows.com einloggen (Konto hast du eh). Einmalig auf
>    „Mit Claude verbinden" tippen — bekommst deinen persönlichen Verbindungs-Code.
>
> **2. Einkleben.** In Claude (am Rechner, einmal) den Code als Connector
>    einfügen. Fertig — catknows taucht ab jetzt auch in deiner Handy-App auf.
>
> **3. Fragen.** Vom Handy aus mit Claude reden: „Wer verlässt gerade meine
>    Community?", „Schreib mir den Wochen-Post." Deine Daten, deine KI, überall.

(Technisch hinter Schritt 1–2: cat-knows.com generiert das Bearer-Token, der
Nutzer trägt URL + Token als Custom Connector auf claude.ai ein — das synct
automatisch aufs Handy. Schritt 3 ist reines Chatten.)

**Write in diesem Bild** (§5c, KAS-Variante): „Posten erlauben" ist ein Schalter
im cat-knows.com-Dashboard (wie der Download-Grant heute). Einmal an — danach im
Chat nur noch „poste X" → Entwurf → bestätigen → raus. Der Schalter bleibt
außerhalb des Chats, die KI kann ihn sich nicht selbst geben.

**Zu bauen (grob, PHP auf KAS):**
- `server/endpoints/mcp/…` — streamable-http-MCP-Endpoint, mappt Tools auf die
  vorhandenen Endpoints/Functions (members, posts, today-briefing, export,
  ai-proxy, und für Write: die Post/DM-Pfade).
- Bearer-Check gegen das bestehende Session-/License-Token (der `401` muss
  `WWW-Authenticate: Bearer resource_metadata="…"` liefern, sonst findet Claude
  die Metadata nicht — siehe Recherche §2).
- Ein „Mit Claude verbinden"-Button im Dashboard, der Token + Copy-Paste-URL
  ausgibt.
- Write-Grant-Toggle (analog `download_access`).

**Offen / später:** OAuth statt statischem Bearer (sauberer für Nicht-Techniker,
aber `static_headers`-Bearer reicht zum Start; Bearer ist bei Anthropic Beta).

---

## 6. Offene Entscheidungen für dich

1. **Welches Produkt ist „catknows am Handy"?** Das offene Repo-Werkzeug
   (Selbst-Hosting) ODER das cat-knows.com-Feature (Konto-Login)? — die
   Kern-Strategiefrage (§5b).
2. Falls **cat-knows.com/KAS**: MCP-Endpoint in PHP vor die bestehende Logik,
   Bearer gegen das vorhandene Token-System. Kein Tunnel, PC muss nicht laufen.
3. Falls **Repo/Selbst-Hosting**: Tunnel zum PC (billig, PC muss laufen) vs.
   kleine Always-on-Instanz.
4. **Write remote**: erst gar nicht (nur read am Handy) oder direkt mit
   Bearer-Pflicht + Draft-confirm mitdenken?
