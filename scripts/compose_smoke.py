"""End-to-end Compose smoke test with stubbed model providers (CI-oriented).

Runs the real stack — PostgreSQL/pg_search, Redis, API, worker, beat, and the
frontend proxy — with the deterministic provider stub (``profile: smoke``),
then:

1. migrates a fresh database,
2. seeds the demo Knowledge Bases and waits for Ready Documents,
3. uploads a Markdown Document through the resumable-upload API,
4. asks a question and asserts a grounded, cited answer,
5. runs a two-case evaluation subset and validates the report schema,
6. optionally brings up the observability profile (MIKURAG_SMOKE_OTEL=1).

The stack runs under the isolated ``mikurag-smoke`` Compose project, so the
script never touches a development installation and always tears its own
volumes down. Set ``MIKURAG_SMOKE_FRONTEND_PORT`` when port 5173 is unavailable.
"""

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from evaluation_report_validation import validate_evaluation_report

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND_PORT = os.environ.get("MIKURAG_SMOKE_FRONTEND_PORT", "5173")
BASE_URL = f"http://localhost:{FRONTEND_PORT}/api/v1"
PROJECT = "mikurag-smoke"

STUB_ENVIRONMENT = {
    "MIKURAG_ENVIRONMENT": "test",
    "MIKURAG_EMBEDDING_ENDPOINT": "http://provider-stub:8900/embeddings",
    "MIKURAG_EMBEDDING_API_KEY": "stub-key",
    "MIKURAG_GENERATION_BASE_URL": "http://provider-stub:8900/v1",
    "MIKURAG_DEMO_ADMIN_PASSWORD": "smoke-admin-password-01",
    "MIKURAG_DEMO_USER_PASSWORD": "smoke-demo-password-01",
}

# Hermetic infrastructure credentials for the isolated smoke project. These
# are forced (not defaulted) so a developer's real `.env` can never make the
# API/worker credentials disagree with the freshly initialized database.
SMOKE_DATABASE_PASSWORD = "mikurag-smoke"
SMOKE_DATABASE_URL = (
    f"postgresql+asyncpg://mikurag:{SMOKE_DATABASE_PASSWORD}@postgres:5432/mikurag"
)
SMOKE_REDIS_URL = "redis://redis:6379/0"

# Session-cookie signing runs inside the backend container so the smoke driver
# itself needs no backend dependencies on the host (CI installs only httpx).
_SIGN_SESSION_SNIPPET = (
    "import json, sys, uuid; "
    "from app.security import CSRF_COOKIE, SESSION_COOKIE, create_session_token, "
    "new_csrf_token; "
    "user_id, session_version = sys.argv[1], sys.argv[2]; "
    "print(json.dumps({"
    "'session_cookie': SESSION_COOKIE, 'csrf_cookie': CSRF_COOKIE, "
    "'session_token': create_session_token(uuid.UUID(user_id), int(session_version)), "
    "'csrf_token': new_csrf_token()}))"
)

SMOKE_DOCUMENT = (
    "# Compose Smoke Fixture\n\n"
    "The Melody Harbor smoke deployment window is Tuesday from 09:00 to 11:00 "
    "Singapore time. The release manager must approve the rollout first.\n"
)
SMOKE_QUESTION = "When is the Melody Harbor smoke deployment window?"


def _run(arguments: list[str], *, capture: bool = True, check: bool = True) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    if check and result.returncode != 0:
        # Surface the captured compose/docker output: without it a failed step
        # (for example an unhealthy service during `compose up`) is
        # undiagnosable from the log alone.
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(arguments)}\n"
            f"{output[-4_000:]}"
        )
    return result.stdout if capture else ""


def _compose_command(*arguments: str) -> list[str]:
    command = ["docker", "compose", "-f", str(ROOT / "compose.yaml")]
    if os.environ.get("MIKURAG_SMOKE_OTEL") == "1":
        command.extend(["-f", str(ROOT / "compose.observability.yaml")])
    command.extend(["-p", PROJECT, *arguments])
    return command


