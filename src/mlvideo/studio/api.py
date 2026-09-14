"""Loopback-only review API. No filesystem paths, model commands or credentials from clients."""
from __future__ import annotations

import hmac
import re
import secrets
import struct
import wave
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from pymysql.err import OperationalError, InterfaceError

from .catalog import Catalog, Conflict
from ..util import read_json


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SeriesIn(Input):
    name: str = Field(max_length=240)
    brand: str = Field(default="", max_length=240)


class CharacterIn(Input):
    name: str = Field(max_length=240)
    notes: str = Field(default="", max_length=4000)


class EpisodeIn(Input):
    title: str = Field(max_length=240)
    source_url: str = ""
    asset_sha512: str | None = None
    source_artifact_id: str | None = None


class AnnotationIn(Input):
    expected_version: int = Field(ge=0)
    character_id: str | None
    status: str
    english_text: str = Field(max_length=20000)
    chinese_text: str = Field(default="", max_length=20000)
    reviewer: str = Field(default="local-user", max_length=240)
    reason: str = Field(max_length=4000)


class BatchItem(AnnotationIn):
    segment_id: str


class NoteIn(Input):
    expected_version: int = Field(ge=0)
    notes: str = Field(max_length=4000)


class BatchIn(Input):
    items: list[BatchItem] = Field(min_length=1, max_length=500)


class SegmentIn(Input):
    id: str | None = Field(default=None, max_length=64)
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=20000)
    local_speaker: str | None = Field(default=None, max_length=240)
    ordinal: int | None = Field(default=None, ge=0)


class RevisionIn(Input):
    expected_revision: str | None
    segments: list[SegmentIn] = Field(min_length=1, max_length=20000)
    reason: str = Field(max_length=4000)


class AttachIn(Input):
    expected_revision: str | None = None
    bindings: dict[str, str]


class ReferenceIn(Input):
    revision_id: str
    character_id: str
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    transcript: str = Field(max_length=20000)
    reviewer: str = Field(default="local-user", max_length=240)
    reason: str = Field(max_length=4000)


class ReviewIn(Input):
    reviewer: str = Field(default="local-user", max_length=240)
    reason: str = Field(max_length=4000)
    checks: dict[str, bool]


class DisableIn(Input):
    reviewer: str = Field(default="local-user", max_length=240)
    reason: str = Field(max_length=4000)


class ProfileIn(Input):
    reference_id: str
    label: str = Field(max_length=240)
    seed: int = Field(default=42, ge=0, le=4294967295)


class PreviewIn(Input):
    chinese_text: str = Field(max_length=2000)


class PublishIn(ReviewIn):
    preview_job_id: str


class PlanIn(Input):
    expected_revision: str
    reviewer: str = Field(default="local-user", max_length=240)
    reason: str = Field(max_length=4000)


class GenerateIn(Input):
    unit_ids: list[str] | None = None


class SelectIn(ReviewIn):
    job_id: str
    unit_id: str


