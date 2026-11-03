"""Recognize the Pacific Rim training sequence from local Android 11 reference frames.

Run directly with Python, OpenCV and NumPy to replay the reference and control frames.
The images are test inputs. Device validation must call locate() on live screenshots.
"""
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1] / "evidence" / "pacific-rim"
REF = ROOT / "pic-trial"
STAGES = {
    "intro": ("api30-screen.png", [(185, 301, 1096, 374), (307, 238, 970, 262)]),
    "career": ("api30-after-continue.png", [(757, 120, 926, 164), (743, 232, 938, 266)]),
    "deploy": ("api30-career.png", [(852, 512, 974, 551), (235, 148, 622, 187)]),
    "block": ("api30-deploy.png", [(525, 485, 747, 550), (369, 398, 918, 474)]),
    "evade": ("api30-block.png", [(525, 471, 762, 535), (521, 380, 762, 455)]),
}
MATCH_THRESHOLD = 0.82


def gray(path):
    frame = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if frame is None:
        raise ValueError(f"Invalid screenshot: {path}")
    return frame


def locate(frame, stage):
    """Return the weaker anchor score and the first anchor's screen coordinates."""
    if frame is None or min(frame.shape[:2]) < 64:
        return 0.0, None
    filename, regions = STAGES[stage]
    reference = gray(REF / filename)
    height, width = frame.shape
    scale_x, scale_y = width / reference.shape[1], height / reference.shape[0]
    best = (0.0, None)
    # Support stretched viewports and aspect-preserving letterboxing.
    scales = {(scale_x, scale_y), (scale_x, scale_x), (scale_y, scale_y)}
    visible_rows = np.flatnonzero(frame.max(axis=1) > 8)
    reference_rows = np.flatnonzero(reference.max(axis=1) > 8)
    if len(visible_rows) and len(reference_rows):
        content_height = int(visible_rows[-1] - visible_rows[0] + 1)
        if height / 2 < content_height < height:
            reference_height = int(reference_rows[-1] - reference_rows[0] + 1)
            content_scale = content_height / reference_height
            scales.add((content_scale, content_scale))
    for sx, sy in sorted(scales):
        hits = []
        offsets = []
        for left, top, right, bottom in regions:
            patch = reference[top:bottom, left:right]
            template = cv2.resize(patch, None, fx=sx, fy=sy, interpolation=cv2.INTER_AREA)
            if template.shape[0] > height or template.shape[1] > width:
                break
            matches = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(matches)
            hits.append((score, x + template.shape[1] / 2, y + template.shape[0] / 2))
            offsets.append((x - left * sx, y - top * sy))
        if len(hits) != 2:
            continue
        # The two matches must imply the same viewport origin.
        if (abs(offsets[0][0] - offsets[1][0]) > max(8, width * .04)
                or abs(offsets[0][1] - offsets[1][1]) > max(8, height * .04)):
            continue
        confidence = min(hit[0] for hit in hits)
        if confidence > best[0]:
            best = confidence, (round(hits[0][1]), round(hits[0][2]))
    return best


if __name__ == "__main__":
    import json

    results = {}
    for expected, (filename, _) in STAGES.items():
        resized = cv2.resize(gray(REF / filename), (720, 480), interpolation=cv2.INTER_AREA)
        scores = {stage: round(locate(resized, stage)[0], 3) for stage in STAGES}
        matched = [stage for stage, score in scores.items() if score >= MATCH_THRESHOLD]
        assert matched == [expected], (filename, scores)
        results[expected] = scores
    for name, expected in [
        ("android11-after-load.png", ["career"]),
        ("android11-candidate-screen.png", ["intro"]),
        ("preview30.png", []),
    ]:
        frame = gray(ROOT / name)
        matched = [stage for stage in STAGES if locate(frame, stage)[0] >= MATCH_THRESHOLD]
        assert matched == expected, (name, matched)
        results[name] = matched
    print(json.dumps(results, indent=2))
