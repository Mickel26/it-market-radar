"""Lokalny interfejs matchera: jedna komenda zamiast piatki.

    python -m radar.server

Otwiera http://127.0.0.1:8765 - profil, zbieranie ofert i wyniki dopasowania
klika sie w przegladarce.

DLACZEGO TO NIE JEST CZESC DASHBOARDU NA GITHUB PAGES
-----------------------------------------------------
Dashboard jest publiczny i pokazuje wylacznie liczby o rynku. Ten interfejs
dotyka Twojego CV, profilu i surowego korpusu ofert - rzeczy, ktore z zalozenia
nie opuszczaja dysku. Dlatego to osobny program, ktory:

* slucha TYLKO na 127.0.0.1, wiec nikt z sieci sie nie dobije,
* nie wysyla niczego na zewnatrz poza samym zbieraniem ofert z agregatora,
* trzyma pliki w profile/, ktore jest w .gitignore.

Strony z radar/ui/ leza poza katalogiem publikowanym jako dashboard i bez tego
serwera sa martwe - same z siebie nie maja skad wziac danych.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import threading
import traceback
import webbrowser
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import analyze, collect, match
from .profile import (
    DEFAULT_PROFILE,
    PROFILE_DIR,
    SKILL_VOCABULARY,
    Profile,
    normalize,
    skills_from_linkedin,
    skills_from_text,
)
from .queries import CONTRACT_TYPES, SENIORITIES, WORK_MODES

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent / "ui"
ASSETS_DIR = ROOT / "assets"

MAX_UPLOAD = 25 * 1024 * 1024  # eksport z LinkedIna potrafi wazyc kilkanascie MB


class CollectJob:
    """Zbieranie chodzi w tle, bo trwa ~4 minuty i blokowaloby przegladarke."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | done | error
        self.done = 0
        self.total = 0
        self.message = ""

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self.state, "done": self.done, "total": self.total, "message": self.message}

    def start(self) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.state = "running"
            self.done = 0
            self.total = 0
            self.message = ""
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            matrix = collect.build_matrix()
            with self.lock:
                self.total = len(matrix)

            # collect.run wypisuje postep na stdout; przechwytujemy go, zeby
            # przegladarka mogla pokazac pasek zamiast czterech minut ciszy.
            sink = _ProgressSink(self)
            with redirect_stdout(sink):
                stats = collect.run(matrix, verbose=True)

            analyze_snapshot = analyze.Snapshot.latest(analyze.DATA_DIR)
            report = analyze.build_report(analyze_snapshot)
            out = analyze.DATA_DIR / "reports" / f"{analyze_snapshot.day.isoformat()}.json"
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            analyze.write_reports_index(out.parent)
            analyze.write_trends(out.parent)

            with self.lock:
                self.state = "done"
                self.message = (
                    f"{stats.queries_ok} zapytań OK, {stats.queries_failed} błędów, "
                    f"{stats.unique_jobs} unikalnych ofert."
                )
        except Exception as exc:  # noqa: BLE001 - komunikat ma trafic do przegladarki
            with self.lock:
                self.state = "error"
                self.message = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()


class _ProgressSink(io.StringIO):
    """Liczy linie postepu z collect.run, zeby dalo sie pokazac pasek."""

    def __init__(self, job: CollectJob) -> None:
        super().__init__()
        self.job = job

    def write(self, text: str) -> int:
        if "[" in text and "/" in text:
            try:
                fragment = text.split("[", 1)[1].split("]", 1)[0]
                done, total = fragment.split("/")
                with self.job.lock:
                    self.job.done = int(done)
                    self.job.total = int(total)
            except (ValueError, IndexError):
                pass
        return super().write(text)


COLLECT = CollectJob()


def corpus_state() -> dict[str, Any]:
    try:
        snapshot = analyze.Snapshot.latest(analyze.DATA_DIR)
    except FileNotFoundError:
        return {"exists": False, "day": None, "jobs": 0}
    return {"exists": bool(snapshot.jobs), "day": snapshot.day.isoformat(), "jobs": len(snapshot.jobs)}


