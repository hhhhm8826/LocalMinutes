"""루프백 API. 모델은 별도 작업 프로세스에서만 로드한다."""
from contextlib import asynccontextmanager
import secrets
import time
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import __version__
from .logging import configure_log, event
from .security import COOKIE, digest, owner_session, local_session, ensure_local_session, has_owner, prepare_owner_key
from .settings import Settings
from .storage import make_engine, migrate
from .contracts import ConfirmMinutes, GenerateMinutes, MeetingCreate, TranscriptEdit
from .documents import EditDocument
from .codex_provider import CodexFailure
from .minutes_management import edit_minutes, finish_transcript_only, list_minutes, queue_generation, read_minutes
from .repository import Conflict, Missing, Repository
from .uploads import receive_upload
from .worker import worker_env
from .transcripts import current_transcript, edit_transcript, list_transcripts
from .library import audio_path, export_download, media_info
from .deletion import purge_meeting, request_delete
from .retention import RetentionUpdate, policy, update_policy
from .diagnostics import app_usage, model_cache, runtime_status, storage_status
from .youtube_jobs import register_youtube
from .processes import ProcessFailure


class Login(BaseModel):
    key: str = Field(min_length=1, max_length=200)


class YoutubeCreate(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    title: str = Field(default='', max_length=200)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.prepare()
        prepare_owner_key(settings.owner_key_path)
        engine = make_engine(settings.database_path)
        migrate(engine)
        settings.database_path.chmod(0o600)
        app.state.engine = engine
        app.state.repository = Repository(engine)
        app.state.login_attempts = []
        app.state.login_lock = threading.Lock()
        app.state.diagnostic_lock = threading.Lock()
        app.state.runtime_diagnostic = None
        app.state.logger = configure_log(settings.data_dir / "logs")
        event(app.state.logger, "APP_STARTED")
        worker = None
        if settings.worker_enabled:
            worker = subprocess.Popen([sys.executable, '-m', 'meeting_minutes.worker'],
                                      env=worker_env(settings), start_new_session=True)
        app.state.worker = worker
        try:
            yield
        finally:
            if worker and worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    # 다음 관리자가 저장된 프로세스 식별자로 복구한다.
                    worker.kill()
                    worker.wait()
            event(app.state.logger, "APP_STOPPED")
            engine.dispose()

    app = FastAPI(title="로컬 회의록", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    origins = {value for value in (settings.origin, settings.dev_origin) if value}
    authorities = {urlsplit(value).netloc for value in origins}

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(CodexFailure)
    async def invalid_minutes(request, exc):
        return JSONResponse({'detail': exc.code}, status_code=422)

    @app.post('/api/meetings')
    def create_meeting(body: MeetingCreate, request: Request, session=Depends(local_session)):
        automatic = body.model_copy(update={'language': 'auto', 'speakers': None, 'allow_external_text': True})
        return app.state.repository.create_meeting(automatic, request.headers.get('idempotency-key'))

    @app.get('/api/meetings')
    def list_meetings(q: str = '', document_kind: Literal['meeting', 'video_summary'] = 'meeting', session=Depends(local_session)):
        return app.state.repository.meetings(q[:200], document_kind)

    @app.post('/api/videos')
    def create_video(body: MeetingCreate, request: Request, session=Depends(local_session)):
        automatic = body.model_copy(update={'language': 'auto', 'speakers': None, 'allow_external_text': True, 'occurred_at': None})
        return app.state.repository.create_meeting(automatic, request.headers.get('idempotency-key'), document_kind='video_summary')

    @app.post('/api/videos/youtube')
    def create_youtube(body: YoutubeCreate, request: Request, session=Depends(local_session)):
        try:
            job = register_youtube(app.state.repository, body.url, body.title, request.headers.get('idempotency-key', ''))
        except ProcessFailure as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        return JSONResponse(job, status_code=202)

    @app.get('/api/meetings/{meeting_id}')
    def get_meeting(meeting_id: str, session=Depends(local_session)):
        return app.state.repository.meeting(meeting_id)

    @app.delete('/api/meetings/{meeting_id}')
    def delete_meeting(meeting_id: str, session=Depends(local_session)):
        request_delete(app.state.repository, meeting_id)
        done = purge_meeting(app.state.repository, settings, meeting_id)
        return JSONResponse({'id': meeting_id, 'status': 'deleted' if done else 'deleting'}, status_code=200 if done else 202)

    @app.get('/api/deletions/{meeting_id}')
    def deletion_status(meeting_id: str, session=Depends(local_session)):
        with app.state.engine.connect() as connection:
            row = connection.execute(text('SELECT deleted_at FROM meetings WHERE id=:id'), {'id': meeting_id}).first()
        return {'id': meeting_id, 'status': 'deleted' if row is None else 'deleting' if row.deleted_at else 'not_requested'}

    @app.get('/api/settings/retention')
    def retention_policy(session=Depends(owner_session)):
        return policy(app.state.repository)

    @app.patch('/api/settings/retention')
    def change_retention(body: RetentionUpdate, session=Depends(owner_session)):
        return update_policy(app.state.repository, body)

    @app.put('/api/meetings/{meeting_id}/media')
    async def upload(meeting_id: str, filename: str, request: Request, session=Depends(local_session)):
        return await receive_upload(request, meeting_id, filename, request.headers.get('idempotency-key', ''))

    @app.get('/api/meetings/{meeting_id}/media')
    def media_metadata(meeting_id: str, session=Depends(local_session)):
        return media_info(app.state.repository, settings, meeting_id)

    @app.get('/api/meetings/{meeting_id}/audio')
    def audio(meeting_id: str, session=Depends(local_session)):
        return FileResponse(audio_path(app.state.repository, settings, meeting_id), media_type='audio/wav')

    @app.get('/api/meetings/{meeting_id}/export')
    def export(meeting_id: str, format: Literal['md', 'txt'] = 'md', version: str | None = None,
               session=Depends(local_session)):
        filename, content = export_download(app.state.repository, meeting_id, version, markdown=format == 'md')
        return Response(content, media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/api/jobs')
    def jobs(session=Depends(local_session)):
        values = app.state.repository.jobs()
        waiting = sorted((row for row in values if row['state'] == 'QUEUED'), key=lambda row: row['sequence'])
        positions = {}
        for summary in (False, True):
            lane = [row for row in waiting if (row['kind'] == 'summarize') == summary]
            positions.update({row['id']: index + 1 for index, row in enumerate(lane)})
        return [row | {'queue_position': positions.get(row['id']),
                       'elapsed_seconds': max(0, (row['finished_at'] or time.time()) - row['started_at']) if row['started_at'] else 0}
                for row in values]

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str, session=Depends(local_session)):
        return app.state.repository.cancel(job_id)

    @app.post('/api/jobs/{job_id}/retry')
    def retry(job_id: str, session=Depends(local_session)):
        return app.state.repository.retry(job_id)

    @app.post('/api/jobs/{job_id}/track')
    def select_track(job_id: str, track: int, session=Depends(local_session)):
        return app.state.repository.select_track(job_id, track)

    @app.get('/api/meetings/{meeting_id}/transcript')
    def transcript(meeting_id: str, version: str | None = None, session=Depends(local_session)):
        return current_transcript(app.state.repository, meeting_id, version)

    @app.get('/api/meetings/{meeting_id}/transcript/versions')
    def transcript_versions(meeting_id: str, session=Depends(local_session)):
        return list_transcripts(app.state.repository, meeting_id)

    @app.post('/api/meetings/{meeting_id}/transcript/edits')
    def transcript_edit(meeting_id: str, body: TranscriptEdit, session=Depends(local_session)):
        return edit_transcript(app.state.repository, meeting_id, body.expected_revision, body.operation.model_dump())

    @app.post('/api/meetings/{meeting_id}/minutes/generate')
    def generate_minutes(meeting_id: str, body: GenerateMinutes, request: Request, session=Depends(local_session)):
        job = queue_generation(app.state.repository, settings, meeting_id, body, request.headers.get('idempotency-key', ''))
        return JSONResponse(job, status_code=200 if job['state'] == 'COMPLETED' else 202)

    @app.post('/api/jobs/{job_id}/finish-transcript-only')
    def keep_transcript(job_id: str, session=Depends(local_session)):
        return finish_transcript_only(app.state.repository, job_id)

    @app.get('/api/meetings/{meeting_id}/minutes')
    def minutes(meeting_id: str, version: str | None = None, session=Depends(local_session)):
        return read_minutes(app.state.repository, meeting_id, version)

    @app.get('/api/meetings/{meeting_id}/minutes/versions')
    def minutes_versions(meeting_id: str, session=Depends(local_session)):
        return list_minutes(app.state.repository, meeting_id)

    @app.patch('/api/meetings/{meeting_id}/minutes/{version}')
    def update_minutes(meeting_id: str, version: str, body: EditDocument, session=Depends(local_session)):
        return edit_minutes(app.state.repository, meeting_id, version, body.expected_revision, body.content)

    @app.post('/api/meetings/{meeting_id}/minutes/{version}/confirm')
    def confirm_minutes(meeting_id: str, version: str, body: ConfirmMinutes, session=Depends(local_session)):
        return edit_minutes(app.state.repository, meeting_id, version, body.expected_revision, confirm=True)

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.headers.get("host") not in authorities:
            return JSONResponse({"detail": "허용되지 않은 호스트입니다"}, status_code=400)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "외부 사이트 요청은 허용되지 않습니다"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") not in origins:
                return JSONResponse({"detail": "허용되지 않은 출처입니다"}, status_code=403)
            is_upload = request.method == 'PUT' and request.url.path.startswith('/api/meetings/') and request.url.path.endswith('/media')
            if not is_upload:
                chunks, size = [], 0
                body_limit = 1_100_000 if request.method == 'PATCH' and '/minutes/' in request.url.path else 65536
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > body_limit:
                        return JSONResponse({'detail': '요청 본문이 너무 큽니다'}, status_code=413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        now = time.time()
        with app.state.login_lock:
            attempts = [stamp for stamp in app.state.login_attempts if stamp > now - 60]
            app.state.login_attempts = attempts
            if len(attempts) >= 10:
                raise HTTPException(429, "잠시 후 다시 시도하세요")
            attempts.append(now)
        if not secrets.compare_digest(digest(body.key), digest(settings.owner_key_path.read_text().strip())):
            raise HTTPException(401, "소유자 키가 일치하지 않습니다")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with app.state.engine.begin() as connection:
            connection.execute(text("DELETE FROM owner_sessions WHERE expires_at<=:now"), {"now": now})
            connection.execute(text("INSERT INTO owner_sessions VALUES (:hash,:csrf,:expiry)"),
                               {"hash": digest(token), "csrf": csrf, "expiry": now + 43200})
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=43200, path="/")
        local = ensure_local_session(request, response)
        return {"csrf_token": local['csrf_token'], 'owner': True}

    @app.post('/api/auth/local')
    def bootstrap(request: Request, response: Response):
        local = ensure_local_session(request, response)
        return {'csrf_token': local['csrf_token'], 'owner': has_owner(request)}

    @app.get("/api/auth/session")
    def session(request: Request, session=Depends(local_session)):
        return {"csrf_token": session["csrf_token"], 'owner': has_owner(request)}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, session=Depends(local_session)):
        with app.state.engine.begin() as connection:
            connection.execute(text("DELETE FROM owner_sessions WHERE token_hash=:hash"),
                               {"hash": digest(request.cookies.get(COOKIE, ""))})
        response.delete_cookie(COOKIE, path="/")
        with app.state.diagnostic_lock:
            app.state.runtime_diagnostic = None
        return {"status": "logged_out", 'owner': False, 'csrf_token': session['csrf_token']}

    @app.get("/api/diagnostics")
    def diagnostics(session=Depends(owner_session)):
        with app.state.engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        with app.state.diagnostic_lock:
            cached = app.state.runtime_diagnostic
            if cached is None or time.time() - cached['checked_at'] >= 30:
                cached = runtime_status(settings)
                app.state.runtime_diagnostic = cached
        return {"version": __version__, "database_revision": revision, "device": "cpu",
                "worker_alive": app.state.worker is not None and app.state.worker.poll() is None,
                "threads": settings.threads, "model": "large-v3-turbo", "diarization": "community-1",
                "max_upload_bytes": settings.max_upload_bytes,
                "max_duration_seconds": settings.max_duration_seconds, "external_text_requires_consent": True,
                "storage": storage_status(settings), "model_cache": model_cache(settings),
                "runtime": cached, "usage": app_usage(app.state.repository)}

    if settings.web_dir and settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
