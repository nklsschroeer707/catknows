# Plan: catknows als Connector in der Claude-App (Mobile)

> **Update 2026-08-10:** Die Strategie ist weitergezogen — das Hauptprodukt
> wird ein von uns **gehosteter** MCP-Server, die Open-Source-Variante bleibt
> daneben bestehen. Siehe [HOSTED_MCP_PLAN.md](HOSTED_MCP_PLAN.md). Dieser
> Plan bleibt als Referenz für das Selbst-Hosting (Modell A) gültig.

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

## 5b. Write über die Claude-App — was tut der Nutzer?

Wichtigste Unterscheidung: **Den Write-Schalter kann der Nutzer NICHT aus dem
Chat heraus umlegen.** Ob posten erlaubt ist, ist eine **Server-Einstellung**,
kein Chat-Befehl — genau das ist der Sinn: die KI kann sich die Rechte nicht
selbst geben.

### Selbst-Hosting (Modell A)
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

### Was der Nutzer in der App tatsächlich TUT
- **Nichts umstellen im Chat.** Er formuliert nur die Absicht („schreib einen
  Willkommens-Post"), sieht den Entwurf, bestätigt.
- Die **eine** bewusste Entscheidung („darf meine KI überhaupt posten?") trifft
  er vorher, einmal, an einem sicheren Ort (Server-Config) — nie beiläufig im
  Gespräch.

Das ist Absicht: Write ist ein geladenes Werkzeug (postet als du, an echte
Mitglieder). Der Schalter gehört außerhalb des Chats, die Bestätigung in den
Chat.

---

## 6. Offene Entscheidungen

1. **Hosting-Form für Modell A**: Tunnel zum PC (billig, PC muss laufen) vs.
   kleine Always-on-Instanz.
2. **Write remote**: erst gar nicht (nur read am Handy) oder direkt mit
   Bearer-Pflicht + Draft-confirm mitdenken?

Überlegungen zu einer zentral gehosteten Variante (Modell B) werden bewusst
außerhalb dieses öffentlichen Repos geführt.
