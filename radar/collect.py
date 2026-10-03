"""Zbieranie danych: przechodzi macierz zapytan i zapisuje surowy wynik.

Zasada: ten modul NICZEGO nie interpretuje. Zapisuje to, co powiedzial serwer,
w formie append-only. Cala analiza dzieje sie pozniej, na dysku. Dzieki temu
zmiana pomyslu na metryke nie wymaga ponownego odpytywania serwera - a
historycznych danych i tak nie da sie odtworzyc, bo oferty znikaja.

Uzycie:
    python -m radar.collect                      # pelny przebieg
    python -m radar.collect --seniorities junior,mid
    python -m radar.collect --groups frontend,backend --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .mcp_client import EldoradoClient, RateLimited
from .queries import DEFAULT_WINDOW, SENIORITIES, TECHNOLOGIES, Query, build_matrix

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOB_ID_RE = re.compile(r"/praca/(\d+)-")


def job_id(url: str) -> str | None:
    """Wyciaga stabilne ID oferty z URL-a (/praca/440489-slug -> '440489')."""
    match = JOB_ID_RE.search(url or "")
    return match.group(1) if match else None


@dataclass
class RunStats:
    queries_ok: int = 0
    queries_failed: int = 0
    jobs_seen: int = 0
    unique_jobs: int = 0
    rate_limited: int = 0
    search_calls: int = 0
    failures: list[str] = field(default_factory=list)


class SnapshotWriter:
    """Zapisuje jeden przebieg do data/snapshots/<data>/."""

    def __init__(self, root: Path, run_date: date) -> None:
        self.dir = root / "snapshots" / run_date.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._measurements = (self.dir / "measurements.jsonl").open("a", encoding="utf-8")
        self._jobs = (self.dir / "jobs.jsonl").open("a", encoding="utf-8")
        self._seen_jobs: set[str] = set()

    def write_measurement(self, query: Query, payload: dict[str, Any]) -> None:
        record = {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "kind": query.kind,
            "label": query.label,
            "group": query.group,
            "seniority": query.seniority,
            "total_results": payload.get("total_results", 0),
            "shown_results": payload.get("shown_results", 0),
            "arguments": query.arguments,
        }
        self._measurements.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._measurements.flush()

    def write_jobs(self, jobs: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Zapisuje oferty, pomijajac te juz widziane w tym przebiegu."""
        total = new = 0
        for job in jobs:
            total += 1
            identifier = job_id(job.get("url", "")) or job.get("url", "")
            if not identifier or identifier in self._seen_jobs:
                continue
            self._seen_jobs.add(identifier)
            new += 1
            self._jobs.write(json.dumps({"id": identifier, **job}, ensure_ascii=False) + "\n")
        self._jobs.flush()
        return total, new

    def write_manifest(self, run_record: dict[str, Any]) -> None:
        """Dopisuje przebieg do manifestu dnia, nie nadpisujac poprzednich.

        Kilka przebiegow tego samego dnia dopisuje sie do tych samych plikow,
        wiec manifest musi pamietac je wszystkie. Wczesniej nieudany przebieg
        zamazywal zerami zapis udanego - a to jedyny slad, ile danych w tym
        katalogu faktycznie jest.

        Pola na gorze opisuja ostatni przebieg, ktory COKOLWIEK zebral: przebieg
        z zerem udanych zapytan nie dodal ani jednej linii do snapshotu, wiec
        nie ma prawa zmieniac jego opisu. Trafia tylko do historii w `runs`.
        """
        path = self.dir / "manifest.json"
        previous: dict[str, Any] = {}
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}

        runs = list(previous.get("runs") or [])
        if not runs and previous.get("started_at"):
            # Manifest sprzed wprowadzenia historii: jego jedyny przebieg
            # staje sie pierwszym wpisem, zamiast przepasc.
            runs.append({k: v for k, v in previous.items() if k != "runs"})
        runs.append(run_record)

        head = run_record if run_record.get("queries_ok") else {k: v for k, v in previous.items() if k != "runs"}
        manifest = {**(head or run_record), "runs": runs}
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        self._measurements.close()
        self._jobs.close()

    @property
    def unique_jobs(self) -> int:
        return len(self._seen_jobs)