def create_app(config, db_factory=None, allowed_hosts=None):
    from ..db import DB

    factory = db_factory or DB
    allowed_hosts = set(allowed_hosts or {"127.0.0.1", "localhost", "::1"})
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="Series Voice Studio", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    static = Path(__file__).with_name("static")

    @app.middleware("http")
    async def local_boundary(request, call_next):
        host = urlparse("http://" + request.headers.get("host", "")).hostname
        if host not in allowed_hosts:
            return JSONResponse({"error": "只允许本机 Host"}, status_code=403)
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if (site == "cross-site" and request.url.path.startswith("/api/")) or (origin and origin != f"{request.url.scheme}://{request.headers.get('host')}"):
            return JSONResponse({"error": "拒绝跨站访问本机工作台"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("x-studio-token", ""), token):
                return JSONResponse({"error": "会话已过期，请刷新页面"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "请求必须为 application/json"}, status_code=415)
            length = request.headers.get("content-length", "")
            if not length.isdecimal() or int(length) > 2_000_000:
                return JSONResponse({"error": "请求缺少长度或超过 2MB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "media-src 'self' blob:; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"error": str(error), "type": "conflict"}, status_code=409)

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    async def database_unavailable(request, error):
        return JSONResponse({"error": "数据库暂时不可用，请确认 Docker/MySQL 正在运行。恢复后页面会自动重连。",
                             "type": "database_unavailable"}, status_code=503)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": str(error), "type": "invalid_request"}, status_code=400)

    def catalogue():
        db = factory(config)
        try:
            db.bind_root()
            yield Catalog(db, config)
        finally:
            db.close()

    @app.get("/")
    def index():
        return FileResponse(static / "index.html", media_type="text/html")

    @app.get("/api/session")
    def session():
        return {"token": token, "mode": "local", "tts_strategy": "cosyvoice3_zero_shot",
                "configured": {k: bool(config.get("models", {}).get(k)) for k in
                               ("N07/whisper", "N09/codex", "N11/cosyvoice3_zero_shot", "N12/audio_qa_asr", "N17/bilingual")}}

    @app.get("/api/series")
    def series(cat=Depends(catalogue)):
        return cat.rows("studio_series")

    @app.post("/api/series", status_code=201)
    def new_series(body: SeriesIn, cat=Depends(catalogue)):
        return cat.create_series(**body.model_dump())

    @app.get("/api/series/{identity}")
    def series_detail(identity: str, cat=Depends(catalogue)):
        s = cat.get("studio_series", identity)
        episodes = cat.rows("studio_episodes", "series_id=%s", (identity,))
        for ep in episodes:
            if ep["active_revision_id"]:
                rev = cat.revision_view(ep["active_revision_id"])
                ep["segment_count"] = len(rev["segments"])
                ep["confirmed_count"] = sum(bool(u["annotation"] and u["annotation"]["status"] == "CONFIRMED") for u in rev["segments"])
            else:
                ep.update(segment_count=0, confirmed_count=0)
        return s | {"characters": cat.rows("studio_characters", "series_id=%s", (identity,)), "episodes": episodes}

    @app.post("/api/series/{identity}/characters", status_code=201)
    def new_character(identity: str, body: CharacterIn, cat=Depends(catalogue)):
        return cat.create_character(identity, **body.model_dump())

    @app.post("/api/series/{identity}/episodes", status_code=201)
    def new_episode(identity: str, body: EpisodeIn, cat=Depends(catalogue)):
        return cat.create_episode(identity, **body.model_dump())

    @app.get("/api/episodes/{identity}")
    def episode_detail(identity: str, cat=Depends(catalogue)):
        ep = cat.get("studio_episodes", identity)
        plans = cat.rows("studio_plans", "episode_id=%s", (identity,))
        for p in plans:
            p["stale"] = cat.plan_stale(p)
        return ep | {"revision": cat.revision_view(ep["active_revision_id"]) if ep["active_revision_id"] else None,
                     "plans": plans, "jobs": cat.rows("studio_jobs", "episode_id=%s", (identity,))}

    @app.post("/api/episodes/{identity}/prepare", status_code=202)
    def prepare(identity: str, cat=Depends(catalogue)):
        return cat.prepare(identity)

    @app.post("/api/episodes/{identity}/attach", status_code=201)
    def attach(identity: str, body: AttachIn, cat=Depends(catalogue)):
        if set(body.bindings) != {"audio", "video", "canonical", "speech", "vad", "captions"}:
            raise ValueError("需要 audio/video/canonical/speech/vad/captions 六个明确产物 ID")
        ep = cat.get("studio_episodes", identity)
        speech_ref = cat.engine.artifact(body.bindings["speech"], ep["asset_sha512"])
        speech = read_json(Path(speech_ref["path"]))
        segments = [{"start_sample": s["start_sample"], "end_sample": s["end_sample"], "text": s["text"],
                     "local_speaker": s.get("speaker_id")} for s in speech["segments"] if s["text"].strip()]
        return cat.create_revision(identity, body.bindings, segments, body.expected_revision, "接入已有不可变分析，角色仍待人工确认")

    @app.post("/api/episodes/{identity}/revisions", status_code=201)
    def revise(identity: str, body: RevisionIn, cat=Depends(catalogue)):
        if not body.expected_revision:
            raise ValueError("请先分析或接入已有分析")
        old = cat.get("studio_revisions", body.expected_revision)
        if old["episode_id"] != identity:
            raise ValueError("分段版本不属于该视频")
        return cat.create_revision(identity, old["payload"]["bindings"], [s.model_dump(exclude_none=True) for s in body.segments],
                                   body.expected_revision, body.reason)

    @app.get("/api/revisions/{identity}")
    def revision(identity: str, cat=Depends(catalogue)):
        return cat.revision_view(identity)

    @app.post("/api/revisions/{identity}/segments/{segment}/annotation", status_code=201)
    def annotate(identity: str, segment: str, body: AnnotationIn, cat=Depends(catalogue)):
        return cat.annotate(identity, segment, **body.model_dump())

    @app.post("/api/revisions/{identity}/segments/{segment}/note", status_code=201)
    def note(identity: str, segment: str, body: NoteIn, cat=Depends(catalogue)):
        return cat.save_note(identity, segment, **body.model_dump())

    @app.post("/api/revisions/{identity}/annotations", status_code=201)
    def batch_annotate(identity: str, body: BatchIn, cat=Depends(catalogue)):
        return cat.batch_annotate(identity, [x.model_dump() for x in body.items])

    @app.post("/api/revisions/{identity}/translate", status_code=202)
    def translate(identity: str, cat=Depends(catalogue)):
        return cat.translate(identity)

    @app.post("/api/revisions/{identity}/suggest", status_code=202)
    def suggest(identity: str, cat=Depends(catalogue)):
        return cat.suggest(identity)

    @app.get("/api/characters/{identity}")
    def character(identity: str, cat=Depends(catalogue)):
        c = cat.get("studio_characters", identity)
        profiles = cat.rows("studio_profiles", "character_id=%s", (identity,))
        ids = {p["id"] for p in profiles}
        jobs = [j for j in cat.rows("studio_jobs", "kind='PREVIEW'") if j["payload"].get("profile_id") in ids]
        originals = []
        for ep in cat.rows("studio_episodes", "series_id=%s", (c["series_id"],)):
            if ep["active_revision_id"]:
                rev = cat.revision_view(ep["active_revision_id"])
                originals += [s | {"episode_title": ep["title"], "revision_id": rev["id"],
                                     "audio_artifact_id": rev["payload"]["bindings"]["audio"]}
                              for s in rev["segments"] if s["annotation"] and s["annotation"]["character_id"] == identity]
        return c | {"references": cat.rows("studio_references", "character_id=%s", (identity,)),
                    "profiles": profiles, "preview_jobs": jobs, "originals": originals}

    @app.post("/api/references", status_code=201)
    def new_reference(body: ReferenceIn, cat=Depends(catalogue)):
        return cat.create_reference(**body.model_dump())

    @app.post("/api/references/{identity}/approve")
    def approve_reference(identity: str, body: ReviewIn, cat=Depends(catalogue)):
        return cat.approve_reference(identity, **body.model_dump())

    @app.post("/api/references/{identity}/disable")
    def disable_reference(identity: str, body: DisableIn, cat=Depends(catalogue)):
        return cat.disable_reference(identity, **body.model_dump())

    @app.post("/api/characters/{identity}/profiles", status_code=201)
    def new_profile(identity: str, body: ProfileIn, cat=Depends(catalogue)):
        return cat.create_profile(identity, **body.model_dump())

    @app.post("/api/profiles/{identity}/preview", status_code=202)
    def preview(identity: str, body: PreviewIn, cat=Depends(catalogue)):
        return cat.preview(identity, **body.model_dump())

    @app.post("/api/profiles/{identity}/publish")
    def publish(identity: str, body: PublishIn, cat=Depends(catalogue)):
        return cat.publish_profile(identity, **body.model_dump())

    @app.post("/api/episodes/{identity}/plans", status_code=201)
    def new_plan(identity: str, body: PlanIn, cat=Depends(catalogue)):
        return cat.create_plan(identity, **body.model_dump())

    @app.get("/api/plans/{identity}")
    def plan(identity: str, cat=Depends(catalogue)):
        p = cat.get("studio_plans", identity)
        return p | {"stale": cat.plan_stale(p), "selections": cat.selections(identity),
                    "jobs": [j for j in cat.rows("studio_jobs", "episode_id=%s", (p["episode_id"],))
                             if j["payload"].get("plan_id") == identity]}

    @app.post("/api/plans/{identity}/generate", status_code=202)
    def generate(identity: str, body: GenerateIn, cat=Depends(catalogue)):
        return cat.generate(identity, **body.model_dump())

    @app.post("/api/plans/{identity}/select", status_code=201)
    def select_clip(identity: str, body: SelectIn, cat=Depends(catalogue)):
        return cat.select_clip(identity, **body.model_dump())

    @app.post("/api/plans/{identity}/render", status_code=202)
    def render(identity: str, cat=Depends(catalogue)):
        return cat.render(identity)

    @app.get("/api/jobs")
    def jobs(cat=Depends(catalogue)):
        return cat.rows("studio_jobs", order="created_at DESC,id DESC")[:250]

    @app.get("/api/jobs/{identity}")
    def job(identity: str, cat=Depends(catalogue)):
        return cat.get("studio_jobs", identity)

    @app.post("/api/jobs/{identity}/retry", status_code=202)
    def retry(identity: str, cat=Depends(catalogue)):
        return cat.retry_job(identity)

    @app.get("/api/assets")
    def assets(cat=Depends(catalogue)):
        return cat.db.query("SELECT * FROM assets ORDER BY created_at DESC LIMIT 100")

    @app.get("/api/assets/{asset}/artifacts")
    def artifacts(asset: str, cat=Depends(catalogue)):
        return cat.db.query("SELECT a.id,a.schema_id,a.kind,a.execution_id,a.relative_path FROM artifacts a JOIN executions e ON e.id=a.execution_id WHERE e.asset_sha512=%s AND e.state='SUCCEEDED' ORDER BY e.started_at DESC", (asset,))

    def media_ref(cat, identity):
        row = cat.db.one("SELECT e.asset_sha512 FROM artifacts a JOIN executions e ON a.execution_id=e.id WHERE a.id=%s", (identity,))
        if not row:
            raise ValueError("媒体产物不存在")
        ref = cat.engine.artifact(identity, row["asset_sha512"])
        if ref["schema_id"] not in {"Audio.v1", "Video.v1"}:
            raise ValueError("只允许播放登记的音频/视频，不提供任意文件访问")
        return ref

    @app.api_route("/api/media/{identity}", methods=["GET", "HEAD"])
    def media(identity: str, cat=Depends(catalogue)):
        ref = media_ref(cat, identity)
        mime = "audio/wav" if ref["schema_id"] == "Audio.v1" else ("video/mp4" if ref["path"].endswith(".mp4") else "video/x-matroska")
        if ref["schema_id"] == "Video.v1" and ref["path"].endswith("original.bin"):
            with Path(ref["path"]).open("rb") as source:
                header = source.read(128)
            if header[4:8] == b"ftyp":
                mime = "video/mp4"
            elif b"\x42\x82\x84webm" in header:
                mime = "video/webm"
        return FileResponse(ref["path"], media_type=mime, headers={"ETag": '"' + ref["sha512"] + '"'})

    @app.api_route("/api/audio/{identity}/clip", methods=["GET", "HEAD"])
    def clip(identity: str, request: Request, start_sample: int, end_sample: int, cat=Depends(catalogue)):
        ref = media_ref(cat, identity)
        if ref["schema_id"] != "Audio.v1":
            raise ValueError("需要音频产物")
        path = Path(ref["path"])
        with wave.open(str(path), "rb") as wav:
            rate, channels, width, count = wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()
        if width != 2 or not 0 <= start_sample < end_sample <= count or end_sample-start_sample > 180*rate:
            raise ValueError("试听需为有效 PCM16 区间，最长 180 秒")
        alignment = channels * width
        length = (end_sample-start_sample)*alignment
        header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36+length, b"WAVE", b"fmt ", 16,
                             1, channels, rate, rate*alignment, alignment, width*8, b"data", length)
        total, lo, hi, status = 44+length, 0, 43+length, 200
        requested = request.headers.get("range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                return JSONResponse({"error": "只支持单个字节范围"}, status_code=416, headers={"Content-Range": f"bytes */{total}"})
            a, b = match.groups()
            if a:
                lo, hi = int(a), min(int(b), total-1) if b else total-1
            else:
                lo, hi = max(0, total-int(b)), total-1
            if not 0 <= lo <= hi < total:
                return JSONResponse({"error": "无效字节范围"}, status_code=416, headers={"Content-Range": f"bytes */{total}"})
            status = 206
        headers = {"Content-Length": str(hi-lo+1), "Accept-Ranges": "bytes"}
        if status == 206:
            headers["Content-Range"] = f"bytes {lo}-{hi}/{total}"

        def chunks():
            if request.method == "HEAD":
                return
            if lo < 44:
                yield header[lo:min(hi+1, 44)]
            begin, remaining = max(lo, 44)-44, hi-max(lo, 44)+1
            if remaining <= 0:
                return
            with wave.open(str(path), "rb") as wav:
                wav.setpos(start_sample + begin//alignment)
                drop = begin % alignment
                while remaining:
                    raw = wav.readframes(min(16384, (remaining+drop+alignment-1)//alignment))
                    if not raw:
                        raise ValueError("WAV truncated during playback")
                    block = raw[drop:drop+remaining]
                    drop = 0
                    remaining -= len(block)
                    yield block
        return StreamingResponse(chunks(), status_code=status, media_type="audio/wav", headers=headers)

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
