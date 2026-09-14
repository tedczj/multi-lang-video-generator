"""Series catalogue with short transactions and immutable review/generation snapshots.

The media engine's writer lock is deliberately NOT held by catalogue operations:
a user can review episode B while the single media worker synthesizes episode A.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from ..engine import Engine
from ..util import canonical, digest, file_lock, uid, ensure_no_secrets

TABLES = [
    "studio_series", "studio_characters", "studio_episodes", "studio_revisions",
    "studio_annotations", "studio_suggestions", "studio_references", "studio_profiles",
    "studio_plans", "studio_jobs", "studio_clip_selections", "studio_events",
]
REFERENCE_CHECKS = {"speaker_identity", "transcript", "clean_reference", "complete_words"}
VOICE_CHECKS = {"voice_identity", "chinese_content", "naturalness"}
CLIP_CHECKS = {"voice_identity", "spoken_content", "complete_tail"}


class Conflict(ValueError):
    """The client is editing a stale version. Return HTTP 409, not last-write-wins."""


def unpack(row):
    if row is None:
        return None
    result = dict(row)
    for key, value in list(result.items()):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(value) if isinstance(value, str) else value
            del result[key]
    return result


def text(value, field, limit=20000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{field}: 必须是{'可为空的' if empty else '非空'}文本，最多 {limit} 字符")
    return value.strip()


def youtube_url(value):
    value = text(value, "YouTube URL", 2000)
    p = urlparse(value)
    if (p.scheme != "https" or p.username or p.password or p.port not in (None, 443)
            or p.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}):
        raise ValueError("仅接受 HTTPS YouTube 视频链接，不接受任意网址")
    if p.hostname == "youtu.be":
        video = p.path.strip("/")
    elif p.path == "/watch":
        video = parse_qs(p.query).get("v", [""])[0]
    elif p.path.startswith(("/shorts/", "/embed/", "/live/")):
        video = p.path.split("/")[-1]
    else:
        video = ""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ValueError("需要单个 YouTube 视频链接（不能只有播放列表）")
    # Canonicalize, intentionally ignore playback offsets/playlist parameters.
    return "https://www.youtube.com/watch?v=" + video


def require_checks(checks, expected, reviewer, reason):
    text(reviewer, "reviewer", 240)
    text(reason, "审核说明", 4000)
    if not isinstance(checks, dict) or set(checks) != expected or any(v is not True for v in checks.values()):
        raise ValueError("必须由实际审核者明确确认所有检查项: " + ", ".join(sorted(expected)))


def mutation(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        if self._depth:
            return fn(self, *args, **kwargs)
        with file_lock(self.root / ".studio.catalog.lock", blocking=True):
            with self.db.transaction():
                self._depth += 1
                try:
                    return fn(self, *args, **kwargs)
                finally:
                    self._depth -= 1
    return wrapped


class Catalog:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.root = Path(config["data_root"])
        self.engine = Engine(db, config)
        self._depth = 0

    def rows(self, table, where="", args=(), order="created_at,id"):
        if table not in TABLES:
            raise ValueError("Invalid catalogue table")
        return [unpack(r) for r in self.db.query(
            f"SELECT * FROM {table}" + (" WHERE " + where if where else "") + " ORDER BY " + order, args)]

    def get(self, table, identity):
        if table not in TABLES:
            raise ValueError("Invalid catalogue table")
        row = unpack(self.db.one(f"SELECT * FROM {table} WHERE id=%s", (identity,)))
        if not row:
            raise ValueError("记录不存在: " + str(identity))
        if "payload_sha512" in row and digest(row["payload"]) != row["payload_sha512"]:
            raise ValueError("不可变快照校验失败: " + str(identity))
        return row

    def event(self, identity, action, payload):
        self.db.insert("studio_events", {
            "id": uid("evt"), "aggregate_id": identity, "action": action,
            "payload_json": canonical(payload).decode(),
        })

    def _snapshot(self, table, row, payload):
        ensure_no_secrets(payload)
        self.db.insert(table, row | {"payload_json": canonical(payload).decode(), "payload_sha512": digest(payload)})
        return self.get(table, row["id"])

    def character_in_series(self, identity, series):
        c = self.get("studio_characters", identity)
        if c["series_id"] != series or c["archived"]:
            raise ValueError("角色不属于当前系列或已停用")
        return c

    @mutation
    def create_series(self, name, brand=""):
        row = {"id": uid("series"), "name": text(name, "系列名", 240), "brand": text(brand, "来源", 240, True)}
        self.db.insert("studio_series", row)
        self.event(row["id"], "series.created", row)
        return self.get("studio_series", row["id"])

    @mutation
    def create_character(self, series_id, name, notes=""):
        if self.get("studio_series", series_id)["archived"]:
            raise ValueError("系列已归档")
        row = {"id": uid("char"), "series_id": series_id, "name": text(name, "角色名", 240),
               "notes": text(notes, "备注", 4000, True)}
        self.db.insert("studio_characters", row)
        self.event(row["id"], "character.created", row)
        return self.get("studio_characters", row["id"])

    @mutation
    def rename(self, kind, identity, name, notes=None):
        table = {"series": "studio_series", "character": "studio_characters", "episode": "studio_episodes"}.get(kind)
        if table is None:
            raise ValueError("不能重命名此对象")
        self.get(table, identity)
        field = "title" if kind == "episode" else "name"
        self.db.query(f"UPDATE {table} SET {field}=%s WHERE id=%s", (text(name, "名称", 240), identity))
        if kind == "character" and notes is not None:
            self.db.query(f"UPDATE {table} SET notes=%s WHERE id=%s", (text(notes, "备注", 4000, True), identity))
        self.event(identity, kind + ".renamed", {"name": name, "notes": notes})
        return self.get(table, identity)

    @mutation
    def create_episode(self, series_id, title, source_url="", asset_sha512=None, source_artifact_id=None):
        series = self.get("studio_series", series_id)
        if series["archived"]:
            raise ValueError("系列已归档")
        if bool(asset_sha512) != bool(source_artifact_id):
            raise ValueError("asset_sha512 和 source_artifact_id 必须同时提供")
        if source_url:
            source_url = youtube_url(source_url)
        if source_artifact_id:
            if not asset_sha512:
                raise ValueError("绑定已有源素材时需要 asset_sha512")
            ref = self.engine.artifact(source_artifact_id, asset_sha512)
            if ref["schema_id"] != "Binary.v1":
                raise ValueError("必须绑定源视频 Binary.v1")
            row = self.db.one("SELECT node,strategy_id FROM executions WHERE id=%s", (ref["execution_id"],))
            if row != {"node": "N02", "strategy_id": "source"}:
                raise ValueError("必须使用 N02/source 的源视频产物")
        else:
            source_url = youtube_url(source_url)
        settings = self.config.get("studio", {}).get("prepare_settings", {"caption_mode": "asr"})
        ensure_no_secrets(settings)
        row = {"id": uid("episode"), "series_id": series_id, "title": text(title, "视频标题", 240),
               "source_url": source_url, "asset_sha512": asset_sha512, "source_artifact_id": source_artifact_id,
               "settings_json": canonical(settings).decode()}
        self.db.insert("studio_episodes", row)
        self.event(row["id"], "episode.created", row)
        return self.get("studio_episodes", row["id"])

    @mutation
    def bind_source(self, episode_id, asset_sha512, source_artifact_id, *, replace_unreviewed=False):
        episode = self.get("studio_episodes", episode_id)
        self.engine.artifact(source_artifact_id, asset_sha512)
        if episode["asset_sha512"] and (episode["asset_sha512"], episode["source_artifact_id"]) != (asset_sha512, source_artifact_id):
            if not replace_unreviewed or episode["active_revision_id"] or self.rows("studio_revisions", "episode_id=%s", (episode_id,)):
                raise Conflict("视频已绑定另一份源素材。请新建视频条目，不覆盖旧资源")
        self.db.query("UPDATE studio_episodes SET asset_sha512=%s,source_artifact_id=%s WHERE id=%s",
                      (asset_sha512, source_artifact_id, episode_id))
        self.event(episode_id, "episode.source_bound", {"asset_sha512": asset_sha512, "source_artifact_id": source_artifact_id,
            "previous_asset_sha512": episode["asset_sha512"], "previous_source_artifact_id": episode["source_artifact_id"]})

    def revision_view(self, revision_id):
        rev = self.get("studio_revisions", revision_id)
        annotations = {}
        for a in self.rows("studio_annotations", "revision_id=%s", (revision_id,), "version,id"):
            annotations[a["segment_id"]] = a
        suggestions = {}
        for s in self.rows("studio_suggestions", "revision_id=%s", (revision_id,), "version,id"):
            suggestions.setdefault(s["segment_id"], {})[s["payload"].get("kind", "speaker")] = s
        return rev | {"segments": [s | {"annotation": annotations.get(s["id"]),
                                                  "suggestions": suggestions.get(s["id"], {})}
                                   for s in rev["payload"]["segments"]]}

    @mutation
    def create_revision(self, episode_id, bindings, segments, expected_revision=None, reason="自动分析候选，未审核"):
        ep = self.get("studio_episodes", episode_id)
        if ep["active_revision_id"] != expected_revision:
            raise Conflict("分段版本已变化，请刷新页面后重试")
        required = {"audio": "Audio.v1", "video": "Video.v1", "canonical": "CanonicalMedia.v1",
                    "speech": "SpeechTrack.v1", "vad": "Binary.v1", "captions": "CaptionTrack.v1"}
        if set(bindings) != set(required):
            raise ValueError("分段版本需要完整的音频、视频、时钟、ASR、VAD、字幕绑定")
        refs = {k: self.engine.artifact(v, ep["asset_sha512"]) for k, v in bindings.items()}
        if any(refs[k]["schema_id"] != schema for k, schema in required.items()):
            raise ValueError("分段产物 schema 不匹配")
        if len({refs[k]["execution_id"] for k in ("audio", "video", "canonical")}) != 1:
            raise ValueError("音频/视频/时钟不是同一规范化版本")
        from ..util import read_json
        speech = read_json(Path(refs["speech"]["path"]))
        clock = read_json(Path(refs["canonical"]["path"]))
        if speech["audio_artifact_id"] != bindings["audio"]:
            raise ValueError("ASR 与当前音频版本不一致")
        if refs["speech"]["execution_id"] != refs["vad"]["execution_id"]:
            raise ValueError("VAD 与 ASR 应来自同一分析执行")
        captions = read_json(Path(refs["captions"]["path"]))
        inventory = self.engine.artifact(captions["inventory_artifact_id"], ep["asset_sha512"])
        if inventory["schema_id"] != "SubtitleInventory.v1" or read_json(Path(inventory["path"]))["video_artifact_id"] != bindings["video"]:
            raise ValueError("字幕证据不属于当前规范化视频")
        if not segments or len(segments) > 20000:
            raise ValueError("分段数量必须为 1–20000")
        new, ids = [], set()
        for i, s in enumerate(segments):
            a, b = s["start_sample"], s["end_sample"]
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= clock["source_samples"]:
                raise ValueError("分段必须使用有效的原音频采样范围")
            ident = s.get("id") or uid("seg")
            if not isinstance(ident, str) or not 1 <= len(ident) <= 64 or ident in ids:
                raise ValueError("分段 ID 为空、重复或过长")
            ids.add(ident)
            new.append({"id": ident, "start_sample": a, "end_sample": b,
                        "text": text(s["text"], "英文原文", 20000),
                        "local_speaker": s.get("local_speaker"), "ordinal": i})
        if any(b["start_sample"] < a["start_sample"] for a, b in zip(new, new[1:])):
            raise ValueError("分段需按原音频起始时间排序")
        version = 1 + (self.get("studio_revisions", expected_revision)["version"] if expected_revision else 0)
        row = {"id": uid("rev"), "episode_id": episode_id, "version": version}
        payload = {"bindings": bindings, "segments": new, "source_samples": clock["source_samples"],
                   "sample_rate": 48000, "reason": text(reason, "分段说明", 4000)}
        rev = self._snapshot("studio_revisions", row, payload)
        if expected_revision:
            previous_revision = self.revision_view(expected_revision)
            same_source = previous_revision["payload"]["bindings"] == bindings
            old = {s["id"]: s for s in previous_revision["segments"]} if same_source else {}
            for s in new:
                previous = old.get(s["id"])
                if previous and previous["annotation"] and all(s[k] == previous[k] for k in ("start_sample", "end_sample", "text")):
                    a = previous["annotation"]
                    carry = {k: a[k] for k in ("segment_id", "character_id", "status", "english_text", "chinese_text", "reviewer", "reason")}
                    self.db.insert("studio_annotations", carry | {"id": uid("ann"), "revision_id": row["id"], "version": 1, "carried_from": a["id"]})
        self.db.query("UPDATE studio_episodes SET active_revision_id=%s WHERE id=%s", (row["id"], episode_id))
        self.event(episode_id, "revision.created", {"id": row["id"], "previous": expected_revision, "sha512": rev["payload_sha512"]})
        return self.revision_view(row["id"])

    @mutation
    def annotate(self, revision_id, segment_id, expected_version, character_id, status, english_text, chinese_text, reviewer, reason):
        rev = self.revision_view(revision_id)
        ep = self.get("studio_episodes", rev["episode_id"])
        if ep["active_revision_id"] != revision_id:
            raise Conflict("当前分段已替换，不能提交到旧版本")
        s = next((s for s in rev["segments"] if s["id"] == segment_id), None)
        if s is None:
            raise ValueError("片段不属于当前分段版本")
        old = s["annotation"]
        if expected_version != (old["version"] if old else 0):
            raise Conflict("该片段已由另一个页面修改，请刷新后重新提交")
        if status not in {"CONFIRMED", "UNKNOWN", "OVERLAP"}:
            raise ValueError("无效角色审核状态")
        if status == "CONFIRMED":
            self.character_in_series(character_id, ep["series_id"])
        elif character_id is not None:
            raise ValueError("未知/重叠片段不能指定正式角色")
        row = {"id": uid("ann"), "revision_id": revision_id, "segment_id": segment_id,
               "version": expected_version + 1, "character_id": character_id, "status": status,
               "english_text": text(english_text, "英文原文"), "chinese_text": text(chinese_text, "中文译文", 20000, True),
               "reviewer": text(reviewer, "审核者", 240), "reason": text(reason, "审核说明", 4000)}
        self.db.insert("studio_annotations", row)
        self.event(revision_id, "annotation.appended", row)
        return unpack(self.db.one("SELECT * FROM studio_annotations WHERE id=%s", (row["id"],)))

    @mutation
    def add_suggestions(self, revision_id, suggestions):
        rev = self.revision_view(revision_id)
        ep = self.get("studio_episodes", rev["episode_id"])
        valid = {s["id"] for s in rev["segments"]}
        if len(suggestions) > len(valid):
            raise ValueError("建议数量超过片段数")
        for proposal in suggestions:
            if proposal["segment_id"] not in valid:
                raise ValueError("建议不属于该分段版本")
            character = proposal.get("character_id")
            if character:
                self.character_in_series(character, ep["series_id"])
            ensure_no_secrets(proposal)
            version = self.db.one("SELECT COALESCE(MAX(version),0)+1 n FROM studio_suggestions WHERE revision_id=%s AND segment_id=%s", (revision_id, proposal["segment_id"]))["n"]
            self.db.insert("studio_suggestions", {"id": uid("suggest"), "version": version, "revision_id": revision_id,
                "segment_id": proposal["segment_id"], "character_id": character,
                "payload_json": canonical(proposal).decode()})
        self.event(revision_id, "suggestions.appended", {"count": len(suggestions)})
        return {"count": len(suggestions), "confirmed_annotations_changed": 0}

    @mutation
    def create_reference(self, revision_id, character_id, start_sample, end_sample, transcript, reviewer, reason):
        rev = self.revision_view(revision_id)
        ep = self.get("studio_episodes", rev["episode_id"])
        self.character_in_series(character_id, ep["series_id"])
        if ep["active_revision_id"] != revision_id:
            raise Conflict("请从当前分段版本选取参考")
        a, b = start_sample, end_sample
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= rev["payload"]["source_samples"] or not 48000 <= b-a <= 30*48000:
            raise ValueError("当前 CosyVoice 参考需为源 PCM 中连续 1–30 秒")
        overlapping = [s for s in rev["segments"] if s["start_sample"] < b and s["end_sample"] > a]
        if not overlapping or any(not s["annotation"] or s["annotation"]["status"] != "CONFIRMED"
                                  or s["annotation"]["character_id"] != character_id for s in overlapping):
            raise ValueError("参考区间包含未确认、重叠或其他角色的片段")
        # Do not use unknown material outside the extent of confirmed source segments.
        if a < min(s["start_sample"] for s in overlapping) or b > max(s["end_sample"] for s in overlapping):
            raise ValueError("参考范围超出已确认角色的讲话范围")
        from ..util import read_json
        bindings = rev["payload"]["bindings"]
        speech = read_json(Path(self.engine.artifact(bindings["speech"], ep["asset_sha512"])["path"]))
        vad = read_json(Path(self.engine.artifact(bindings["vad"], ep["asset_sha512"])["path"]))
        if vad.get("sample_rate") != 16000:
            raise ValueError("参考需要原始 16 kHz VAD 证据")
        detected = [(s["start_sample"], s["end_sample"]) for s in speech["segments"]]
        detected += [(s["start"] * 3, s["end"] * 3) for s in vad["intervals"]]
        for start, end in detected:
            cursor, stop = max(a, start), min(b, end)
            for segment in overlapping:
                if segment["start_sample"] <= cursor:
                    cursor = max(cursor, segment["end_sample"])
            if cursor < stop:
                raise ValueError("参考区间存在人工分段未覆盖的 ASR/VAD 讲话，请补录并确认角色")
        payload = {"asset_sha512": ep["asset_sha512"], "audio_artifact_id": rev["payload"]["bindings"]["audio"],
                   "speech_artifact_id": rev["payload"]["bindings"]["speech"], "start_sample": a, "end_sample": b,
                   "transcript": text(transcript, "参考音频实际英文原文"), "character_id": character_id,
                   "annotations": [{k: s["annotation"][k] for k in ("id", "revision_id", "segment_id", "version", "character_id", "status", "english_text", "chinese_text", "reviewer", "reason")} for s in overlapping],
                   "reviewer": text(reviewer, "候选提交者", 240), "reason": text(reason, "候选说明", 4000)}
        ref = self._snapshot("studio_references", {"id": uid("ref"), "character_id": character_id,
                             "revision_id": revision_id, "state": "CANDIDATE"}, payload)
        job = self.enqueue("REFERENCE", {"reference_id": ref["id"]}, ep["id"])
        self.event(ref["id"], "reference.created", {"job_id": job["id"]})
        return ref | {"job": job}

    @mutation
    def finish_reference(self, reference_id, artifacts):
        ref = self.get("studio_references", reference_id)
        if ref["artifacts"] is not None:
            raise Conflict("参考文件已经固定；创建新候选以更换文件")
        for value in artifacts.values():
            self.engine.artifact(value, ref["payload"]["asset_sha512"])
        self.db.query("UPDATE studio_references SET artifacts_json=%s WHERE id=%s", (canonical(artifacts).decode(), reference_id))
        self.event(reference_id, "reference.materialized", artifacts)

    @mutation
    def approve_reference(self, reference_id, reviewer, reason, checks):
        require_checks(checks, REFERENCE_CHECKS, reviewer, reason)
        ref = self.get("studio_references", reference_id)
        if ref["state"] != "CANDIDATE" or not ref["artifacts"]:
            raise ValueError("只能批准已生成试听文件的候选参考")
        for value in ref["artifacts"].values():
            self.engine.artifact(value, ref["payload"]["asset_sha512"])
        self.db.query("UPDATE studio_references SET state='APPROVED' WHERE id=%s", (reference_id,))
        self.event(reference_id, "reference.approved", {"reviewer": reviewer, "reason": reason, "checks": checks,
                                                      "artifacts": ref["artifacts"], "payload_sha512": ref["payload_sha512"]})
        return self.get("studio_references", reference_id)

    @mutation
    def disable_reference(self, reference_id, reviewer, reason):
        self.get("studio_references", reference_id)
        text(reviewer, "审核者", 240); text(reason, "停用原因", 4000)
        self.db.query("UPDATE studio_references SET state='DISABLED' WHERE id=%s", (reference_id,))
        self.event(reference_id, "reference.disabled", {"reviewer": reviewer, "reason": reason})
        return self.get("studio_references", reference_id)

    @mutation
    def create_profile(self, character_id, reference_id, label, seed=42):
        c = self.get("studio_characters", character_id)
        ref = self.get("studio_references", reference_id)
        if c["archived"] or ref["character_id"] != character_id or ref["state"] != "APPROVED":
            raise ValueError("声音配置必须使用该角色已批准且未停用的参考")
        if type(seed) is not int or not 0 <= seed <= 2**32-1:
            raise ValueError("seed 必须是 uint32")
        deployment = self.config.get("models", {}).get("N11/cosyvoice3_zero_shot", [])
        if len(deployment) != 1:
            raise ValueError("请先在私有 config.models 配置 N11/cosyvoice3_zero_shot")
        refs = {k: self.engine.artifact(v, ref["payload"]["asset_sha512"]) for k, v in ref["artifacts"].items()}
        payload = {"character_id": character_id, "reference_id": reference_id,
                   "reference_payload_sha512": ref["payload_sha512"], "references": refs,
                   "source_asset_sha512": ref["payload"]["asset_sha512"],
                   "strategy": "cosyvoice3_zero_shot", "models": deployment, "params": {"seed": seed}}
        version = self.db.one("SELECT COALESCE(MAX(version),0)+1 n FROM studio_profiles WHERE character_id=%s", (character_id,))["n"]
        profile = self._snapshot("studio_profiles", {"id": uid("voice"), "character_id": character_id,
            "reference_id": reference_id, "version": version, "label": text(label, "声音名称", 240), "state": "DRAFT"}, payload)
        self.event(character_id, "profile.created", {"profile_id": profile["id"], "version": version})
        return profile

    def usable_profile(self, profile_id, published=True):
        profile = self.get("studio_profiles", profile_id)
        if profile["state"] not in ({"PUBLISHED"} if published else {"DRAFT", "PUBLISHED"}):
            raise ValueError("声音配置尚未发布")
        ref = self.get("studio_references", profile["reference_id"])
        if ref["state"] != "APPROVED" or ref["payload_sha512"] != profile["payload"]["reference_payload_sha512"]:
            raise ValueError("声音配置所用参考已停用或绑定发生变化")
        for port, snapshot in profile["payload"]["references"].items():
            actual = self.engine.artifact(snapshot["artifact_id"], profile["payload"]["source_asset_sha512"])
            if actual["sha512"] != snapshot["sha512"] or actual["artifact_id"] != ref["artifacts"].get(port):
                raise ValueError("角色声音参考摘要或身份不一致")
        return profile

    @mutation
    def publish_profile(self, profile_id, preview_job_id, reviewer, reason, checks):
        require_checks(checks, VOICE_CHECKS, reviewer, reason)
        p = self.usable_profile(profile_id, published=False)
        job = self.get("studio_jobs", preview_job_id)
        if job["kind"] != "PREVIEW" or job["state"] != "SUCCEEDED" or job["payload"].get("profile_id") != profile_id:
            raise ValueError("请先生成并实际试听此声音版本的中文测试音频")
        result = job["result"]
        if not result or "audio" not in result or result.get("quality_status") == "FAIL":
            raise ValueError("测试音频未生成或存在关键质量错误")
        self.engine.artifact(result["audio"], p["payload"]["source_asset_sha512"])
        self.db.query("UPDATE studio_profiles SET state='PUBLISHED' WHERE id=%s", (profile_id,))
        self.db.query("UPDATE studio_characters SET active_profile_id=%s WHERE id=%s", (profile_id, p["character_id"]))
        self.event(p["character_id"], "profile.published", {"profile_id": profile_id, "preview_job_id": preview_job_id,
            "reviewer": reviewer, "reason": reason, "checks": checks, "profile_sha512": p["payload_sha512"]})
        return self.get("studio_profiles", profile_id)

    @mutation
    def create_plan(self, episode_id, expected_revision, reviewer, reason):
        ep = self.get("studio_episodes", episode_id)
        if not expected_revision or ep["active_revision_id"] != expected_revision:
            raise Conflict("请从当前分段版本生成计划")
        rev = self.revision_view(expected_revision)
        profiles, items = {}, []
        for s in rev["segments"]:
            a = s["annotation"]
            if not a or a["status"] != "CONFIRMED" or not a["chinese_text"].strip():
                raise ValueError("所有片段都必须确认角色并填写中文译文；未知/重叠片段需先处理")
            c = self.character_in_series(a["character_id"], ep["series_id"])
            if not c["active_profile_id"]:
                raise ValueError("角色尚无已发布声音: " + c["name"])
            p = self.usable_profile(c["active_profile_id"])
            profiles[c["id"]] = {"id": p["id"], "sha512": p["payload_sha512"], "payload": p["payload"]}
            items.append({"unit_id": s["id"], "start_sample": s["start_sample"], "end_sample": s["end_sample"],
                          "speaker_id": c["id"], "text": a["english_text"], "chinese_text": a["chinese_text"],
                          "annotation_id": a["id"], "annotation_version": a["version"]})
        if any(b["start_sample"] < a["end_sample"] for a, b in zip(items, items[1:])):
            raise ValueError("仍有重叠台词，不能冻结整集配音计划")
        render = self.config.get("studio", {}).get("render_settings", {})
        payload = {"episode_id": episode_id, "series_id": ep["series_id"], "asset_sha512": ep["asset_sha512"],
                   "revision_id": expected_revision, "revision_sha512": rev["payload_sha512"],
                   "bindings": rev["payload"]["bindings"], "items": items, "profiles": profiles,
                   "render_settings": render, "models": {k: self.config.get("models", {}).get(k, [])
                       for k in ("N12/audio_qa_asr", "N17/bilingual")},
                   "reviewer": text(reviewer, "审核者", 240), "reason": text(reason, "边界与文本复核说明", 4000)}
        plan = self._snapshot("studio_plans", {"id": uid("plan"), "episode_id": episode_id,
                            "revision_id": expected_revision, "state": "PENDING"}, payload)
        job = self.enqueue("MATERIALIZE", {"plan_id": plan["id"]}, episode_id)
        self.event(episode_id, "plan.frozen", {"plan_id": plan["id"], "sha512": plan["payload_sha512"]})
        return plan | {"job": job}

    @mutation
    def bind_plan(self, plan_id, bindings):
        plan = self.get("studio_plans", plan_id)
        if plan["bindings"] is not None:
            raise Conflict("计划材料已固定。需要新的材料版本时新建计划")
        self.db.query("UPDATE studio_plans SET bindings_json=%s,state='READY' WHERE id=%s", (canonical(bindings).decode(), plan_id))
        self.event(plan_id, "plan.materialized", bindings)

    def plan_stale(self, plan):
        ep = self.get("studio_episodes", plan["episode_id"])
        if ep["active_revision_id"] != plan["revision_id"]:
            return True
        rev = self.revision_view(plan["revision_id"])
        annotations = {s["id"]: s["annotation"]["id"] if s["annotation"] else None for s in rev["segments"]}
        return any(annotations.get(u["unit_id"]) != u["annotation_id"] for u in plan["payload"]["items"])

    @mutation
    def enqueue(self, kind, payload, episode_id=None, retry_of=None):
        if kind not in {"PREPARE", "REFERENCE", "PREVIEW", "MATERIALIZE", "GENERATE", "RENDER", "TRANSLATE", "SUGGEST"}:
            raise ValueError("未知任务类型")
        ensure_no_secrets(payload)
        row = {"id": uid("job"), "episode_id": episode_id, "kind": kind, "state": "QUEUED",
               "payload_json": canonical(payload).decode(), "retry_of": retry_of}
        self.db.insert("studio_jobs", row)
        self.event(row["id"], "job.queued", {"kind": kind, "retry_of": retry_of})
        return self.get("studio_jobs", row["id"])

    @mutation
    def prepare(self, episode_id):
        ep = self.get("studio_episodes", episode_id)
        return self.enqueue("PREPARE", {"episode_id": episode_id, "expected_revision": ep["active_revision_id"],
                            "source_url": ep["source_url"], "source_artifact_id": ep["source_artifact_id"],
                            "asset_sha512": ep["asset_sha512"], "settings": ep["settings"]}, episode_id)

    @mutation
    def preview(self, profile_id, chinese_text):
        self.usable_profile(profile_id, published=False)
        return self.enqueue("PREVIEW", {"profile_id": profile_id, "text": text(chinese_text, "测试中文", 2000)})

    @mutation
    def generate(self, plan_id, unit_ids=None):
        plan = self.get("studio_plans", plan_id)
        if plan["state"] != "READY":
            raise ValueError("计划尚未材料化，请查看计划任务")
        if self.plan_stale(plan):
            raise Conflict("台词标注/分段已变化。请新建计划，不将新内容混入旧计划")
        valid = {u["unit_id"] for u in plan["payload"]["items"]}
        if unit_ids is None:
            unit_ids = [u["unit_id"] for u in plan["payload"]["items"]]
        if not unit_ids or len(unit_ids) != len(set(unit_ids)) or set(unit_ids) - valid:
            raise ValueError("指定的生成片段不存在或重复")
        for p in plan["payload"]["profiles"].values():
            self.usable_profile(p["id"])
        return self.enqueue("GENERATE", {"plan_id": plan_id, "unit_ids": unit_ids}, plan["episode_id"])

    @mutation
    def select_clip(self, plan_id, job_id, unit_id, reviewer, reason, checks):
        require_checks(checks, CLIP_CHECKS, reviewer, reason)
        plan = self.get("studio_plans", plan_id)
        job = self.get("studio_jobs", job_id)
        if job["kind"] != "GENERATE" or job["payload"].get("plan_id") != plan_id or not job["result"]:
            raise ValueError("生成结果不属于该计划")
        clip = next((u for u in job["result"].get("units", []) if u["unit_id"] == unit_id), None)
        if clip is None or clip.get("quality_status") == "FAIL":
            raise ValueError("所选句子未成功生成或存在关键质量错误")
        for key in ("audio", "clip", "raw_clip", "raw_audio", "qa"):
            self.engine.artifact(clip[key], plan["payload"]["asset_sha512"])
        version = self.db.one("SELECT COALESCE(MAX(version),0)+1 n FROM studio_clip_selections WHERE plan_id=%s AND unit_id=%s", (plan_id, unit_id))["n"]
        row = {"id": uid("pick"), "plan_id": plan_id, "unit_id": unit_id, "job_id": job_id, "version": version,
               "payload_json": canonical(clip).decode(), "reviewer": reviewer, "reason": reason}
        self.db.insert("studio_clip_selections", row)
        self.event(plan_id, "clip.selected", {"selection_id": row["id"], "checks": checks, "job_id": job_id, "unit_id": unit_id})
        return self.get("studio_clip_selections", row["id"])

    def selections(self, plan_id):
        result = {}
        for row in self.rows("studio_clip_selections", "plan_id=%s", (plan_id,), "version,id"):
            result[row["unit_id"]] = row
        return result

    @mutation
    def render(self, plan_id):
        plan = self.get("studio_plans", plan_id)
        selections = self.selections(plan_id)
        if plan["state"] != "READY" or set(selections) != {u["unit_id"] for u in plan["payload"]["items"]}:
            raise ValueError("请先逐句确认并选用本计划的全部中文音频")
        # Snapshot selections NOW. A later click cannot mutate a queued render.
        snapshot = {unit: {k: row[k] for k in ("id", "plan_id", "unit_id", "job_id", "version", "payload", "reviewer", "reason")} for unit, row in selections.items()}
        return self.enqueue("RENDER", {"plan_id": plan_id, "selections": snapshot}, plan["episode_id"])

    @mutation
    def retry_job(self, job_id):
        job = self.get("studio_jobs", job_id)
        if job["state"] not in {"FAILED", "INTERRUPTED", "SUCCEEDED"}:
            raise ValueError("运行中/排队任务不能重试")
        if job["kind"] in {"REFERENCE", "MATERIALIZE", "PREPARE"} and job["state"] == "SUCCEEDED":
            raise ValueError("该任务已固定产物。请创建新参考/分段/计划版本，而不是覆盖")
        return self.enqueue(job["kind"], job["payload"], job["episode_id"], job_id)

    @mutation
    def finish_job(self, job_id, state, result=None, error=None):
        if state not in {"RUNNING", "SUCCEEDED", "FAILED", "INTERRUPTED"}:
            raise ValueError("Invalid job transition")
        old = self.get("studio_jobs", job_id)
        allowed = {"QUEUED": {"RUNNING"}, "RUNNING": {"SUCCEEDED", "FAILED", "INTERRUPTED"}}
        if state not in allowed.get(old["state"], set()):
            raise Conflict("任务状态已改变")
        clock = "started_at" if state == "RUNNING" else "finished_at"
        self.db.query(f"UPDATE studio_jobs SET state=%s,result_json=%s,error_text=%s,{clock}=UTC_TIMESTAMP(6) WHERE id=%s",
            (state, canonical(result).decode() if result is not None else None, error, job_id))
        self.event(job_id, "job." + state.lower(), {"error": error})

    @mutation
    def job_progress(self, job_id, result):
        if self.get("studio_jobs", job_id)["state"] != "RUNNING":
            raise Conflict("不能修改已结束任务")
        self.db.query("UPDATE studio_jobs SET result_json=%s WHERE id=%s", (canonical(result).decode(), job_id))

    @mutation
    def translate(self, revision_id):
        rev = self.revision_view(revision_id)
        ep = self.get("studio_episodes", rev["episode_id"])
        if ep["active_revision_id"] != revision_id:
            raise Conflict("分段已变化，请刷新")
        items = [{"unit_id": s["id"], "start_sample": s["start_sample"], "end_sample": s["end_sample"],
                  "text": s["annotation"]["english_text"] if s["annotation"] else s["text"],
                  "speaker_id": s["annotation"]["character_id"] if s["annotation"] else None} for s in rev["segments"]]
        return self.enqueue("TRANSLATE", {"revision_id": revision_id, "items": items}, ep["id"])

    @mutation
    def suggest(self, revision_id):
        rev = self.get("studio_revisions", revision_id)
        return self.enqueue("SUGGEST", {"revision_id": revision_id}, rev["episode_id"])

    @mutation
    def batch_annotate(self, revision_id, items):
        if not items or len(items) > 500 or len({x["segment_id"] for x in items}) != len(items):
            raise ValueError("批量标注需为 1–500 个不重复片段")
        return [self.annotate(revision_id, **item) for item in items]
