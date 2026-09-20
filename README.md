# Radar rynku pracy IT

Narzędzie, które odpowiada na pytania, na które nie odpowiada żadna wyszukiwarka ofert:

- **Które technologie realnie wpuszczają początkujących?** Java ma tysiące ofert, ale jeśli 97% z nich to seniorzy, dla juniora ten rynek jest zamknięty. Liczy się `junior_ratio`, nie popularność.
- **Umiem X — czego douczyć się następnego?** Liczone ze współwystępowania technologii w realnych ogłoszeniach, nie z czyjejś roadmapy na YouTube.
- **Jak to się zmienia w czasie?** Snapshoty są append-only, więc po kilku miesiącach powstaje szereg czasowy, którego nie da się odtworzyć wstecz — oferty znikają.

Dane pochodzą z [czyjesteldorado.pl](https://czyjesteldorado.pl) przez jego [eksperymentalny serwer MCP](https://github.com/orgs/Czy-Jest-Eldorado/discussions/54) — agregatora, który indeksuje portale pracy, systemy ATS i strony kariery z deduplikacją.

## Szybki start

Bez zależności — potrzebny tylko Python 3.11+.

```bash
python -m radar.collect --dry-run          # pokaż, co zostanie odpytane
python -m radar.collect                    # pełny przebieg (~4 min)
python -m radar.analyze                    # raport w konsoli + data/reports/<data>.json
```

Węższy przebieg, gdy chcesz tylko sprawdzić jeden obszar:

```bash
python -m radar.collect --groups frontend,backend --seniorities junior,mid
python -m radar.analyze --anchors React,TypeScript
```

## Dashboard

```bash
python -m http.server        # i wejdz na http://localhost:8000
```

Statyczna strona bez zaleznosci i bez build stepu — czysty HTML, CSS i SVG
rysowane z `data/reports/<data>.json`. Cala matematyka dzieje sie wczesniej, w
`radar.analyze`; dashboard tylko rysuje gotowe liczby.

Trzeba go podac przez serwer HTTP, a nie otworzyc plik z dysku — przegladarka
blokuje wtedy odczyt JSON-a. Strona sama to zglosi, jesli sprobujesz.

Dashboard czyta `data/reports/index.json` (spis raportow aktualizowany przez
`radar.analyze`) i bierze najnowszy, wiec nie ma nigdzie wpisanej daty na
sztywno.

Kazda karta niesie znacznik zrodla — `dokladne liczby` albo `proba` — zgodnie
z podzialem opisanym nizej. Kazdy wykres ma tez blizniaczy widok tabeli.

## Architektura

```
radar/
  mcp_client.py   klient MCP: sesja, retry z backoffem, throttling, parsowanie SSE
  queries.py      macierz pomiarowa — co mierzymy (jedyny plik, który zmienia się często)
  collect.py      przebieg zbierania → surowe pliki append-only
  analyze.py      metryki liczone offline z tego, co już na dysku
  profile.py      profil kandydata: CV, eksport LinkedIna, schemat
  match.py        dopasowywanie ofert do profilu — wyłącznie lokalnie
data/
  snapshots/<data>/measurements.jsonl   jedna linia = jedna komórka macierzy
  snapshots/<data>/jobs.jsonl           korpus ofert, deduplikowany po ID
  snapshots/<data>/manifest.json        metadane przebiegu + lista błędów
  reports/<data>.json                   policzony raport
  reports/index.json                    spis raportów dla dashboardu
profile/                                CV, profil i wyniki — NIE w repo (.gitignore)
index.html                              dashboard
assets/dashboard.css                    tokeny kolorów, układ
assets/dashboard.js                     wykresy SVG, bez zależności
```

Zbieranie i analiza są rozdzielone celowo (ELT, nie ETL). Zmiana pomysłu na metrykę nie wymaga ponownego odpytywania serwera — a historycznych danych i tak nie dałoby się odtworzyć.

## Uczciwość danych

Serwer zwraca maksymalnie **50 ofert na zapytanie** i nie ma paginacji, ale podaje `total_results` — pełną liczbę dopasowań. To są dwa różne poziomy zaufania i projekt ich nie miesza:

| Źródło | Co to jest | Do czego używamy |
|---|---|---|
| `total_results` | dokładna liczba ofert | liczenie popytu, `junior_ratio`, udziały rynku |
| `jobs.jsonl` | próba ≤50 na zapytanie, sortowana od najnowszych | rozkłady: widełki, współwystępowanie technologii |

Każda metryka w raporcie ma pole `source`. Liczenie „ilu jest ofert" z próby byłoby najłatwiejszym sposobem, żeby skłamać wykresem.

Dodatkowe zastrzeżenia:

- Widełki ujawnia tylko część ogłoszeń — `disclosure_rate` w raporcie mówi jaka. Mediany dotyczą wyłącznie tej części i są zawyżone względem całego rynku.
- Poziom `seniority` pochodzi z klasyfikacji agregatora, nie z treści ogłoszenia.
- Dopasowanie technologii idzie po frazie w ofercie, więc „Go" czy „R" łapią fałszywe trafienia. Frazy są cudzysłowione, co ogranicza problem, ale go nie usuwa.

## Dopasowywanie ofert do profilu

Radar mierzy rynek. To jest druga, osobista strona tego samego korpusu: które
konkretne oferty pasują do Ciebie i czego Ci do nich brakuje.

```bash
python -m radar.profile --init                 # szkielet profilu do wypełnienia
python -m radar.profile --from-cv profile/cv.txt
python -m radar.profile --from-linkedin ~/Downloads/Basic_LinkedInDataExport.zip
python -m radar.match --html                   # ranking + profile/matches.html
```

**Wszystko dzieje się lokalnie.** Katalog `profile/` (CV, eksport z LinkedIna,
wyniki) jest w `.gitignore` — repozytorium jest publiczne, a to są dane osobowe.
Żaden kod w `match.py` ani `profile.py` niczego nie wysyła do sieci.

LinkedIn czytamy z **oficjalnego eksportu** (Ustawienia → Prywatność danych →
Pobierz kopię swoich danych), a nie przez scrapowanie — scrapowanie łamie ich
regulamin i technicznie i tak nie działa. Eksport daje te same dane, legalnie.
CV podaj jako `.txt`; parsera PDF-ów nie będzie, bo projekt nie ma zależności.

### Jak liczona jest ocena

Pokazujemy dwie liczby, bo mówią o czym innym:

| Liczba | Odpowiada na pytanie |
|---|---|
| **Pokrycie** | jaki % wymagań oferty spełniasz — „czy mam szanse" |
| **Trafienia** | ile Twoich umiejętności oferta wymienia — „czy to o mnie, czy przypadek" |

Samo pokrycie kłamie: oferta z jednym wymaganiem, które akurat masz, to 100%
pokrycia i zero informacji. Dlatego wynik końcowy mnożymy przez nasycenie liczbą
trafień — pełny kredyt dopiero od trzech. To ta sama zasada, co próg minimalnej
liczby ofert na dashboardzie: mały mianownik zmyśla wyniki.

Oferta bez ujawnionych widełek **nie** odpada przy ustawionym `salary_min` —
widełki podaje około połowy ogłoszeń i odrzucanie reszty za milczenie
ucięłoby pół rynku. Tak samo oferta bez przypisanego poziomu.

Najciekawszy jest nie sam ranking, tylko sekcja **„czego brakuje najczęściej"**:
liczy braki w ofertach, które i tak do Ciebie pasują, czyli pokazuje, czego
douczyć się, żeby te same oferty stały się osiągalne.

## Cotygodniowy przebieg

`.github/workflows/collect.yml` odpala `radar.collect` + `radar.analyze` w każdy
poniedziałek o 06:00 UTC i commituje wynik. Dashboard na Pages przebudowuje się
sam po tym commicie, więc dane odświeżają się bez niczyjego udziału.

Można go też odpalić ręcznie: Actions → *Cotygodniowy przebieg radaru* → Run workflow.

Korpus ofert powstaje na runnerze, bo analiza go potrzebuje, i ginie razem z nim.
Do repo idą wyłącznie liczby zagregowane — commitowane z białej listy ścieżek,
nie przez `git add -A`. Gdyby korpus mimo to trafił do indeksu, przebieg przerywa
się błędem zamiast go opublikować.

Uwaga: GitHub wyłącza zaplanowane workflow po 60 dniach bez żadnej aktywności
w repo. Jeden ręczny run albo commit resetuje ten licznik.

## Etyka i obciążenie serwera

Serwer MCP jest w bardzo wczesnej becie, udostępniony za darmo przez społeczność. Klient trzyma domyślnie **1 zapytanie na 1,2 s**, robi wykładniczy backoff z jitterem przy błędach i przedstawia się w `User-Agent`. Pełny przebieg to ~190 zapytań, czyli jedna kolekcja dziennie to znikome obciążenie. Nie zwiększaj `--interval` poniżej 1 s.

## Status

Wczesna wersja. Zrobione: klient MCP, macierz pomiarowa, zbieranie, metryki, raport JSON,
dashboard dla pojedynczego snapshotu, cotygodniowy przebieg z crona,
lokalne dopasowywanie ofert do profilu.
Następne: szereg czasowy między snapshotami, kanał powiadomień dla dopasowań.

Dashboard celowo nie pokazuje jeszcze trendów — jest jeden snapshot, a wykres czasowy
z jednym punktem udaje wiedzę, której nie ma. Trendy dochodzą, gdy uzbiera się kilka przebiegów.
