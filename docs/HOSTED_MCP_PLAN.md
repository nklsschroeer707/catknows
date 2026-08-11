# Plan: catknows als gehosteter MCP-Server (Hauptprodukt)

Stand: 2026-08-10. Dieser Plan löst die Strategie aus
[MOBILE_MCP_PLAN.md](MOBILE_MCP_PLAN.md) ab bzw. baut auf ihr auf:

**Das Hauptprodukt wird der von uns gehostete catknows-MCP-Server.** Nutzer
verbinden ihre Claude-/ChatGPT-App mit einem Klick — kein Git, kein Python,
kein Terminal. Die Open-Source-Variante bleibt vollständig auf GitHub, wird
auf Website, About-Page und in der Community verlinkt und ist weiterhin der
Weg für alle, die selbst hosten oder mitentwickeln wollen (Cal.com-Modell:
offen für alle, bezahlbare Komfort-/Business-Schicht obendrauf).

---

## 1. Zielbild (was der Nutzer am Ende tut)

1. Nutzer findet catknows (Website, Community, später Connector-Verzeichnis).
2. Er fügt den Connector hinzu: URL eintragen bzw. im Verzeichnis auf
   „Verbinden" klicken — einmalig auf claude.ai (Web), synct dann automatisch
   auf Handy und Desktop.
3. Beim ersten Verbinden öffnet sich ein Login-Fenster („Mit Google anmelden"
   oder E-Mail) — das ist der catknows-Account.
4. Im catknows-Web-Dashboard verbindet er einmalig sein Skool-Konto: „Skool
   verbinden" tippen → Skools echte Login-Seite erscheint, normal einloggen
   (auch per Google/Apple), fertig. Komplett am Handy. (Technik: siehe §2a.)
5. Ab dann: „Wer sind meine 10 aktivsten Member?" — direkt am Handy.

Das ist das Muster der etablierten Anbieter (Canva `mcp.canva.com/mcp`,
Atlassian, Linear, Sentry, Stripe …): gehosteter Streamable-HTTP-Endpoint,
OAuth-Login beim Verbinden, MCP als dünne Fassade vor dem eigenen System.

## 2. Architektur

```
website  →  catknows.<tld>          statische Seite (Landing, Docs, Privacy,
                                    Dashboard) — Cloudflare Pages oder VPS
MCP      →  mcp.catknows.<tld>/mcp  FastMCP (Python, streamable HTTP)
                                    auf kleinem VPS hinter Caddy (Auto-TLS)
Auth     →  Identity Provider mit OAuth 2.1 + PKCE + DCR
                                    (WorkOS AuthKit; Google-Login ist dort
                                    ein Häkchen, kein eigener Code)
Daten    →  pro Nutzer: verschlüsselte Skool-Session + Einstellungen
                                    (Write-Toggle, Limits); strikte
                                    Tenant-Trennung pro Request
```

Warum **nicht** Cloudflare Workers (der Weg der Großen): unser Python-Kern
mit `curl_cffi` (echter Chrome-TLS-Handshake für api2.skool.com) läuft dort
nicht — JS-Runtime, 10 ms CPU. Ein VPS ist für uns die richtige Wahl und
zugleich die günstigste.

Der gehostete Server nutzt **denselben Kern wie dieses Repo** — obendrauf
kommt nur die Multi-Tenant-Schicht (Login, Session-Store, Dashboard). Kein
zweites System; Verbesserungen fließen in beide Richtungen.

### 2a. Skool-Verbindung: gestreamter Remote-Login

Skools `auth_token` ist httpOnly und die WAF-Challenge löst nur in echten
Browsern — genau deshalb macht [`auth.py`](../catknows/auth.py) den Login
lokal über Playwright. Gehostet ist der Zielweg **direkt** der gestreamte
Remote-Login (keine Passwort-Zwischenlösung): ein serverseitiger Browser,
der dem Nutzer als interaktives Fenster ins Dashboard gestreamt wird.

1. Nutzer tippt „Skool verbinden".
2. Der Server öffnet Skools **echte** Login-Seite in einer Browser-Session
   auf dem VPS und streamt sie live ins Dashboard (Handy tauglich).
3. Der Nutzer loggt sich normal ein — **auch per Google/Apple-SSO**, weil es
   aus seiner Sicht ein echter Browser ist. Das Passwort tippt er bei Skool,
   **nie bei uns**.
4. Der Login passiert im Browser auf unserem Server → das `auth_token`-Cookie
   landet direkt in unserer (verschlüsselten) Jar.

- Vorteil: Nutzer gibt uns nie sein Passwort, SSO-Nutzer sind ohne Umweg dabei
  — ein einziger Weg für alle, kein Caveat.
- Pro Nutzer bleibt das Browser-Profil (verschlüsselt) erhalten — läuft die
  Session ab, erneuert sie sich headless und lautlos, wie lokal mit
  `profile_dir`.