def _compose(*arguments: str, capture: bool = True, check: bool = True) -> str:
    return _run(
        _compose_command(*arguments),
        capture=capture,
        check=check,
    )


def _ensure_results_tree_writable() -> None:
    """The evaluate container runs as an unprivileged uid, so the bind-mounted
    results tree must exist and accept writes from any host uid."""
    results = BACKEND / "evaluation" / "results"
    results.mkdir(parents=True, exist_ok=True)
    try:
        results.chmod(0o777)
    except OSError:
        return  # Windows Docker Desktop mounts are already permissive
    for path in results.rglob("*"):
        try:
            path.chmod(0o777 if path.is_dir() else 0o666)
        except OSError:
            pass


def _psql(postgres_user: str, postgres_database: str, statement: str) -> str:
    return _compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        postgres_user,
        "-d",
        postgres_database,
        "-At",
        "-c",
        statement,
    ).strip()


def _wait_for_postgres(postgres_user: str, postgres_database: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        probe = subprocess.run(
            _compose_command(
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                postgres_user,
                "-d",
                postgres_database,
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("PostgreSQL did not become ready within 120 seconds")


def _wait_for_ready(client: httpx.Client, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get("/health/ready", timeout=5)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last_error = f"status={response.status_code} body={response.text[:200]}"
        except (httpx.HTTPError, ValueError) as error:
            last_error = str(error)
        time.sleep(2)
    raise RuntimeError(f"Backend did not become Ready: {last_error}")


def _require(response: httpx.Response) -> httpx.Response:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} failed: "
            f"{response.status_code} {response.text[:300]}"
        ) from error
    return response


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        payload = json.loads("\n".join(data_lines)) if data_lines else {}
        events.append((event_name, payload))
    return events


def _teardown() -> None:
    profiles = ["--profile", "smoke", "--profile", "tools"]
    if os.environ.get("MIKURAG_SMOKE_OTEL") == "1":
        profiles.extend(["--profile", "observability"])
    subprocess.run(
        _compose_command(
            *profiles,
            "down",
            "-v",
            "--remove-orphans",
        ),
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _prom_instant(query: str) -> list[dict]:
    response = httpx.get(
        "http://localhost:9090/api/v1/query",
        params={"query": query},
        timeout=5,
    )
    response.raise_for_status()
    return response.json().get("data", {}).get("result", [])


def _prom_scalar(query: str) -> float | None:
    results = _prom_instant(query)
    if not results:
        return None
    return float(results[0]["value"][1])


def _verify_observability() -> None:
    """Assert the full pipeline works: app metrics and spans reach the
    collector and Prometheus scrapes both the exporter and self-telemetry."""

    print("Verifying the observability pipeline ...")
    deadline = time.monotonic() + 120
    last = "no probe completed"
    while time.monotonic() < deadline:
        try:
            exporter_up = _prom_scalar('up{job="mikurag-otel-collector"}')
            self_up = _prom_scalar('up{job="mikurag-otel-collector-self"}')
            turn_total = _prom_scalar("sum(mikurag_rag_turns_total)")
            accepted_spans = _prom_scalar(
                'sum({__name__=~"otelcol_receiver_accepted_spans(_total)?"})'
            )
            if exporter_up != 1.0:
                last = "collector exporter target is not up yet"
            elif self_up != 1.0:
                last = "collector self-telemetry target is not up yet"
            elif turn_total is None or turn_total < 1:
                last = "no mikuRAG turn metric has reached Prometheus yet"
            elif accepted_spans is None or accepted_spans < 1:
                last = "collector has not accepted spans yet"
            else:
                print(
                    "  collector scraped, app metrics and spans received "
                    f"(mikurag_rag_turns_total={turn_total:g})"
                )
                return
        except (httpx.HTTPError, ValueError, KeyError) as error:
            last = str(error)
        time.sleep(5)
    raise RuntimeError(f"Observability pipeline verification failed: {last}")


def main() -> None:
    if not (ROOT / ".env").exists():
        raise SystemExit("A .env with SESSION_SECRET / ENCRYPTION_MASTER_KEY is required")

    # Force the deterministic stub providers for every service in the project.
    os.environ.update(STUB_ENVIRONMENT)
    os.environ["POSTGRES_PASSWORD"] = SMOKE_DATABASE_PASSWORD
    os.environ["MIKURAG_DATABASE_URL"] = SMOKE_DATABASE_URL
    os.environ["MIKURAG_REDIS_URL"] = SMOKE_REDIS_URL
    os.environ["MIKURAG_FRONTEND_PORT"] = FRONTEND_PORT
    otel_enabled = os.environ.get("MIKURAG_SMOKE_OTEL") == "1"
    if otel_enabled:
        # The observability override deliberately requires explicit Grafana
        # credentials; the isolated smoke project uses throwaway values.
        os.environ.setdefault("GRAFANA_ADMIN_USER", "smoke-admin")
        os.environ.setdefault("GRAFANA_ADMIN_PASSWORD", "smoke-grafana-01")
        os.environ.setdefault("MIKURAG_OTEL_ENABLED", "true")
        os.environ.setdefault("MIKURAG_OTEL_METRIC_EXPORT_INTERVAL_MS", "15000")

    config = json.loads(
        _compose("--profile", "smoke", "--profile", "tools", "config", "--format", "json")
    )
    postgres_environment = config["services"]["postgres"]["environment"]
    postgres_user = postgres_environment["POSTGRES_USER"]
    postgres_database = postgres_environment["POSTGRES_DB"]

    try:
        print("Building images ...")
        _compose("--profile", "tools", "build", "migrate", "backend", "worker", "beat")

        print("Starting infrastructure ...")
        services = ["postgres", "redis", "provider-stub", "backend", "worker", "beat", "frontend"]
        profiles = ["--profile", "smoke"]
        if otel_enabled:
            services += ["otel-collector", "tempo", "prometheus"]
            profiles += ["--profile", "observability"]
        _compose(*profiles, "up", "-d", *services)
        _wait_for_postgres(postgres_user, postgres_database)

        print("Migrating ...")
        _compose("--profile", "tools", "run", "--rm", "migrate")

        print("Waiting for the API ...")
        with httpx.Client(base_url=BASE_URL, timeout=30) as client:
            _wait_for_ready(client)

            print("Seeding demo data ...")
            seed_output = _compose(
                "run",
                "--rm",
                "-e",
                "MIKURAG_DEMO_ADMIN_PASSWORD",
                "-e",
                "MIKURAG_DEMO_USER_PASSWORD",
                "backend",
                "python",
                "-m",
                "app.demo_seed",
                "--wait",
            )
            seed_payload = json.loads(seed_output[seed_output.index("{") :])
            knowledge_base_id = seed_payload["public_knowledge_base_id"]

            # Sign in as the seeded Administrator the same way restart_smoke
            # does: a short-lived cookie signed with the deployed secret.
            administrator = _psql(
                postgres_user,
                postgres_database,
                "SELECT id::text || '|' || session_version::text "
                "FROM users WHERE is_administrator IS TRUE AND is_enabled IS TRUE "
                "ORDER BY created_at LIMIT 1",
            )
            if not administrator:
                raise RuntimeError("The demo seed did not create an Administrator")
            administrator_id, session_version = administrator.split("|", maxsplit=1)

            signed_output = _compose(
                "run",
                "--rm",
                "backend",
                "python",
                "-c",
                _SIGN_SESSION_SNIPPET,
                administrator_id,
                session_version,
            )
            signed = json.loads(signed_output[signed_output.index("{") :])
            client.cookies.set(signed["session_cookie"], signed["session_token"])
            client.cookies.set(signed["csrf_cookie"], signed["csrf_token"])
            headers = {"X-CSRF-Token": signed["csrf_token"]}

            print("Uploading a Document through the resumable API ...")
            content = SMOKE_DOCUMENT.encode("utf-8")
            upload = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base_id}/document-uploads",
                    headers=headers,
                    json={
                        "original_name": f"compose-smoke-{uuid.uuid4().hex[:8]}.md",
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    },
                )
            ).json()
            _require(
                client.put(
                    f"/admin/knowledge-bases/{knowledge_base_id}/document-uploads/"
                    f"{upload['id']}/parts",
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "X-Upload-Offset": "0",
                        "X-Upload-Length": str(len(content)),
                        "X-Upload-SHA256": hashlib.sha256(content).hexdigest(),
                    },
                    content=content,
                )
            )
            document = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base_id}/document-uploads/"
                    f"{upload['id']}/complete",
                    headers=headers,
                )
            ).json()

            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                documents = _require(
                    client.get(f"/admin/knowledge-bases/{knowledge_base_id}/documents")
                ).json()
                row = next((item for item in documents if item["id"] == document["id"]), None)
                if row is not None and row["status"] == "ready":
                    break
                time.sleep(2)
            else:
                status = row["status"] if row else "missing"
                raise RuntimeError(f"Smoke Document did not become Ready; status={status}")

            print("Asking a question ...")
            conversation = _require(
                client.post(
                    "/conversations",
                    headers=headers,
                    json={"knowledge_base_id": knowledge_base_id},
                )
            ).json()
            with client.stream(
                "POST",
                f"/conversations/{conversation['id']}/turns",
                headers=headers,
                json={"question": SMOKE_QUESTION},
                timeout=120,
            ) as response:
                _require(response)
                request_id = response.headers.get("X-Request-ID")
                body = "".join(response.iter_text())
            events = _parse_sse(body)
            outcomes = {
                payload.get("outcome") for name, payload in events if name == "done"
            }
            citation_events = [payload for name, payload in events if name == "citations"]
            citations = citation_events[0].get("items", []) if citation_events else []
            if outcomes != {"grounded_answer"}:
                raise RuntimeError(
                    f"Expected a grounded answer, got outcomes={outcomes}; events={events[:6]}"
                )
            if not citations:
                raise RuntimeError("The grounded answer carried no Citations")
            if not request_id:
                raise RuntimeError("The response did not echo an X-Request-ID")
            if "citations" not in body:
                raise RuntimeError("SSE stream did not include the citations event")
            print(f"  request_id={request_id} citations={len(citations)}")

            print("Running the two-case evaluation subset ...")
            _ensure_results_tree_writable()
            _compose(
                "--profile",
                "tools",
                "run",
                "--rm",
                "evaluate",
                "python",
                "-m",
                "app.evaluation_cli",
                "run",
                "--max-cases",
                "2",
            )
            results_dir = BACKEND / "evaluation" / "results"
            reports = sorted(results_dir.rglob("report.json"), key=os.path.getmtime)
            if not reports:
                raise RuntimeError("The evaluation subset produced no report.json")
            with reports[-1].open(encoding="utf-8") as handle:
                report = json.load(handle)
            validate_evaluation_report(report, expected_case_count=2)
            print(
                f"  report schema_version={report['schema_version']} "
                f"cases={report['case_count']}"
            )

            if otel_enabled:
                _verify_observability()

        print(
            json.dumps(
                {
                    "migration": "pass",
                    "demo_seed": "pass",
                    "resumable_upload": "pass",
                    "grounded_answer_with_citations": "pass",
                    "evaluation_subset_and_report_schema": "pass",
                    "observability_profile": (
                        "enabled" if os.environ.get("MIKURAG_SMOKE_OTEL") == "1" else "off"
                    ),
                },
                indent=2,
            )
        )
    finally:
        _teardown()


if __name__ == "__main__":
    main()