# --- limit serwera ---------------------------------------------------------
# Od pazdziernika 2026 serwer pozwala na 100 wyszukiwan pod rzad, potem jedno
# na minute i 500 dziennie - i prosi, zeby powiedziec o tym uzytkownikowi.
# Macierz ma 189 zapytan, wiec pelny przebieg MUSI przejsc na wolne tempo.
# Szanujemy limit, zamiast z nim walczyc: to darmowa beta, a operator jasno
# napisal zasady.
BURST_SEARCHES = 95          # zapas 5 pod limitem 100 "pod rzad"
SLOW_INTERVAL = 61.0         # "jedno na minute", z sekunda zapasu
RATE_LIMIT_WAITS = 3         # ile kolejnych minut czekamy na jedno zapytanie
DAILY_SEARCH_CAP = 480       # zapas 20 pod limitem 500 dziennie
# Ile zapytan pod rzad musi odpasc na limicie MIMO minutowych przerw, zebysmy
# uznali, ze to limit dzienny (np. inny przebieg tego dnia go zuzyl).
# Dalsze czekanie nic by nie dalo - serwer odmowi do polnocy.
DAILY_LIMIT_STREAK = 3

# --- prawdziwe awarie (nie limit) -------------------------------------------
# Ile nieudanych zapytan pod rzad uznajemy za awarie serwera, a nie pech.
FAILURE_STREAK = 5
# Przerwa po takiej serii, zeby nie odpytywac martwego serwera co sekunde.
COOLDOWN_SECONDS = 90
# Ile takich przerw w jednym przebiegu, zanim uznamy, ze dzis sie nie da.
MAX_COOLDOWNS = 3
# Przerwa przed druga runda dla zapytan, ktore padly w pierwszej.
RETRY_PASS_DELAY = 60
# Twardy limit czasu calego zbierania. Przy wolnym tempie pelny przebieg trwa
# ~100 minut; budzet konczy go z tym, co jest, zanim GitHub zabije job.
DEFAULT_BUDGET_MINUTES = 110.0


class OutOfTime(RuntimeError):
    """Skonczyl sie budzet czasu - konczymy z tym, co jest."""


class OutOfQuota(RuntimeError):
    """Doszlismy do dziennego limitu serwera - dalsze zapytania i tak by odpadly."""


class ServerUnavailable(RuntimeError):
    """Serwer nie wrocil mimo kolejnych przerw - konczymy z tym, co jest."""


