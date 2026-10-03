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
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .mcp_client import EldoradoClient
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


# Ile nieudanych zapytan pod rzad uznajemy za awarie serwera, a nie pech.
FAILURE_STREAK = 5
# Przerwa po takiej serii. 28.09 serwer wrocil po kilku minutach - dalsze
# odpytywanie co 1,2 s tylko go dobijalo i palilo kolejne komorki macierzy.
COOLDOWN_SECONDS = 90
# Ile takich przerw w jednym przebiegu, zanim uznamy, ze dzis sie nie da.
# Ogranicza tez czas: workflow ma limit 30 minut.
MAX_COOLDOWNS = 3
# Przerwa przed druga runda dla zapytan, ktore padly w pierwszej.
RETRY_PASS_DELAY = 60


class ServerUnavailable(RuntimeError):
    """Serwer nie wrocil mimo kolejnych przerw - konczymy z tym, co jest."""


class _Pass:
    """Jedna runda po liscie zapytan z bezpiecznikiem na serie bledow."""

    def __init__(self, client: EldoradoClient, writer: SnapshotWriter, stats: RunStats, verbose: bool) -> None:
        self.client = client
        self.writer = writer
        self.stats = stats
        self.verbose = verbose
        self.streak = 0
        self.cooldowns = 0

    def query(self, label: str, query: Query) -> str | None:
        """Wykonuje zapytanie. Zwraca opis bledu albo None przy sukcesie."""
        try:
            payload = self.client.search_jobs(**query.arguments)
        except Exception as exc:  # noqa: BLE001 - jedna zla komorka nie psuje przebiegu
            if self.verbose:
                print(f"  {label} BLAD {query.label}: {exc}", file=sys.stderr)
            self.streak += 1
            if self.streak >= FAILURE_STREAK:
                self._cool_down()
            return f"{query.kind}/{query.label}/{query.seniority}: {exc}"

        self.streak = 0
        self.writer.write_measurement(query, payload)
        seen, _ = self.writer.write_jobs(payload.get("jobs", []))
        self.stats.queries_ok += 1
        self.stats.jobs_seen += seen
        if self.verbose:
            total = payload.get("total_results", 0)
            print(f"  {label} {query.label:<18} {query.seniority or '-':<8} {total:>6}")
        return None

    def _cool_down(self) -> None:
        if self.cooldowns >= MAX_COOLDOWNS:
            raise ServerUnavailable(
                f"{self.streak} bledow pod rzad mimo {MAX_COOLDOWNS} przerw po {COOLDOWN_SECONDS} s"
            )
        self.cooldowns += 1
        if self.verbose:
            print(
                f"  -- {self.streak} bledow pod rzad: przerwa {COOLDOWN_SECONDS} s "
                f"({self.cooldowns}/{MAX_COOLDOWNS}) i nowa sesja --",
                file=sys.stderr,
            )
        time.sleep(COOLDOWN_SECONDS)
        self.streak = 0
        try:
            self.client.connect()
        except Exception as exc:  # noqa: BLE001 - nowa sesja to proba, nie warunek
            if self.verbose:
                print(f"  -- nowa sesja nie wstala: {exc}", file=sys.stderr)


def run(
    matrix: list[Query],
    *,
    data_dir: Path = DATA_DIR,
    window: str = DEFAULT_WINDOW,
    interval: float = 1.2,
    verbose: bool = True,
) -> RunStats:
    """Przebieg zbierania odporny na przejsciowe awarie serwera.

    1. Pierwsza runda po calej macierzy. Seria bledow pod rzad uruchamia
       przerwe i nowa sesje, zamiast odpytywac martwy serwer dalej.
    2. Druga runda tylko dla zapytan, ktore padly - po minucie przerwy.
       Przejsciowa awaria w polowie przebiegu nie kosztuje wtedy polowy danych.
    3. Jesli serwer nie wraca mimo przerw, konczymy z tym, co juz jest
       zapisane. Kazda zebrana komorka jest nieodtwarzalna, wiec niczego
       nie wyrzucamy tylko dlatego, ze reszta sie nie udala.
    """
    stats = RunStats()
    writer = SnapshotWriter(data_dir, date.today())
    started = datetime.now(timezone.utc)
    failed: list[tuple[Query, str]] = []
    aborted: str | None = None

    try:
        with EldoradoClient(min_interval=interval) as client:
            if verbose:
                name = client.server_info.get("name", "?")
                print(f"Polaczono z MCP: {name} | zapytan do wykonania: {len(matrix)}")

            first = _Pass(client, writer, stats, verbose)
            position = 0
            try:
                for position, query in enumerate(matrix):
                    if error := first.query(f"[{position + 1}/{len(matrix)}]", query):
                        failed.append((query, error))
            except ServerUnavailable as exc:
                aborted = str(exc)
                # Zapytanie, na ktorym bezpiecznik zadzialal, i wszystkie dalsze
                # tez sa nieudane - licza sie do bledow, zeby manifest nie udawal
                # pelnego przebiegu.
                failed.extend((q, "pominiete: serwer niedostepny") for q in matrix[position:])

            if failed and not aborted:
                if verbose:
                    print(f"\nDruga runda: {len(failed)} zapytan po {RETRY_PASS_DELAY} s przerwy")
                time.sleep(RETRY_PASS_DELAY)
                second = _Pass(client, writer, stats, verbose)
                still_failed: list[tuple[Query, str]] = []
                position = 0
                try:
                    for position, (query, _) in enumerate(failed):
                        if error := second.query(f"[druga {position + 1}/{len(failed)}]", query):
                            still_failed.append((query, error))
                except ServerUnavailable as exc:
                    aborted = str(exc)
                    still_failed.extend(failed[position:])
                failed = still_failed
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
        print(f"{len(matrix)} zapytan, szacowany czas: {len(matrix) * args.interval / 60:.1f} min")
        for query in matrix[:10]:
            print(f"  {query.kind:<10} {query.label:<18} {query.seniority or '-'}")
        print("  ...")
        return 0

    stats = run(matrix, window=args.window, interval=args.interval)
    print(
        f"\nGotowe: {stats.queries_ok} ok, {stats.queries_failed} bledow, "
        f"{stats.unique_jobs} unikalnych ofert."
    )
    return 1 if stats.queries_failed > len(matrix) // 4 else 0


if __name__ == "__main__":
    raise SystemExit(main())
