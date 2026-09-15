"""Persistent studio queue. Run in a dedicated MAIN process (engine uses signals)."""
from __future__ import annotations

import time
from pathlib import Path

from ..phase2_pipeline import ports
from ..translation import batches
from ..util import digest, file_lock, read_json
from .catalog import Catalog, Conflict


class JobExecutor:
    def __init__(self, catalog, job):
        self.catalog = catalog
        self.engine = catalog.engine
        self.job = job
        self.payload = job["payload"]
        self.result = {"executions": {}, "units": [], "quality_status": "REVIEW"}
        self.old = catalog.get("studio_jobs", job["retry_of"])["result"] if job["retry_of"] else None

    def progress(self):
        self.catalog.job_progress(self.job["id"], self.result)

    def value(self, artifact, asset):
        return read_json(Path(self.engine.artifact(artifact, asset)["path"]))

    def step(self, key, asset, node, strategy, inputs, params=None, models=None):
        self.result["current_step"] = key
        self.result["asset_sha512"] = asset
        self.progress()
        previous = (self.old or {}).get("executions", {}).get(key)
        if self.old and self.old.get("asset_sha512") != asset:
            previous = None
        scope = "studio_" + digest([self.job["kind"], self.payload.get("plan_id") or self.payload.get("profile_id") or self.payload.get("reference_id") or self.job["episode_id"], key])[:32]
        try:
            result = self.engine.run(asset, node, strategy, inputs, params or {}, scope=scope,
                retry_of=previous["execution_id"] if previous else None, model_deployment=models)
        except BaseException:
            # Preserve the new failed execution ID too, so a subsequent retry is linked.
            latest = self.catalog.db.one("SELECT id FROM executions WHERE asset_sha512=%s AND node=%s AND scope=%s ORDER BY version DESC LIMIT 1", (asset, node, scope))
            if latest:
                self.result["executions"][key] = self.engine.result(latest["id"])
                self.progress()
            raise
        self.result["executions"][key] = result
        self.progress()
        return ports(result)

    def import_profile(self, profile_id, asset, purpose, key):
        profile = self.catalog.usable_profile(profile_id, published=purpose != "preview")
        p = profile["payload"]
        result = self.step(key, asset, "N10", "studio_import_reference",
            {k: p["references"][k]["artifact_id"] for k in ("reference", "audio")},
            {"profile_id": profile_id, "profile_sha512": profile["payload_sha512"], "purpose": purpose})
        return profile, result

    def normalize_dub(self, key, asset, raw, models):
        configured = models.get("N12/audio_qa_asr", [])
        strategy = "audio_qa_asr" if configured else "audio_qa"
        result = self.step(key, asset, "N12", strategy, {"audio": raw["audio"], "clip": raw["clip"]}, models=configured)
        # Engine now persists FAIL. Do not use engine.artifact on rejected producer;
        # inspect the immutable QA file to report the reason while retaining evidence.
        qa_row = self.catalog.db.one("SELECT relative_path FROM artifacts WHERE id=%s", (result["qa"],))
        report = read_json(self.engine.root / qa_row["relative_path"])
        return result, report["overall"]

    def run(self):
        kind = self.job["kind"]
        if kind == "PREPARE":
            self.prepare()
        elif kind == "REFERENCE":
            self.reference()
        elif kind == "PREVIEW":
            self.preview()
        elif kind == "MATERIALIZE":
            self.materialize()
        elif kind == "GENERATE":
            self.generate()
        elif kind == "RENDER":
            self.render()
        elif kind == "TRANSLATE":
            self.translate()
        elif kind == "SUGGEST":
            self.suggest()
        else:
            raise ValueError("Unsupported queued job")
        self.result.pop("current_step", None)
        return self.result

    def prepare(self):
        from ..store import DOWNLOAD_POLICY, ingest

        ep = self.catalog.get("studio_episodes", self.payload["episode_id"])
        if ep["active_revision_id"] != self.payload["expected_revision"]:
            raise Conflict("视频分段已变化，不能用旧准备任务覆盖；请重新提交分析")
        refresh = False
        if ep["source_artifact_id"] and self.payload["source_url"] and not ep["active_revision_id"]:
            source_ref = self.engine.artifact(ep["source_artifact_id"], ep["asset_sha512"])
            receipts = self.catalog.db.query("SELECT id FROM artifacts WHERE execution_id=%s AND schema_id='AcquisitionReceipt.v1'", (source_ref["execution_id"],))
            refresh = not receipts or self.value(receipts[0]["id"], ep["asset_sha512"]).get("download_policy") != DOWNLOAD_POLICY
        if not ep["source_artifact_id"] or refresh:
            acquired = ingest(self.engine, self.payload["source_url"], download=True)
            self.catalog.bind_source(ep["id"], acquired["asset_sha512"], acquired["source_artifact_id"], replace_unreviewed=refresh)
            ep = self.catalog.get("studio_episodes", ep["id"])
        asset, source, settings = ep["asset_sha512"], ep["source_artifact_id"], self.payload["settings"]
        probe = self.step("probe", asset, "N03", "ffprobe", {"source": source})
        c = self.step("canonical", asset, "N04", "excerpt" if "excerpt" in settings else "original",
                      {"source": source, "probe": probe["probe"]}, settings.get("excerpt", {}))
        inventory = self.step("inventory", asset, "N05", "inventory", {"video": c["video"], "probe": probe["probe"]},
                              {"roi": settings.get("roi", [0, .5, 1, 1])})
        speech = self.step("speech", asset, "N07", "whisper", {"audio": c["audio"]})
        mode = settings.get("caption_mode", "asr")
        if mode == "asr":
            captions = self.step("captions", asset, "N06", "studio_captions",
                                 {"speech": speech["speech"], "inventory": inventory["inventory"]})
        else:
            captions = self.step("captions", asset, "N06", "captions",
                {"source": source, "video": c["video"], "canonical": c["canonical"], "inventory": inventory["inventory"]},
                {"mode": mode, "stride_frames": settings.get("stride_frames", 25)})
        raw = self.value(speech["speech"], asset)
        segments = [{"start_sample": s["start_sample"], "end_sample": s["end_sample"], "text": s["text"],
                     "local_speaker": s.get("speaker_id")} for s in raw["segments"] if s["text"].strip()]
        if settings.get("diarization", False):
            track = self.step("diarization", asset, "N07", "speaker_diarization", {"audio": c["audio"]},
                              {"cluster_threshold": settings.get("cluster_threshold", .5)})
            turns = self.value(track["speakers"], asset)["turns"]
            for s in segments:
                candidates = {t["speaker_id"] for t in turns if t["start_sample"] < s["end_sample"] and t["end_sample"] > s["start_sample"]}
                s["local_speaker"] = next(iter(candidates)) if len(candidates) == 1 else None
        bindings = {"audio": c["audio"], "video": c["video"], "canonical": c["canonical"],
                    "speech": speech["speech"], "vad": speech["vad"], "captions": captions["captions"]}
        rev = self.catalog.create_revision(ep["id"], bindings, segments, self.payload["expected_revision"])
        self.result.update(revision_id=rev["id"], segment_count=len(segments), state="WAITING_REVIEW")

    def reference(self):
        ref = self.catalog.get("studio_references", self.payload["reference_id"])
        if ref["state"] != "CANDIDATE" or ref["artifacts"] is not None:
            raise ValueError("只能生成尚未固定的候选参考")
        p = ref["payload"]
        result = self.step("reference", p["asset_sha512"], "N10", "studio_reference",
            {"audio": p["audio_artifact_id"], "speech": p["speech_artifact_id"]},
            {k: p[k] for k in ("start_sample", "end_sample", "transcript", "character_id", "annotations", "reviewer", "reason")})
        self.catalog.finish_reference(ref["id"], result)
        self.result.update(reference_id=ref["id"], **result)

    def preview(self):
        profile = self.catalog.usable_profile(self.payload["profile_id"], published=False)
        asset = profile["payload"]["source_asset_sha512"]
        profile, ref = self.import_profile(profile["id"], asset, "preview", "import_reference")
        unit = "sample_" + self.job["id"].split("_", 1)[1]
        translated = self.step("sample_text", asset, "N09", "studio_sample", {"reference": ref["reference"]},
                               {"unit_id": unit, "text": self.payload["text"]})
        raw = self.step("sample_voice", asset, "N11", profile["payload"]["strategy"],
                        {"translation": translated["translation"], "reference": ref["reference"], "audio": ref["audio"]},
                        profile["payload"]["params"] | {"unit_id": unit}, models=profile["payload"]["models"])
        dub, quality = self.normalize_dub("sample_qa", asset, raw, self.engine.config.get("models", {}))
        self.result.update(profile_id=profile["id"], text=self.payload["text"], audio=dub["audio"], raw_audio=raw["audio"],
                           clip=dub["clip"], qa=dub["qa"], quality_status=quality)

    def materialize(self):
        plan = self.catalog.get("studio_plans", self.payload["plan_id"])
        if plan["bindings"] is not None:
            raise ValueError("计划材料已经固定，请新建计划而不是覆盖")
        p, asset = plan["payload"], plan["payload"]["asset_sha512"]
        utterances = self.step("reviewed_units", asset, "N08", "studio_utterances",
            {k: p["bindings"][k] for k in ("audio", "speech", "canonical", "captions", "vad")},
            {"items": p["items"], "reviewer": p["reviewer"], "reason": p["reason"], "require_coverage": True})
        translated, by_unit = [], {}
        for i, group in enumerate(batches(p["items"])):
            tr = self.step(f"translation_{i}", asset, "N09", "studio_translation", {"utterances": utterances["utterances"]},
                {"batch_index": i, "items": [{"unit_id": u["unit_id"], "source_text": u["text"], "text": u["chinese_text"]} for u in group],
                 "reviewer": p["reviewer"], "reason": p["reason"]})
            translated.append(tr["translation"])
            by_unit.update({u["unit_id"]: tr["translation"] for u in group})
        bindings = {"utterances": utterances["utterances"], "translations": translated, "by_unit": by_unit}
        self.catalog.bind_plan(plan["id"], bindings)
        self.result.update(plan_id=plan["id"], bindings=bindings)

    def generate(self):
        plan = self.catalog.get("studio_plans", self.payload["plan_id"])
        if plan["state"] != "READY":
            raise ValueError("Generation plan is not materialized")
        p, asset, bindings = plan["payload"], plan["payload"]["asset_sha512"], plan["bindings"]
        # Deliberately do not read current annotations/default profiles here. A
        # queued plan is a frozen request even if the UI changes while it runs.
        selected = set(self.payload["unit_ids"])
        units = [u for u in p["items"] if u["unit_id"] in selected]
        if len(units) != len(selected):
            raise ValueError("Unknown generation unit")
        prepared = {}
        for u in units:
            cid, ident = u["speaker_id"], u["unit_id"]
            snap = p["profiles"][cid]
            if cid not in prepared:
                profile, reference = self.import_profile(snap["id"], asset, "dubbing", "import_" + cid)
                if profile["payload_sha512"] != snap["sha512"]:
                    raise ValueError("Pinned profile changed")
                prepared[cid] = profile, reference
            profile, reference = prepared[cid]
            # A revocation during a long batch blocks the NEXT model invocation.
            self.catalog.usable_profile(profile["id"])
            raw = self.step("tts_" + ident, asset, "N11", profile["payload"]["strategy"],
                {"translation": bindings["by_unit"][ident], "reference": reference["reference"], "audio": reference["audio"]},
                profile["payload"]["params"] | {"unit_id": ident}, models=profile["payload"]["models"])
            dub, quality = self.normalize_dub("qa_" + ident, asset, raw, p["models"])
            self.result["units"].append({"unit_id": ident, "character_id": cid, "profile_id": profile["id"],
                "audio": dub["audio"], "raw_audio": raw["audio"], "clip": dub["clip"], "raw_clip": raw["clip"],
                "qa": dub["qa"], "translation": bindings["by_unit"][ident], "quality_status": quality})
            self.progress()
        self.result.update(plan_id=plan["id"], quality_status="FAIL" if any(u["quality_status"] == "FAIL" for u in self.result["units"]) else "REVIEW")

    def render(self):
        plan = self.catalog.get("studio_plans", self.payload["plan_id"])
        p, b, asset = plan["payload"], plan["bindings"], plan["payload"]["asset_sha512"]
        c = p["bindings"]
        selections = self.payload["selections"]
        if set(selections) != {u["unit_id"] for u in p["items"]}:
            raise ValueError("Incomplete frozen clip selection")
        rows = []
        for u in p["items"]:
            snap = selections[u["unit_id"]]
            actual = self.catalog.get("studio_clip_selections", snap["id"])
            if actual["plan_id"] != plan["id"] or actual["payload"] != snap["payload"]:
                raise ValueError("Frozen selection changed")
            rows.append(snap["payload"])
        timeline = self.step("timeline", asset, "N16", "dub_gap_first", {
            "canonical": c["canonical"], "utterances": b["utterances"],
            "clips": [r["clip"] for r in rows], "raw_clips": [r["raw_clip"] for r in rows], "translations": b["translations"]})
        settings = p["render_settings"]
        anchor = settings.get("source_subtitle_box")
        if settings.get("preserve_source_english", False) and settings.get("footer_height", 0) == 0 and anchor is None:
            boxes = [box for cue in self.value(c["captions"], asset)["cues"] for box in cue["boxes"]]
            if not boxes:
                raise ValueError("保留原英文时需要实际字幕框；请使用 OCR 字幕证据或配置 source_subtitle_box")
            distinct = {tuple(box) for box in boxes}
            if len(distinct) != 1:
                raise ValueError("英文字幕框随页面变化，需逐段/逐帧定位，不能用全片并集代替当前字幕框（spec.md）")
            anchor = [int(v) for v in next(iter(distinct))]
        layout = self.step("layout", asset, "N17", "bilingual", {
            "timeline": timeline["timeline"], "utterances": b["utterances"], "translations": b["translations"], "video": c["video"]},
            {"font_size": settings.get("font_size", 24), "preserve_source_english": settings.get("preserve_source_english", False),
             "footer_height": settings.get("footer_height", 0), "source_subtitle_box": anchor},
            models=p["models"]["N17/bilingual"])
        render = self.step("render", asset, "N18", "dub_ffmpeg", {
            "video": c["video"], "audio": c["audio"], "timeline": timeline["timeline"], "dubs": [r["audio"] for r in rows],
            "layout": layout["layout"], "overlays": layout["overlays"]})
        review = self.step("review", asset, "N19", "review", {
            "render": render["master"], "render_qa": render["qa"], "layout": layout["layout"], "audio_qa": [r["qa"] for r in rows]})
        self.result.update(plan_id=plan["id"], render=render, timeline=timeline, layout=layout, review=review, state="REVIEW")

    def translate(self):
        rev = self.catalog.get("studio_revisions", self.payload["revision_id"])
        ep = self.catalog.get("studio_episodes", rev["episode_id"])
        b, asset = rev["payload"]["bindings"], ep["asset_sha512"]
        # English text and IDs were snapshotted when queued, not read from latest.
        rows = self.payload["items"]
        utterances = self.step("translation_units", asset, "N08", "studio_utterances",
            {k: b[k] for k in ("audio", "speech", "canonical", "captions", "vad")},
            {"items": rows, "require_coverage": False, "reviewer": "", "reason": "Translation candidate only"})
        suggestions = []
        for i, group in enumerate(batches(rows)):
            tr = self.step(f"translate_{i}", asset, "N09", "codex", {"utterances": utterances["utterances"]}, {"batch_index": i})
            for u in self.value(tr["translation"], asset)["items"]:
                suggestions.append({"kind": "translation", "segment_id": u["unit_id"], "text": u["text"],
                                    "source_annotation_id": self.payload.get("source_annotations", {}).get(u["unit_id"]),
                                    "source_text": u["source_text"], "artifact_id": tr["translation"]})
        self.catalog.add_suggestions(rev["id"], suggestions)
        self.result.update(revision_id=rev["id"], count=len(suggestions), state="WAITING_REVIEW")

    def suggest(self):
        rev = self.catalog.revision_view(self.payload["revision_id"])
        known = {}
        for s in rev["segments"]:
            if s.get("local_speaker") and s["annotation"] and s["annotation"]["status"] == "CONFIRMED":
                known.setdefault(s["local_speaker"], set()).add(s["annotation"]["character_id"])
        proposals = []
        for s in rev["segments"]:
            candidates = known.get(s.get("local_speaker"), set())
            if len(candidates) == 1 and (not s["annotation"] or s["annotation"]["status"] != "CONFIRMED"):
                proposals.append({"kind": "speaker", "segment_id": s["id"], "character_id": next(iter(candidates)),
                    "strategy": "same_episode_local_cluster_hint_v1", "score": None,
                    "reason": "同一集局部分组已有唯一人工角色。未校准，仅供复审，不是跨集声纹识别"})
        self.result.update(self.catalog.add_suggestions(rev["id"], proposals))