class _Pass:
    """Jedna runda po liscie zapytan: tempo zgodne z limitem i bezpiecznik."""

    def __init__(
        self,
        client: EldoradoClient,
        writer: SnapshotWriter,
        stats: RunStats,
        verbose: bool,
        deadline: float,
    ) -> None:
        self.deadline = deadline
        self.client = client
        self.writer = writer
        self.stats = stats
        self.verbose = verbose
        self.streak = 0
        self.cooldowns = 0
        self.limit_streak = 0

    def _slow_down(self, reason: str) -> None:
        if self.client.min_interval >= SLOW_INTERVAL:
            return
        self.client.min_interval = SLOW_INTERVAL
        if self.verbose:
            print(f"  -- {reason}: dalej jedno zapytanie co {SLOW_INTERVAL:.0f} s --", file=sys.stderr)

    def _check_limits(self) -> None:
        if self.client.search_calls >= DAILY_SEARCH_CAP:
            raise OutOfQuota(f"{self.client.search_calls} wyszukiwan - blisko dziennego limitu serwera (500)")
        if time.monotonic() + self.client.min_interval >= self.deadline:
            raise OutOfTime("skonczyl sie budzet czasu")

    def query(self, label: str, query: Query) -> str | None:
        """Wykonuje zapytanie. Zwraca opis bledu albo None przy sukcesie."""
        if self.client.search_calls >= BURST_SEARCHES:
            self._slow_down(f"{self.client.search_calls} wyszukiwan pod rzad")

        for wait in range(RATE_LIMIT_WAITS + 1):
            self._check_limits()
            try:
                payload = self.client.search_jobs(**query.arguments)
                break
            except RateLimited as exc:
                # Odmowa z powodu limitu to nie awaria: nie liczy sie do serii
                # bledow. Zwalniamy i probujemy tego samego zapytania w kolejnym
                # oknie - throttling klienta sam odczeka minute.
                self.stats.rate_limited += 1
                self._slow_down("serwer zglosil limit")
                if wait == RATE_LIMIT_WAITS:
                    if self.verbose:
                        print(f"  {label} LIMIT {query.label}: {exc}", file=sys.stderr)
                    self.limit_streak += 1
                    if self.limit_streak >= DAILY_LIMIT_STREAK:
                        raise OutOfQuota(
                            f"{self.limit_streak} zapytania pod rzad odrzucone mimo minutowych przerw - "
                            "najpewniej wyczerpany dzienny limit serwera"
                        )
                    return f"{query.kind}/{query.label}/{query.seniority}: limit serwera: {exc}"
            except Exception as exc:  # noqa: BLE001 - jedna zla komorka nie psuje przebiegu
                if self.verbose:
                    print(f"  {label} BLAD {query.label}: {exc}", file=sys.stderr)
                self.streak += 1
                if self.streak >= FAILURE_STREAK:
                    self._cool_down()
                return f"{query.kind}/{query.label}/{query.seniority}: {exc}"

        self.streak = 0
        self.limit_streak = 0
        self.writer.write_measurement(query, payload)
        seen, _ = self.writer.write_jobs(payload.get("jobs", []))
        self.stats.queries_ok += 1
        self.stats.jobs_seen += seen
        if self.verbose:
            total = payload.get("total_results", 0)
            print(f"  {label} {query.label:<18} {query.seniority or '-':<8} {total:>6}")
        return None

    def _cool_down(self) -> None:
        if time.monotonic() + COOLDOWN_SECONDS >= self.deadline:
            raise OutOfTime("przerwa ochronna nie zmiescilaby sie w budzecie czasu")
        if self.cooldowns >= MAX_COOLDOWNS:
            raise ServerUnavailable(
                f"{self.streak} bledow pod rzad mimo {MAX_COOLDOWNS} przerw po {COOLDOWN_SECONDS} s"
            )
        self.cooldowns += 1
        if self.verbose:
            print(
                f"  -- {self.streak} bledow pod rzad: przerwa {COOLDOWN_SECONDS} s "
                f"({self.cooldowns}/{MAX_COOLDOWNS}) --",
                file=sys.stderr,
            )
        # Bez zakladania nowej sesji: to wygladaloby jak obchodzenie limitu
        # "na klienta". Sesje odtwarza tylko _rpc, gdy serwer o niej zapomni (404).
        time.sleep(COOLDOWN_SECONDS)
        self.streak = 0


