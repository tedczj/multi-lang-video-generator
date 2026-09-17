"""Stream source-frame OCR, retaining page-specific geometry and raw evidence."""

import argparse
import base64
import io
import json
import time
import urllib.request
from pathlib import Path

from .util import atomic_json, sha512


def scan(source, directory, deployment, stride=15, sample_frames=None):
    import av

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / "scan.json"
    binding = {
        "source_sha512": sha512(source),
        "deployment": deployment,
        "stride_frames": stride,
        "sample_frames": sample_frames,
    }
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved["binding"] != binding:
            raise ValueError("OCR resume input/model changed")
        return saved
    if stride < 1:
        raise ValueError("OCR stride must be positive")
    selected = set(sample_frames or [])
    rows = []
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
        for index, frame in enumerate(container.decode(video=0)):
            if (sample_frames is not None and index not in selected) or (
                sample_frames is None and index % stride
            ):
                continue
            image = frame.to_image()
            scaled_width = min(1280, width)
            image = image.resize((scaled_width, round(height * scaled_width / width)))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            data = buffer.getvalue()
            evidence = directory / f"{index:08d}.jpg"
            response = directory / f"{index:08d}.json"
            from .util import digest

            key = digest([binding, index])
            if response.exists():
                saved = json.loads(response.read_text())
                if saved["key"] != key or saved["image_sha512"] != sha512(evidence):
                    raise ValueError("OCR cached frame binding changed")
                raw = saved["response"]
            else:
                evidence.write_bytes(data)
                body = {
                    "file": base64.b64encode(data).decode(),
                    "fileType": 1,
                    "visualize": False,
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                    "useTextlineOrientation": False,
                }
                body.update(deployment.get("request_options", {}))
                request = urllib.request.Request(
                    deployment["endpoint"],
                    json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=180) as answer:
                    raw = json.load(answer)
                if raw.get("errorCode") != 0:
                    raise ValueError(f"OCR failed at source frame {index}: {raw}")
                atomic_json(
                    response,
                    {"key": key, "image_sha512": sha512(evidence), "response": raw},
                )
            lines = []
            for page in raw["result"]["ocrResults"]:
                r = page["prunedResult"]
                scores = r.get("rec_scores", [1.0] * len(r["rec_texts"]))
                for text, box, score in zip(
                    r["rec_texts"], r["rec_boxes"], scores, strict=True
                ):
                    if text.strip() and score >= 0.8:
                        x0, y0, x1, y1 = box
                        lines.append(
                            {
                                "text": text,
                                "box": [
                                    int(x0 * width / image.width),
                                    int(y0 * height / image.height),
                                    min(width, int(x1 * width / image.width) + 1),
                                    min(height, int(y1 * height / image.height) + 1),
                                ],
                                "score": score,
                            }
                        )
            rows.append(
                {
                    "frame": index,
                    "seconds": float(frame.time),
                    "lines": lines,
                    "evidence": str(evidence.resolve()),
                }
            )
            atomic_json(
                directory / "progress.json",
                {"frames_scanned": len(rows), "last_source_frame": index},
            )
            if len(rows) % 20 == 0:
                print("OCR", index, round(float(frame.time), 1), flush=True)
    result = {
        "binding": binding,
        "width": width,
        "height": height,
        "rows": rows,
        "quality_status": "REVIEW",
    }
    atomic_json(receipt, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--asr", type=Path)
    args = p.parse_args()
    cfg = json.loads(args.config.read_text())
    started = time.monotonic()
    samples = None
    if args.asr:
        import bisect

        import av

        with av.open(str(args.source)) as c:
            times = [float(f.time) for f in c.decode(video=0)]
        samples = set()
        for segment in json.loads(args.asr.read_text())["segments"]:
            start, end = segment["start"], segment["end"]
            points = [(start + end) / 2] + [
                start + 0.3 + 2 * i for i in range(int((end - start) / 2))
            ]
            for t in points:
                samples.add(min(len(times) - 1, bisect.bisect_left(times, t)))
        samples = sorted(samples)
    r = scan(
        args.source,
        args.output,
        cfg["models"]["N06/captions"][0],
        sample_frames=samples,
    )
    print(json.dumps({"frames": len(r["rows"]), "seconds": time.monotonic() - started}))


if __name__ == "__main__":
    main()
