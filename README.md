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

## Architektura

```
radar/
  mcp_client.py   klient MCP: sesja, retry z backoffem, throttling, parsowanie SSE
  queries.py      macierz pomiarowa — co mierzymy (jedyny plik, który zmienia się często)
  collect.py      przebieg zbierania → surowe pliki append-only
  analyze.py      metryki liczone offline z tego, co już na dysku
data/
  snapshots/<data>/measurements.jsonl   jedna linia = jedna komórka macierzy
  snapshots/<data>/jobs.jsonl           korpus ofert, deduplikowany po ID
  snapshots/<data>/manifest.json        metadane przebiegu + lista błędów
  reports/<data>.json                   policzony raport
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

## Etyka i obciążenie serwera

Serwer MCP jest w bardzo wczesnej becie, udostępniony za darmo przez społeczność. Klient trzyma domyślnie **1 zapytanie na 1,2 s**, robi wykładniczy backoff z jitterem przy błędach i przedstawia się w `User-Agent`. Pełny przebieg to ~190 zapytań, czyli jedna kolekcja dziennie to znikome obciążenie. Nie zwiększaj `--interval` poniżej 1 s.

## Status

Wczesna wersja. Zrobione: klient MCP, macierz pomiarowa, zbieranie, metryki, raport JSON.
Następne: szereg czasowy między snapshotami, dashboard, cotygodniowy przebieg z crona.
