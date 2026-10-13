@echo off
setlocal
chcp 65001 >nul
title Briefing
cd /d "%~dp0"

set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if not exist "%CLAUDE%" set "CLAUDE=claude"

echo ============================================
echo   Briefing erzeugen
echo ============================================
echo.

echo [1/6] Aktuellen Stand holen ...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo FEHLER: git pull ist gescheitert. Internetverbindung pruefen.
  goto :ende
)

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "HEUTE=%%d"
set "DATEI=episodes\%HEUTE%.md"

if exist "%DATEI%" (
  echo.
  echo Fuer heute gibt es schon eine Folge: %DATEI%
  choice /c JN /m "Neu erzeugen und ueberschreiben"
  if errorlevel 2 goto :ende
  del "%DATEI%"
)

echo.
echo [2/6] Nachrichten herunterladen ...
python scripts\fetch.py
if errorlevel 1 (
  echo.
  echo FEHLER: Keine Nachrichten geladen. Internetverbindung pruefen.
  goto :ende
)

echo.
echo [3/6] Claude liest die Nachrichten und schreibt das Skript.
echo       Das dauert einige Minuten, waehrenddessen erscheint keine Ausgabe.
echo.
"%CLAUDE%" -p "Lies die Datei BRIEFING.md und fuehre die Anweisungen darin aus. Heutiges Datum: %HEUTE%. Zieldatei: episodes/%HEUTE%.md" ^
  --restricted ^
  --tools "Read,Write" ^
  --allowedTools "Read,Edit(episodes/**)" ^
  --permission-prompts none

if not exist "%DATEI%" (
  echo.
  echo FEHLER: Claude hat keine Datei %DATEI% geschrieben.
  echo Die Ausgabe oben zeigt den Grund. Es wurde nichts hochgeladen.
  goto :ende
)

echo.
echo [4/6] Skript speichern ...
git add "%DATEI%"
git commit -q -m "Folge %HEUTE%"
if errorlevel 1 (
  echo FEHLER: Commit gescheitert.
  goto :ende
)

echo [5/6] Hochladen ...
git push -q
if errorlevel 1 (
  echo.
  echo FEHLER: Hochladen gescheitert. Das Skript ist lokal gespeichert.
  echo Spaeter erneut versuchen mit: git push
  goto :ende
)

echo [6/6] GitHub vertont die Folge, das dauert etwa 2 Minuten ...
powershell -NoProfile -Command "Start-Sleep -Seconds 10"
for /f %%r in ('gh run list -L 1 --json databaseId -q ".[0].databaseId"') do set "RUN=%%r"
gh run watch %RUN% --exit-status --interval 15 >nul
if errorlevel 1 (
  echo.
  echo FEHLER bei der Vertonung. Details: gh run view %RUN% --log-failed
  echo Nur Veroeffentlichung wiederholen: gh run rerun %RUN% --failed
  goto :ende
)

echo.
echo Fertig. Die Folge ist jetzt in AntennaPod abrufbar
echo (in der Abo-Liste nach unten ziehen zum Aktualisieren).

:ende
echo.
pause
