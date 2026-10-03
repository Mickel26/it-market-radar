@echo off
rem Matcher ofert - dwuklik uruchamia lokalny interfejs w przegladarce.
rem Dziala wylacznie na tym komputerze: profil i korpus ofert nie wychodza do sieci.

cd /d "%~dp0"

where py >/dev/null 2>nul
if %errorlevel%==0 (
  py -3 -m radar.server
) else (
  python -m radar.server
)

rem Okno zostaje otwarte tylko przy bledzie - zeby dalo sie przeczytac komunikat.
if errorlevel 1 pause