def run(
    matrix: list[Query],
    *,
    data_dir: Path = DATA_DIR,
    window: str = DEFAULT_WINDOW,
    interval: float = 1.2,
    verbose: bool = True,
    budget_minutes: float = DEFAULT_BUDGET_MINUTES,
) -> RunStats:
    """Przebieg zbierania odporny na przejsciowe awarie serwera.

    1. Pierwsza runda po calej macierzy. Seria bledow pod rzad uruchamia
       przerwe i nowa sesje, zamiast odpytywac martwy serwer dalej.
    2. Druga runda tylko dla zapytan, ktore padly - po minucie przerwy.
       Przejsciowa awaria w polowie przebiegu nie kosztuje wtedy polowy danych.
    3. Jesli serwer nie wraca mimo przerw albo skonczy sie budzet czasu,
       konczymy z tym, co juz jest zapisane. Kazda zebrana komorka jest nieodtwarzalna, wiec niczego
       nie wyrzucamy tylko dlatego, ze reszta sie nie udala.
    """
    stats = RunStats()
    writer = SnapshotWriter(data_dir, date.today())
    started = datetime.now(timezone.utc)
    failed: list[tuple[Query, str]] = []
    aborted: str | None = None
    deadline = time.monotonic() + budget_minutes * 60

    try:
        with EldoradoClient(min_interval=interval) as client:
            try:
                if verbose:
                    name = client.server_info.get("name", "?")
                    print(f"Polaczono z MCP: {name} | zapytan do wykonania: {len(matrix)}")

                first = _Pass(client, writer, stats, verbose, deadline)
                position = 0
                try:
                    for position, query in enumerate(matrix):
                        if error := first.query(f"[{position + 1}/{len(matrix)}]", query):
                            failed.append((query, error))
                except (ServerUnavailable, OutOfTime, OutOfQuota) as exc:
                    aborted = str(exc)
                    # Zapytanie, na ktorym bezpiecznik zadzialal, i wszystkie dalsze
                    # tez sa nieudane - licza sie do bledow, zeby manifest nie udawal
                    # pelnego przebiegu.
                    failed.extend((q, f"pominiete: {exc}") for q in matrix[position:])

                enough_time = time.monotonic() + RETRY_PASS_DELAY < deadline
                if failed and not aborted and enough_time:
                    if verbose:
                        print(f"\nDruga runda: {len(failed)} zapytan po {RETRY_PASS_DELAY} s przerwy")
                    time.sleep(RETRY_PASS_DELAY)
                    second = _Pass(client, writer, stats, verbose, deadline)
                    still_failed: list[tuple[Query, str]] = []
                    position = 0
                    try:
                        for position, (query, _) in enumerate(failed):
                            if error := second.query(f"[druga {position + 1}/{len(failed)}]", query):
                                still_failed.append((query, error))
                    except (ServerUnavailable, OutOfTime, OutOfQuota) as exc:
                        aborted = str(exc)
                        still_failed.extend(failed[position:])
                    failed = still_failed
            finally:
                stats.search_calls = client.search_calls
    finally:
        stats.queries_failed = len(failed)
        stats.failures = [error for _, error in failed]
        if aborted:
            stats.failures.insert(0, f"przerwano: {aborted}")
        stats.unique_jobs = writer.unique_jobs
        writer.write_manifest(
            {
                "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "window": window,
                "queries_total": len(matrix),
                "queries_ok": stats.queries_ok,
                "queries_failed": stats.queries_failed,
                "unique_jobs": stats.unique_jobs,
                # Ile razy serwer odmowil z powodu limitu i ile wyszukiwan
                # poszlo w sumie - zeby bylo widac, jak blisko 500/dzien jestesmy.
                "rate_limited": stats.rate_limited,
                "search_calls": stats.search_calls,
                "failures": stats.failures,
            }
        )
        writer.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zbiera snapshot rynku pracy IT.")
    parser.add_argument("--window", default=DEFAULT_WINDOW, help="okno czasowe: 24h/3d/7d/14d/30d")
    parser.add_argument("--seniorities", default=",".join(SENIORITIES))
    parser.add_argument("--groups", default="", help="ogranicz do grup technologii, np. frontend,backend")
    parser.add_argument("--interval", type=float, default=1.2, help="min. odstep miedzy zapytaniami [s]")
    parser.add_argument(
        "--budget-minutes",
        type=float,
        default=DEFAULT_BUDGET_MINUTES,
        help="twardy limit czasu zbierania; po nim koniec z tym, co juz jest",
    )
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaz, co byloby odpytane")
    args = parser.parse_args(argv)

    seniorities = [s.strip() for s in args.seniorities.split(",") if s.strip()]
    technologies = TECHNOLOGIES
    if args.groups:
        wanted = {g.strip() for g in args.groups.split(",") if g.strip()}
        technologies = {k: v for k, v in TECHNOLOGIES.items() if k in wanted}
        if not technologies:
            parser.error(f"nieznane grupy: {sorted(wanted)}; dostepne: {sorted(TECHNOLOGIES)}")

    matrix = build_matrix(window=args.window, seniorities=seniorities, technologies=technologies)

    if args.dry_run:
        fast = min(len(matrix), BURST_SEARCHES)
        estimate = fast * args.interval + (len(matrix) - fast) * SLOW_INTERVAL
        print(
            f"{len(matrix)} zapytan, szacowany czas: {estimate / 60:.0f} min "
            f"({fast} od razu, reszta co {SLOW_INTERVAL:.0f} s przez limit serwera)"
        )
        for query in matrix[:10]:
            print(f"  {query.kind:<10} {query.label:<18} {query.seniority or '-'}")
        print("  ...")
        return 0

    # GitHub konczy przekroczony krok sygnalem, nie wyjatkiem. Zamieniamy go
    # na normalne wyjscie, zeby zadzialal blok finally w run() i manifest
    # zdazyl sie zapisac - bez manifestu analiza nie ruszy tego snapshotu.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    stats = run(matrix, window=args.window, interval=args.interval, budget_minutes=args.budget_minutes)
    print(
        f"\nGotowe: {stats.queries_ok} ok, {stats.queries_failed} bledow, "
        f"{stats.unique_jobs} unikalnych ofert."
    )
    return 1 if stats.queries_failed > len(matrix) // 4 else 0


if __name__ == "__main__":
    raise SystemExit(main())
