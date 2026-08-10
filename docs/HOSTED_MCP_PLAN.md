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
4. Im catknows-Web-Dashboard hinterlegt er einmalig seinen Skool-Zugang
   (Session), damit der Server in **seinem** Namen lesen kann.
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

## 5. Kosten (Ziel: so niedrig wie möglich)

| Posten | Lösung | Kosten |
|---|---|---|
| Server (MCP + ggf. Website) | kleiner VPS (z. B. Hetzner CX22) | ~4,50 €/Monat |
| TLS | Caddy / Let's Encrypt | 0 € |
| Auth inkl. Google-Login | WorkOS AuthKit (frei bis 1 Mio. MAU) | 0 € |
| Website | Cloudflare Pages | 0 € |
| Domain | at-cost-Registrar (Cloudflare/Porkbun) | ~10–12 €/Jahr |

**Gesamt: unter 6 €/Monat.** Eine neue Domain wird ohnehin gebraucht;
`.com`/`.app`/`.dev` sind die günstigen TLDs, `.io`/`.ai` lohnen nicht.

## 6. Phasen

- **Phase 0 — Fundament.** Domain sichern, VPS aufsetzen (Caddy, systemd),
  AuthKit-Projekt anlegen.
- **Phase 1 — MCP remote.** `mcp_server.py`: Streamable-HTTP-Modus +
  AuthKit-OAuth (FastMCP `AuthKitProvider`). Erst Single-User gegen den
  eigenen Account testen (MCP-Inspector, dann claude.ai-Connector, dann
  Handy). Damit ist das Selbst-Hosting-Zielbild aus dem alten Plan nebenbei
  miterfüllt.
- **Phase 2 — Multi-Tenant.** Session-Store pro Nutzer (verschlüsselt),
  Mini-Dashboard: Login, Skool-Session hinterlegen, Write-Toggle,
  Daten löschen. Kleiner Beta-Kreis aus der Community.
- **Phase 3 — Öffentlich.** Website/Landing + Privacy Policy, Anleitung
  („in 3 Minuten verbunden"), OSS-Variante prominent verlinkt (About,
  Banner, Community).
- **Phase 4 — Verzeichnis.** Submission ins Anthropic-Connector-Verzeichnis
  (Annotations, Test-Account, 10-Minuten-Doku für Reviewer). Danach: ein
  Klick statt URL-Copy-Paste.

## 7. Offene Punkte

1. **Domain-Wahl** (Verfügbarkeit checken; die alte Domain steht nicht mehr
   zur Verfügung).
2. **Preismodell der Komfort-Schicht** (frei bis N Abrufe? Business-Tier?) —
   Entscheidung vor Phase 3, die Technik hängt nicht daran.
3. **Backlog aus der Community:** Config-Datei (u. a. Vault-Pfad),
   Verhalten bei geteilten/synchronisierten Vaults, Members-Listing in
   Communities ohne Admin-Rechte robuster machen.