- Nach dem Verbinden gleicht der Server die E-Mail des Skool-Profils mit der
  Login-E-Mail des catknows-Accounts ab — niemand hängt (versehentlich oder
  absichtlich) eine fremde Skool-Session in sein Konto.
- Cookie einfügen bleibt als Power-User-Fallback erhalten.
- **Aufwand, ehrlich:** echte Komponente (Remote-Browser-Streaming via
  CDP/WebRTC, eine Session pro Nutzer; Bausteine: Browserbase, Steel.dev,
  self-hosted Neko). Das ist der größte Brocken des Projekts und wird direkt
  richtig gebaut, statt eine Passwort-Übergangslösung wegzuwerfen.

**Ein echter OAuth-Redirect zu Skool geht nicht** — Skool bietet keinen
solchen Endpoint, und ein Login in Skools Domain im *eigenen* Handy-Browser
setzt das httpOnly-`auth_token`-Cookie auf `skool.com`, das unsere Domain
per Same-Origin-Policy nie lesen kann. Darum der Umweg über den Server-Browser.

## 3. Website und MCP auf einem Server?

Ja — Standard-Setup, ein VPS reicht: Caddy als Reverse Proxy, Website unter
der Hauptdomain, MCP unter `mcp.`-Subdomain. Alternativ (noch günstiger und
schneller): Website auf Cloudflare Pages (gratis, CDN), nur der MCP-Prozess
auf dem VPS. Getrennte Server sind nicht nötig.

## 4. Sicherheit & Produktprinzipien

Nicht verhandelbar — zugleich die Anforderungen für Anthropics
Connector-Verzeichnis:

1. **OAuth 2.1 mit PKCE**, Dynamic Client Registration, 401-Discovery,
   HTTPS-only. Kein statisches Token, kein Authless-Betrieb.
2. **Tenant-Isolation:** jeder Request läuft im Kontext genau eines Nutzers,
   mit dessen eigener Skool-Session. Sessions verschlüsselt at rest, nie in
   Logs, vom Nutzer selbst löschbar (inkl. Konto-Löschung).
3. **Read-first. Write bleibt doppelt gesichert:** per Account-Einstellung im
   Dashboard aktivieren (nie aus dem Chat heraus), Draft-first-confirm im
   Chat bleibt. Default: read-only.
4. **Keine Bulk-/Broadcast-Funktionen — bewusst.** Keine Massen-DMs, keine
   Auto-Post-Schleifen. Serverseitige Rate-Limits pro Nutzer. catknows ist
   eine Brücke für deine eigene Community-Arbeit, kein Spam-Kanon. Das ist
   Produktphilosophie und schützt zugleich die Nutzer-Accounts.
5. **Tool-Annotations** (`readOnlyHint` / `destructiveHint`) auf jedem Tool,
   **öffentliche Privacy Policy** (Pflicht fürs Verzeichnis, Pflicht nach
   DSGVO — wir verarbeiten personenbezogene Daten im Auftrag der Nutzer).
6. Prompt-Injection-Bremse unverändert: Skool-Inhalte sind fremder Text;
   nichts geht ohne menschliches „ja" raus.

### 4a. Auth: Keycloak auf demselben VPS (entschieden 2026-08-11)

Der ursprüngliche Plan nannte WorkOS AuthKit. Zwei geprüfte Fakten haben die
Wahl verschoben:

- **Dynamic Client Registration (RFC 7591) ist die harte Anforderung.** Ohne
  DCR kann sich claude.ai nicht selbst als Client registrieren — seit der
  MCP-Spec 11/2025 faktisch Pflicht. **Zitadel kann das nicht**
  ([zitadel#9810](https://github.com/zitadel/zitadel/issues/9810)), fällt
  damit aus, obwohl es sonst gut gepasst hätte.
- **Zitadels EU-Region kostet 100 $/Monat** — der Free-Tier (25k MAU) liegt
  nicht in der EU. Das Zehnfache der gesamten Serverkosten.

Blieben WorkOS (US) und Keycloak. Keycloak gewinnt, weil es als einziges
beide Kriterien erfüllt: DCR **und** Datenhaltung in Nürnberg. Nutzerkonten
sind bei diesem Produkt keine Nebensache — wer catknows nutzt, hängt seine
Skool-Session daran; Identität und Daten am selben Ort zu halten ist
konsistenter, als beides zu trennen.

**Der Preis, ehrlich:** wir betreiben einen Identity Provider mit
(Sicherheitsupdates, Backup der Keycloak-DB), und er will ~1 GB RAM neben den
Chromium-Instanzen. Auf dem VPS 500 (4 GB) wird das eng, sobald mehrere Nutzer
gleichzeitig aktiv sind → dann Upgrade auf VPS 1000 (8 GB, 10,36 €).
Fällt Keycloak aus, kommt niemand mehr rein — Monitoring ist hier keine Kür.

**Im MCP-SDK ist die Anbindung klein:** `TokenVerifier` ist ein Protokoll mit
genau einer Methode (`verify_token(token) -> AccessToken | None`). Ein
JWKS-Check gegen Keycloak sind ~30 Zeilen. Das SDK bringt keinen fertigen
Verifier mit — der `AuthKitProvider` aus dem alten Plan gehört zum
Drittanbieter-Paket `fastmcp`, das wir nicht verwenden.

## 5. Kosten (Ziel: so niedrig wie möglich)

Gebucht (Stand 2026-08-11):

| Posten | Lösung | Kosten |
|---|---|---|
| Server (MCP + ggf. Website) | netcup VPS 500 G12, Standort Nürnberg | 6,81 €/Monat |
| TLS | Caddy / Let's Encrypt | 0 € |
| Auth inkl. Google-Login | WorkOS AuthKit (frei bis 1 Mio. MAU) | 0 € |
| Website | Cloudflare Pages | 0 € |
| Domain `catknows.app` | bei netcup (Registrar-Wechsel später möglich) | 3,20 €/Monat |

**Gesamt: 10,01 €/Monat.** Mehr als die ursprünglich geschätzten 6 € — die
Speicherpreise sind im Frühjahr 2026 gestiegen, Nürnberg kostet +0,90 €, und
die Domain liegt beim selben Anbieter statt beim at-cost-Registrar (~25 €/Jahr
Aufpreis, dafür eine Rechnung und ein Panel).

**Warum netcup und nicht der billigste Anbieter:** deutsches Unternehmen,
eigenes Rechenzentrum in Nürnberg — der Server mit fremden Mitgliederdaten
steht damit unter deutschem Recht, nicht nur "in der EU". Und die 128 GB NVMe
sind hier kein Luxus: jeder Nutzer bekommt ein persistentes Chromium-Profil
(§2a), gemessen ~130 MB pro Profil. Ein 40-GB-Plan wäre im niedrigen
dreistelligen Nutzerbereich voll, und ein Umzug mit verschlüsselten Sessions
darauf ist die Migration, die man sich erspart.

**Warum `.app`:** die TLD steht komplett auf der HSTS-Preload-Liste — Browser
verweigern `http://` grundsätzlich. Für einen Endpoint, der Skool-Sessions
hält, ist das eine Sicherheitseigenschaft gratis von der Registry.
`catknows.com` war seit 2014 vergeben, `.net` seit Mai 2026.

## 6. Phasen

- **Phase 0 — Fundament.** Domain `catknows.app` und netcup VPS 500 (Nürnberg)
  bestellt ✅. VPS aufsetzen: Rezept liegt fertig in
  [deploy/](../deploy/) (Caddyfile, systemd-Unit, Schritte) — offen, bis der
  Server läuft. Auth-Projekt: erst nach der Anbieter-Entscheidung (§7.1).
- **Phase 1 — MCP remote.** Streamable-HTTP-Transport in `mcp_server.py` ✅
  (`CATKNOWS_HTTP=1`, Tool-Annotations, lokal gegen den MCP-Inspector
  verifiziert). Offen: OAuth-Schicht und der Test als claude.ai-Connector —
  beides braucht den laufenden Server. Damit ist das Selbst-Hosting-Zielbild
  aus dem alten Plan nebenbei miterfüllt.
- **Phase 2 — Multi-Tenant + gestreamter Login.** Session-Store pro Nutzer
  (verschlüsselt), Mini-Dashboard: Login, Skool per gestreamtem Remote-Login
  verbinden (§2a — direkt der Zielweg, keine Passwort-Zwischenstufe),
  Write-Toggle, Daten löschen. Kleiner Beta-Kreis aus der Community.
- **Phase 3 — Öffentlich.** Website/Landing + Privacy Policy, Anleitung
  („in 3 Minuten verbunden"), OSS-Variante prominent verlinkt (About,
  Banner, Community).
- **Phase 4 — Verzeichnis.** Submission ins Anthropic-Connector-Verzeichnis
  (Annotations, Test-Account, 10-Minuten-Doku für Reviewer). Danach: ein
  Klick statt URL-Copy-Paste.

## 7. Offene Punkte

1. ~~Auth-Anbieter~~ — **entschieden 2026-08-11: Keycloak, selbst gehostet.**
   Siehe §4a.
2. **Preismodell der Komfort-Schicht** (frei bis N Abrufe? Business-Tier?) —
   Entscheidung vor Phase 3, die Technik hängt nicht daran.
3. **Backlog aus der Community:** Config-Datei (u. a. Vault-Pfad),
   Verhalten bei geteilten/synchronisierten Vaults, Members-Listing in
   Communities ohne Admin-Rechte robuster machen.