def load_profile_dict() -> dict[str, Any] | None:
    if not DEFAULT_PROFILE.exists():
        return None
    try:
        return json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_matches(limit: int, min_score: float) -> dict[str, Any]:
    profile = Profile.load(DEFAULT_PROFILE)
    snapshot = analyze.Snapshot.latest(analyze.DATA_DIR)
    found = match.find_matches(snapshot.jobs, profile, min_score=min_score)
    return {
        "corpus": len(snapshot.jobs),
        "day": snapshot.day.isoformat(),
        "total": len(found),
        "matches": [
            {
                "score": m.score,
                "coverage": m.coverage,
                "title": m.title,
                "company": m.company,
                "url": m.url,
                "seniority": m.seniority,
                "salary": m.salary,
                "matched": m.matched,
                "partial": m.partial,
                "missing": m.missing,
            }
            for m in found[:limit]
        ],
        "gaps": [{"skill": s, "count": c} for s, c in match.gap_ranking(found)[:12]],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "it-market-radar"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Domyslny log sypie linia na kazdy request, lacznie z odpytywaniem
        # postepu co sekunde. Zostawiamy tylko bledy.
        if not str(args[1] if len(args) > 1 else "").startswith(("2", "3")):
            super().log_message(fmt, *args)

    # --- pomocnicze ---

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: Path, content_type: str) -> None:
        try:
            self._send(200, path.read_bytes(), content_type)
        except OSError:
            self._json({"error": f"brak pliku: {path.name}"}, 404)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError(f"plik wiekszy niz {MAX_UPLOAD // 1024 // 1024} MB")
        return json.loads(self.rfile.read(length) or b"{}")

    # --- trasy ---

    def do_GET(self) -> None:  # noqa: N802 - nazwa wymagana przez BaseHTTPRequestHandler
        route = self.path.split("?", 1)[0]
        try:
            if route in ("/", "/index.html"):
                return self._file(UI_DIR / "index.html", "text/html; charset=utf-8")
            if route == "/ui/app.js":
                return self._file(UI_DIR / "app.js", "application/javascript; charset=utf-8")
            if route == "/assets/dashboard.css":
                return self._file(ASSETS_DIR / "dashboard.css", "text/css; charset=utf-8")

            if route == "/api/state":
                return self._json(
                    {
                        "profile": load_profile_dict(),
                        "corpus": corpus_state(),
                        "vocabulary": SKILL_VOCABULARY,
                        "seniorities": SENIORITIES,
                        "work_modes": WORK_MODES,
                        "contracts": CONTRACT_TYPES,
                        "collect": COLLECT.snapshot(),
                    }
                )
            if route == "/api/collect":
                return self._json(COLLECT.snapshot())
            if route == "/api/matches":
                params = dict(
                    pair.split("=", 1) for pair in self.path.partition("?")[2].split("&") if "=" in pair
                )
                return self._json(
                    build_matches(int(params.get("limit", 40)), float(params.get("min_score", 0)))
                )
        except FileNotFoundError as exc:
            return self._json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        self._json({"error": "nie ma takiej trasy"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/profile":
                data = self._body()
                profile = Profile(**data)
                profile.validate()
                profile.save(DEFAULT_PROFILE)
                return self._json({"ok": True, "path": str(DEFAULT_PROFILE)})

            if self.path == "/api/extract":
                data = self._body()
                kind = data.get("kind")
                if kind == "cv":
                    skills = skills_from_text(data.get("text") or "")
                elif kind == "linkedin":
                    raw = base64.b64decode(data.get("b64") or "")
                    tmp = PROFILE_DIR / ".linkedin-upload.zip"
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_bytes(raw)
                    try:
                        skills = skills_from_linkedin(tmp)
                    finally:
                        tmp.unlink(missing_ok=True)
                else:
                    return self._json({"error": "nieznany rodzaj pliku"}, 400)

                seen: dict[str, str] = {}
                for skill in skills:
                    seen.setdefault(normalize(skill), skill)
                return self._json({"skills": sorted(seen.values(), key=str.lower)})

            if self.path == "/api/collect":
                started = COLLECT.start()
                return self._json({"started": started, **COLLECT.snapshot()})
        except SystemExit as exc:
            # skills_from_linkedin sygnalizuje zly plik przez SystemExit
            return self._json({"error": str(exc)}, 400)
        except (TypeError, ValueError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        self._json({"error": "nie ma takiej trasy"}, 404)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lokalny interfejs matchera ofert.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="nie otwieraj przegladarki")
    args = parser.parse_args(argv)

    # Wylacznie petla zwrotna. Zwiazanie z 0.0.0.0 wystawiloby czyjes CV
    # i korpus ofert na cala siec lokalna.
    address = ("127.0.0.1", args.port)
    httpd = ThreadingHTTPServer(address, Handler)
    url = f"http://127.0.0.1:{args.port}"

    print(f"Matcher ofert: {url}")
    print("Dziala lokalnie - profil i korpus nie opuszczaja tego komputera.")
    print("Zatrzymanie: Ctrl+C")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymano.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