def execute_next(catalog):
    """Take the media lock before claiming a job. No thread may share this DB."""
    with file_lock(catalog.root / ".writer.lock"):
        queued = catalog.rows("studio_jobs", "state='QUEUED'")
        if not queued:
            return False
        job = queued[0]
        catalog.finish_job(job["id"], "RUNNING")
        executor = JobExecutor(catalog, catalog.get("studio_jobs", job["id"]))
        try:
            catalog.engine.guard()
            result = executor.run()
        except BaseException as error:
            state = "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED"
            catalog.finish_job(job["id"], state, executor.result, str(error))
            if state == "INTERRUPTED":
                raise
        else:
            catalog.finish_job(job["id"], "SUCCEEDED", result)
        return True


def worker_loop(config, once=False, db_factory=None):
    import logging
    from pymysql.err import OperationalError, InterfaceError
    from ..db import DB

    db_factory = db_factory or DB
    root = Path(config["data_root"])
    initialized = False
    disconnected = False
    with file_lock(root / ".studio.worker.lock"):
        while True:
            db = None
            worked = False
            try:
                db = db_factory(config)
                db.bind_root()
                cat = Catalog(db, config)
                if not initialized:
                    # Reconnection never reruns a claimed model job. Preserve it
                    # as INTERRUPTED; engine.guard/recover owns orphan recovery.
                    with file_lock(root / ".writer.lock"):
                        for job in cat.rows("studio_jobs", "state='RUNNING'"):
                            cat.finish_job(job["id"], "INTERRUPTED", job["result"],
                                "工作台进程或数据库连接中断；先检查 CLI status/recover，再手动重试。未自动调用模型")
                    initialized = True
                disconnected = False
                worked = execute_next(cat)
            except (OperationalError, InterfaceError):
                initialized = False
                if once:
                    raise
                if not disconnected:
                    logging.getLogger(__name__).warning("Studio database unavailable; waiting to reconnect without retrying running jobs")
                disconnected = True
            except BlockingIOError:
                pass
            finally:
                if db is not None:
                    db.close()
            if once:
                return
            if not worked:
                time.sleep(5 if disconnected else 1)
