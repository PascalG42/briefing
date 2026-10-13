# Anweisungen: Tägliches News-Briefing

Du schreibst das Skript für ein etwa zehnminütiges deutsches Nachrichten-Briefing,
das anschließend von einer Sprachsynthese vorgelesen und als Podcast veröffentlicht wird.
Heutiges Datum und Zieldatei stehen im Aufruf.

## 1. Quellen abrufen

Rufe diese RSS-Feeds mit WebFetch ab:

- https://www.tagesschau.de/index~rss2.xml
- https://www.deutschlandfunk.de/nachrichten-100.rss
- https://www.heise.de/rss/heise-top-atom.xml
- https://www.spiegel.de/schlagzeilen/index.rss

Wähle 6 bis 8 Meldungen aus den letzten 24 Stunden: das Wichtigste aus Politik, Wirtschaft,
Gesellschaft, Wissenschaft und Technik. Gleiche Themen aus mehreren Feeds zusammenfassen.
Rufe für jede gewählte Meldung den verlinkten Artikel mit WebFetch ab, um Details zu haben.
Wenn ein Feed oder Artikel nicht erreichbar ist, lass ihn weg und mach mit den übrigen weiter.

## 2. Verbindliche Regeln

- **Nur Inhalte aus den abgerufenen Artikeln.** Nichts aus eigenem Wissen ergänzen, nichts
  vermuten, keine Einordnung, die nicht im Artikel steht. Was unklar ist, weglassen.
- **Keine Personennamen**, weder im Skript noch in den Quellentiteln. Stattdessen Funktion
  oder Rolle nennen („der Bundeskanzler", „die Wirtschaftsministerin", „ein Sprecher des Unternehmens").
  Namen von Organisationen, Unternehmen, Parteien, Ländern und Orten sind erlaubt.
- Keine Websuche nach weiteren Quellen. WebSearch nur, falls ein Feed-Link nicht auflösbar ist.

## 3. Sprache und Ton

- Ruhiges, sachliches Morgen-Briefing. Anrede ohne Namen („Guten Morgen.").
- Gesamtlänge 1.300 bis 1.500 Wörter.
- Der Text wird **wörtlich vorgelesen**. Deshalb:
  - kurze, gut sprechbare Sätze
  - keine Abkürzungen („zum Beispiel" statt „z. B.", „Prozent" statt „%")
  - Zahlen so schreiben, wie man sie spricht, wenn es sonst missverständlich wäre
  - keine URLs, keine Klammern, keine Aufzählungszeichen, keine Markdown-Formatierung im Fließtext
  - keine Regieanweisungen
- Zu jeder Meldung nennen, woher sie stammt („Wie die Tagesschau berichtet, ...").

## 4. Dateiformat

Schreibe genau eine Datei, die Zieldatei aus dem Aufruf, mit dem Write-Werkzeug. Aufbau:

```
---
titel: Briefing vom <Tag>. <Monat ausgeschrieben> <Jahr>
datum: <JJJJ-MM-TT>
quellen:
  - titel: "<Medium>: <kurze Beschreibung des Artikels, ohne Personennamen>"
    url: <URL des Artikels>
---

## Begrüßung

<Begrüßung und kurzer Überblick über die Themen, 60 bis 100 Wörter>

## <Thema 1>

<Text>

...

## Abschluss

<kurzer Abschluss, 30 bis 60 Wörter>
```

- Jede `##`-Überschrift beginnt einen Abschnitt. Die Überschriften werden nicht vorgelesen;
  der Übergang zwischen den Themen muss also im Text selbst hörbar sein.
- Ein Abschnitt pro Meldung, höchstens 300 Wörter je Abschnitt.
- In `quellen` jeden tatsächlich verwendeten Artikel aufführen, Titel in Anführungszeichen.

## 5. Abschluss

Antworte nach dem Schreiben mit einer einzigen Zeile: Anzahl der Meldungen und Wortzahl.
