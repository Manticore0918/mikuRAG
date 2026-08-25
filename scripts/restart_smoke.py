import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BASE_URL = "http://localhost:5173/api/v1"


def _run(arguments: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def _psql(
    postgres_user: str,
    postgres_database: str,
    statement: str,
) -> str:
    return _run(
        [
            "docker",
            "compose",
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
        ],
        capture=True,
    )


def _wait_for_ready(client: httpx.Client, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = client.get("/health/ready", timeout=5)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Backend did not become Ready after restart")


def _require(response: httpx.Response) -> httpx.Response:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = response.text[:500]
        raise RuntimeError(
            f"{response.request.method} {response.request.url} failed: "
            f"{response.status_code} {detail}"
        ) from error
    return response


def _wait_for_document_status(
    client: httpx.Client,
    knowledge_base_id: str,
    document_id: str,
    terminal_status: str,
    *,
    timeout_seconds: int = 180,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, object] | None = None
    while time.monotonic() < deadline:
        documents = _require(
            client.get(f"/admin/knowledge-bases/{knowledge_base_id}/documents")
        ).json()
        latest = next((row for row in documents if row["id"] == document_id), None)
        if latest is not None and latest["status"] == terminal_status:
            return latest
        time.sleep(2)
    status = latest["status"] if latest is not None else "missing"
    raise RuntimeError(
        f"Document {document_id} did not become {terminal_status}; status={status}"
    )


def main() -> None:
    if not (ROOT / ".env").exists():
        raise SystemExit("Copy .env.example to .env and replace its placeholders first")

    config = json.loads(
        _run(
            ["docker", "compose", "--profile", "tools", "config", "--format", "json"],
            capture=True,
        )
    )
    backend_environment = config["services"]["backend"]["environment"]
    postgres_environment = config["services"]["postgres"]["environment"]
    postgres_user = postgres_environment["POSTGRES_USER"]
    postgres_database = postgres_environment["POSTGRES_DB"]

    # Load the exact Compose settings without printing their values. This lets
    # the runner sign a short-lived cookie for an existing local Administrator
    # without changing that account's password.
    for key, value in backend_environment.items():
        if value is not None:
            os.environ[key] = str(value)
    sys.path.insert(0, str(BACKEND))
    from app.security import (  # noqa: PLC0415
        CSRF_COOKIE,
        SESSION_COOKIE,
        create_session_token,
        new_csrf_token,
    )

    administrator = _psql(
        postgres_user,
        postgres_database,
        "SELECT id::text || '|' || session_version::text "
        "FROM users WHERE is_administrator IS TRUE AND is_enabled IS TRUE "
        "ORDER BY created_at LIMIT 1",
    )
    if not administrator:
        raise SystemExit("No enabled Administrator exists; run the demo seed first")
    administrator_id, session_version = administrator.split("|", maxsplit=1)

    knowledge_base = _psql(
        postgres_user,
        postgres_database,
        "SELECT id::text FROM knowledge_bases "
        "WHERE name = 'mikuRAG Baseline Restricted' LIMIT 1",
    )
    if not knowledge_base:
        raise SystemExit("The restricted baseline Knowledge Base is missing")

    run_id = uuid.uuid4().hex[:10]
    content = (
        f"Checkpoint restart smoke {run_id}.\n"
        "The durable upload checkpoint must survive a backend restart.\n"
        "The queued ingestion task must survive a worker restart.\n"
    ).encode()
    checksum = hashlib.sha256(content).hexdigest()
    csrf_token = new_csrf_token()
    headers = {"X-CSRF-Token": csrf_token}
    upload_id: str | None = None
    document_id: str | None = None
    failure_upload_id: str | None = None
    failure_document_id: str | None = None
    worker_stopped = False

    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        client.cookies.set(
            SESSION_COOKIE,
            create_session_token(uuid.UUID(administrator_id), int(session_version)),
        )
        client.cookies.set(CSRF_COOKIE, csrf_token)
        current_user = _require(client.get("/auth/me")).json()
        if not current_user.get("is_administrator"):
            raise RuntimeError("Restart smoke cookie did not authenticate an Administrator")

        try:
            upload = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads",
                    headers=headers,
                    json={
                        "original_name": f"checkpoint-restart-{run_id}.txt",
                        "size_bytes": len(content),
                        "sha256": checksum,
                    },
                )
            ).json()
            upload_id = upload["id"]

            _run(["docker", "compose", "restart", "backend"])
            _wait_for_ready(client)
            resumed = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                    f"{upload_id}/resume",
                    headers=headers,
                )
            ).json()
            if resumed["received_bytes"] != 0 or resumed["status"] != "open":
                raise RuntimeError("Open upload checkpoint changed across backend restart")

            part = _require(
                client.put(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                    f"{upload_id}/parts",
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "X-Upload-Offset": "0",
                        "X-Upload-Length": str(len(content)),
                        "X-Upload-SHA256": checksum,
                    },
                    content=content,
                )
            ).json()
            if part["next_offset"] != len(content):
                raise RuntimeError("Upload Part checkpoint did not advance to the file size")

            _run(["docker", "compose", "stop", "worker"])
            worker_stopped = True
            document = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                    f"{upload_id}/complete",
                    headers=headers,
                )
            ).json()
            document_id = document["id"]
            if document["status"] != "pending":
                raise RuntimeError(
                    f"Completed upload was expected to be pending, got {document['status']}"
                )

            _run(["docker", "compose", "start", "worker"])
            _run(["docker", "compose", "restart", "worker"])
            worker_stopped = False

            recovered = _wait_for_document_status(
                client,
                knowledge_base,
                document_id,
                "ready",
            )

            malformed = b"def broken(:\n    pass\n"
            malformed_checksum = hashlib.sha256(malformed).hexdigest()
            failed_upload = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads",
                    headers=headers,
                    json={
                        "original_name": f"checkpoint-malformed-{run_id}.py",
                        "size_bytes": len(malformed),
                        "sha256": malformed_checksum,
                        "source_kind": "code",
                        "language": "python",
                        "source_path": f"smoke/checkpoint-malformed-{run_id}.py",
                        "tags": ["checkpoint-1-smoke"],
                    },
                )
            ).json()
            failure_upload_id = failed_upload["id"]
            _require(
                client.put(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                    f"{failure_upload_id}/parts",
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "X-Upload-Offset": "0",
                        "X-Upload-Length": str(len(malformed)),
                        "X-Upload-SHA256": malformed_checksum,
                    },
                    content=malformed,
                )
            )
            failed_document = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                    f"{failure_upload_id}/complete",
                    headers=headers,
                )
            ).json()
            failure_document_id = failed_document["id"]
            first_failure = _wait_for_document_status(
                client,
                knowledge_base,
                failure_document_id,
                "failed",
            )
            if first_failure.get("ingestion_stage") != "failed":
                raise RuntimeError("Malformed parser result did not expose failed stage")
            if not str(first_failure.get("safe_error") or "").startswith("extract:"):
                raise RuntimeError("Malformed parser result did not expose a safe extract error")
            first_attempts = int(first_failure.get("ingestion_attempts") or 0)
            if first_attempts < 1:
                raise RuntimeError("Malformed parser result did not record an attempt")
            chunk_count = int(
                _psql(
                    postgres_user,
                    postgres_database,
                    "SELECT count(*) FROM chunks "
                    f"WHERE document_id = '{failure_document_id}'",
                )
            )
            if chunk_count != 0:
                raise RuntimeError("Malformed source exposed partial retrieval chunks")
            retried = _require(
                client.post(
                    f"/admin/knowledge-bases/{knowledge_base}/documents/"
                    f"{failure_document_id}/retry",
                    headers=headers,
                )
            ).json()
            if retried["status"] != "pending" or retried["ingestion_stage"] != "queued":
                raise RuntimeError("Parser retry did not return to the durable queue state")
            second_failure = _wait_for_document_status(
                client,
                knowledge_base,
                failure_document_id,
                "failed",
            )
            if int(second_failure.get("ingestion_attempts") or 0) <= first_attempts:
                raise RuntimeError("Parser retry did not increment the attempt count")

            print(
                json.dumps(
                    {
                        "backend_restart_open_upload": "pass",
                        "ingestion_after_worker_restart": "pass",
                        "document_status": recovered["status"],
                        "failed_parser_consistency": "pass",
                        "failed_parser_retry": "pass",
                        "partial_chunks_after_failure": chunk_count,
                    },
                    indent=2,
                )
            )
        finally:
            if worker_stopped:
                subprocess.run(
                    ["docker", "compose", "start", "worker"],
                    cwd=ROOT,
                    check=False,
                )
            if document_id is not None:
                try:
                    client.delete(
                        f"/admin/knowledge-bases/{knowledge_base}/documents/{document_id}",
                        headers=headers,
                    )
                except httpx.HTTPError:
                    pass
            elif upload_id is not None:
                try:
                    client.delete(
                        f"/admin/knowledge-bases/{knowledge_base}/document-uploads/"
                        f"{upload_id}",
                        headers=headers,
                    )
                except httpx.HTTPError:
                    pass
            if failure_document_id is not None:
                try:
                    client.delete(
                        f"/admin/knowledge-bases/{knowledge_base}/documents/"
                        f"{failure_document_id}",
                        headers=headers,
                    )
                except httpx.HTTPError:
                    pass
            if upload_id is not None:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "-U",
                        postgres_user,
                        "-d",
                        postgres_database,
                        "-c",
                        f"DELETE FROM upload_sessions WHERE id = '{upload_id}'",
                    ],
                    cwd=ROOT,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            if failure_upload_id is not None:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "-U",
                        postgres_user,
                        "-d",
                        postgres_database,
                        "-c",
                        "DELETE FROM upload_sessions "
                        f"WHERE id = '{failure_upload_id}'",
                    ],
                    cwd=ROOT,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )


if __name__ == "__main__":
    main()
