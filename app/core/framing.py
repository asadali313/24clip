"""Advanced subject tracking for vertical reframing.

Strategies per content type (detected cheaply from the same samples used for
tracking - no extra passes):

  talking      faces on >= 30% of samples: follow the most consistent face
               (matched by proximity across samples, not just "largest")
  action       gameplay/sports (few faces, lots of motion): follow the action
               - per-sample motion centroid blended with the scene's
               accumulated action-map peak and a gentle screen-center bias
               (gameplay action clusters near the middle of the screen)
  documentary  everything else: balanced face + motion + center

Scene changes are detected per sample via HSV-histogram distance; on a cut the
subject is re-identified and the camera state resets (the crop glides to the
new subject instead of dragging the old one in).

Camera follow: EMA + velocity clamp -> smooth glide, no jumps or shaking.
Low confidence -> safe centered crop. Faces are never intentionally cut:
the target is always clamped so the face box stays inside the crop window.

Everything runs on <= 24 samples per clip at 640px - i3-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

YUNET_PATH = config.MODELS_DIR / "yunet.onnx"
YOLO_ONNX = config.MODELS_DIR / "yolov10n.onnx"
YOLO_INPUT = 640
PERSON_CONF = 0.45
SAMPLE_TARGET_W = 640
MAX_SAMPLES = 24
EMA_ALPHA = 0.55
MAX_JUMP_FRACTION = 0.12
SCENE_CUT_HIST_THRESHOLD = 0.5
TALKING_FACE_RATIO = 0.30
ACTION_FACE_RATIO = 0.15
ACTION_MOTION_RATIO = 0.40
CONFIDENCE_MIN = 0.22
CENTER_BIAS_ACTION = 0.30           # gameplay action clusters near center
CENTER_BIAS_OTHER = 0.15


@dataclass
class SubjectTrack:
    xs: list[float] = field(default_factory=list)     # crop x per sample
    ts: list[float] = field(default_factory=list)     # clip-relative sample t
    cys: list[float] = field(default_factory=list)    # subject center y (px)
    face_hits: int = 0
    person_hits: int = 0
    motion_hits: int = 0
    samples: int = 0
    inside_ratio: float = -1.0     # fraction of samples with the subject
                                   # inside the crop window (QC signal)
    detector: str = "none"
    content_type: str = "documentary"                 # talking|action|documentary
    scene_cuts: int = 0

    @property
    def confidence(self) -> float:
        if not self.samples:
            return 0.0
        return min(1.0, (self.face_hits + 0.8 * self.person_hits
                         + 0.5 * self.motion_hits) / self.samples)


_DETECTOR_CACHE: dict = {}


def _yunet(frame_w: int, frame_h: int):
    key = ("yunet", frame_w, frame_h)
    if key not in _DETECTOR_CACHE:
        det = None
        if YUNET_PATH.exists() and cv2 is not None:
            try:
                det = cv2.FaceDetectorYN.create(
                    str(YUNET_PATH), "", (frame_w, frame_h),
                    score_threshold=0.55, nms_threshold=0.3, top_k=8)
            except Exception:
                det = None
        _DETECTOR_CACHE[key] = det
    return _DETECTOR_CACHE[key]


def _haar():
    if "haar" not in _DETECTOR_CACHE:
        try:
            c = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            _DETECTOR_CACHE["haar"] = c if not c.empty() else None
        except Exception:
            _DETECTOR_CACHE["haar"] = None
    return _DETECTOR_CACHE["haar"]


def _yolo():
    """YOLOv10n person detector via onnxruntime (NMS-free, 52ms/frame CPU).
    Detects real people AND game characters - both are framing subjects.
    Loaded once; silently unavailable when the model file is missing."""
    if "yolo" not in _DETECTOR_CACHE:
        sess = None
        if YOLO_ONNX.exists():
            try:
                import onnxruntime as ort
                sess = ort.InferenceSession(str(YOLO_ONNX),
                                            providers=["CPUExecutionProvider"])
            except Exception:
                sess = None
        _DETECTOR_CACHE["yolo"] = sess
    return _DETECTOR_CACHE["yolo"]


def _detect_persons(bgr, sess) -> list[dict]:
    """[{cx, cy, w, h, score, kind}] for person-class detections."""
    out: list[dict] = []
    if sess is None:
        return out
    h, w = bgr.shape[:2]
    # letterbox to 640x640 (no distortion), remember the mapping back
    r = min(YOLO_INPUT / w, YOLO_INPUT / h)
    nw, nh = max(1, int(w * r)), max(1, int(h * r))
    resized = cv2.resize(bgr, (nw, nh))
    canvas = np.full((YOLO_INPUT, YOLO_INPUT, 3), 114, np.uint8)
    px0, py0 = (YOLO_INPUT - nw) // 2, (YOLO_INPUT - nh) // 2
    canvas[py0:py0 + nh, px0:px0 + nw] = resized
    blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0,
                                 (YOLO_INPUT, YOLO_INPUT), swapRB=True)
    try:
        preds = sess.run(None, {"images": blob})[0][0]   # (300, 6)
    except Exception:
        return out
    for p in preds:
        conf, cls = float(p[4]), int(p[5])
        if cls != 0 or conf < PERSON_CONF:               # person only
            continue
        x1 = (float(p[0]) - px0) / r
        y1 = (float(p[1]) - py0) / r
        x2 = (float(p[2]) - px0) / r
        y2 = (float(p[3]) - py0) / r
        x1, x2 = max(0.0, min(x1, w)), max(0.0, min(x2, w))
        y1, y2 = max(0.0, min(y1, h)), max(0.0, min(y2, h))
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        out.append({"cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                    "w": x2 - x1, "h": y2 - y1, "score": conf,
                    "kind": "person"})
    return out


def _detect_faces(bgr, yunet, haar) -> list[dict]:
    """All credible faces in the frame: [{cx, cy, w, h, score}]."""
    out: list[dict] = []
    if yunet is not None:
        try:
            ok, faces = yunet.detect(bgr)
            if ok and faces is not None:
                for f in faces:
                    x, y, w, h, score = f[0], f[1], f[2], f[3], f[-1]
                    if score >= 0.55:
                        out.append({"cx": x + w / 2, "cy": y + h / 2,
                                    "w": w, "h": h, "score": float(score)})
        except Exception:
            pass
    if not out and haar is not None:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = haar.detectMultiScale(gray, 1.1, 4, minSize=(40, 40))
        for x, y, w, h in faces:
            out.append({"cx": x + w / 2, "cy": y + h / 2, "w": w, "h": h,
                        "score": 0.6})
    return out


def _motion_profile(gray, prev_gray):
    """Per-column action energy + x/y centroids. None when nearly static."""
    if prev_gray is None:
        return None, None, None
    diff = cv2.absdiff(gray, prev_gray)
    diff = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)[1]
    total = float(diff.sum())
    if total < 8000:
        return None, None, None
    cols = diff.sum(axis=0).astype(np.float64)
    s = cols.sum()
    if s <= 0:
        return None, None, None
    norm = cols / s
    xs = np.arange(len(norm), dtype=np.float64)
    centroid = float((norm * xs).sum())
    rows = diff.sum(axis=1).astype(np.float64)
    ry = (float((rows / rows.sum() * np.arange(len(rows))).sum())
          if rows.sum() > 0 else float(gray.shape[0]) / 2)
    return norm, centroid, ry


def _scene_cut(prev_small, small) -> bool:
    """HSV histogram distance between consecutive samples."""
    if prev_small is None:
        return False
    prev_hsv = cv2.cvtColor(prev_small, cv2.COLOR_BGR2HSV)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h_prev = cv2.calcHist([prev_hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    h_cur = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h_prev, h_prev)
    cv2.normalize(h_cur, h_cur)
    return float(cv2.compareHist(h_prev, h_cur, cv2.HISTCMP_CORREL)) \
        < SCENE_CUT_HIST_THRESHOLD


def _classify(face_ratio: float, motion_ratio: float,
              person_ratio: float = 0.0) -> str:
    if face_ratio >= TALKING_FACE_RATIO:
        return "talking"
    if person_ratio >= 0.25:
        # full-body person subjects behave like talking: follow them
        return "talking"
    if face_ratio < ACTION_FACE_RATIO and motion_ratio >= ACTION_MOTION_RATIO:
        return "action"
    return "documentary"


def _consistent_box(cands: list[dict], prev: dict | None) -> dict | None:
    """Nearest detection to the previously tracked one; else most prominent."""
    if not cands:
        return None
    if prev is not None:
        best, best_d = None, 1e9
        for f in cands:
            d = abs(f["cx"] - prev["cx"]) + 0.5 * abs(f["cy"] - prev["cy"])
            if d < best_d:
                best, best_d = f, d
        if best is not None and best_d < 0.5 * max(prev.get("w", 80), 80):
            return best
    return max(cands, key=lambda f: f["w"] * f["h"])


def _consistent_face(faces: list[dict], prev: dict | None) -> dict:
    """Face closest to the previously tracked one; else the most prominent."""
    if not faces:
        return {}
    if prev is not None:
        best, best_d = None, 1e9
        for f in faces:
            d = abs(f["cx"] - prev["cx"]) + 0.5 * abs(f["cy"] - prev["cy"])
            if d < best_d:
                best, best_d = f, d
        if best is not None and best_d < 0.5 * max(prev["w"], 80):
            return best
    return max(faces, key=lambda f: f["w"] * f["h"])


def track_subject(video: Path, start: float, end: float, src_w: int,
                  src_h: int, crop_w: int,
                  speech_intervals: list[tuple[float, float]] | None = None
                  ) -> SubjectTrack:
    """Sample the clip; classify content; return a smooth crop track.

    speech_intervals: clip-relative (start, end) times where words are being
    spoken (from the transcript). Used as a WHO-IS-SPEAKING signal: during
    active speech a visible face is the preferred subject; in non-speech
    spans persons/action dominate.
    """
    track = SubjectTrack()
    if cv2 is None:
        return track
    speech_intervals = speech_intervals or []

    def _speaking(rel_t: float) -> bool:
        return any(a <= rel_t <= b for a, b in speech_intervals)

    det_w = min(src_w, SAMPLE_TARGET_W)
    scale = det_w / src_w
    det_h = max(2, int(src_h * scale))
    yunet = _yunet(det_w, det_h)
    haar = None if yunet is not None else _haar()
    yolo = _yolo()
    track.detector = "yunet" if yunet is not None else \
        ("haar" if haar is not None else "none")
    if yolo is not None:
        track.detector += "+yolo"

    max_x = max(0.0, src_w - crop_w)
    step = max(2.0, (end - start) / MAX_SAMPLES)

    # per-sample raw data
    times: list[float] = []
    face_list: list[list[dict]] = []            # faces per sample
    person_list: list[list[dict]] = []          # persons per sample
    motion_centroids: list[float | None] = []   # full-res x per sample
    motion_centroid_ys: list[float | None] = []  # full-res y per sample
    action_maps: list[np.ndarray | None] = []   # per-column energy per sample
    cuts: list[bool] = []

    cap = cv2.VideoCapture(str(video))
    prev_small = None
    try:
        t = start
        while t < end:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            track.samples += 1
            times.append(t - start)
            small = cv2.resize(frame, (det_w, det_h))
            cuts.append(_scene_cut(prev_small, small))
            prev_small = small

            face_list.append(_detect_faces(small, yunet, haar))
            if face_list[-1]:
                track.face_hits += 1

            # person detection only when faces are absent (faces dominate
            # when present - saves the YOLO cost in talking videos)
            if face_list[-1]:
                person_list.append([])
            else:
                persons = _detect_persons(small, yolo)
                person_list.append(persons)
                if persons:
                    track.person_hits += 1

            # motion pair: two frames ~0.33 s apart INSIDE the sample, so the
            # diff centroid points at the subject's CURRENT position instead
            # of the midpoint between samples 2 s apart (which made the
            # camera trail fast subjects until they left the frame)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.33) * 1000.0)
            ok2, frame2 = cap.read()
            gray2 = cv2.cvtColor(
                cv2.resize(frame2, (det_w, det_h)) if ok2 else small,
                cv2.COLOR_BGR2GRAY)
            prof, centroid, centroid_y = _motion_profile(gray2, gray)
            action_maps.append(prof)
            if centroid is not None:
                track.motion_hits += 1
                motion_centroids.append(centroid / scale)
                motion_centroid_ys.append(centroid_y / scale)
            else:
                motion_centroids.append(None)
                motion_centroid_ys.append(None)
            t += step
    finally:
        cap.release()

    if not times:
        return track

    # ---- classify content type -------------------------------------------
    face_ratio = track.face_hits / track.samples
    person_ratio = track.person_hits / track.samples
    motion_ratio = track.motion_hits / track.samples
    track.content_type = _classify(face_ratio, motion_ratio, person_ratio)
    track.scene_cuts = sum(1 for c in cuts if c)

    # ---- per-sample target selection (WHO is speaking / WHAT is shown) ----
    scene_action_peak: float | None = None     # accumulated action-map mode
    acc_action: np.ndarray | None = None
    tracked_face: dict | None = None
    tracked_person: dict | None = None

    targets: list[float | None] = []
    targets_y: list[float | None] = []
    for i in range(track.samples):
        if i > 0 and cuts[i]:
            # scene change: reset subject identity and action accumulation
            tracked_face = None
            tracked_person = None
            acc_action = None
            scene_action_peak = None

        # accumulate the scene's action map (where action lives in general)
        if action_maps[i] is not None:
            acc_action = action_maps[i].copy() if acc_action is None \
                else acc_action + action_maps[i]

        speaking = _speaking(times[i])
        faces = face_list[i]
        persons = person_list[i]

        target: float | None = None
        target_y: float | None = None
        used_kind = "none"

        # Rule 2 - follow the speaker: during active speech a visible face
        # wins even in otherwise action-heavy content
        if faces and (speaking or face_ratio >= TALKING_FACE_RATIO
                      or track.content_type == "talking"):
            f = _consistent_face(faces, tracked_face)
            if f:
                tracked_face = f
                target = f["cx"] / scale            # full-res subject center
                target_y = f["cy"] / scale
                used_kind = "face"
        # Rule 1/3 - person subjects (full-body, sports, shows) outrank raw
        # motion: a detected person IS the subject
        if target is None and persons and person_ratio >= 0.2:
            p = _consistent_box(persons, tracked_person)
            if p:
                tracked_person = p
                target = p["cx"] / scale
                target_y = p["cy"] / scale
                used_kind = "person"
        # Rule 1 - action follows motion (gameplay: no faces/persons)
        if target is None and track.content_type == "action":
            if motion_centroids[i] is not None:
                target = motion_centroids[i]
                target_y = motion_centroid_ys[i]
            if acc_action is not None:
                s = acc_action.sum()
                if s > 0:
                    peak = float((acc_action / s
                                  * np.arange(len(acc_action))).sum())
                    peak_full = peak / scale
                    target = peak_full if target is None \
                        else 0.6 * target + 0.4 * peak_full
            if target is not None:
                target = (1 - CENTER_BIAS_ACTION) * target \
                    + CENTER_BIAS_ACTION * (src_w / 2)
        # documentary / balanced fallback
        if target is None:
            if faces and face_ratio > 0.05:
                f = _consistent_face(faces, tracked_face)
                if f:
                    tracked_face = f
                    target = f["cx"] / scale
                    target_y = f["cy"] / scale
                    used_kind = "face"
            elif persons:
                p = _consistent_box(persons, tracked_person)
                if p:
                    tracked_person = p
                    target = p["cx"] / scale
                    target_y = p["cy"] / scale
                    used_kind = "person"
            elif motion_centroids[i] is not None:
                target = motion_centroids[i]
                target_y = motion_centroid_ys[i]
                used_kind = "motion"

        targets.append(target)
        targets_y.append(target_y)

    # ---- confidence + fallback ---------------------------------------------
    hit_ratio = sum(1 for x in targets if x is not None) / len(targets)
    if hit_ratio < CONFIDENCE_MIN:
        track.xs = [max_x / 2]
        track.ts = [0.0]
        return track

    # ---- fill gaps (nearest real sample, backward then forward) -------------
    def fill(seq: list[float | None], seed: float) -> list[float]:
        out_f = list(seq)
        last = seed
        for i, x in enumerate(out_f):
            if x is not None:
                last = x
            else:
                out_f[i] = last
        nxt = out_f[-1]
        for i in range(len(out_f) - 1, -1, -1):
            if seq[i] is not None:
                nxt = seq[i]
            else:
                out_f[i] = nxt
        return out_f

    def smooth(values: list[float], lo: float, hi: float,
               vjump: float) -> list[float]:
        out_s: list[float] = []
        ema = values[0]
        cur = values[0]
        for i, x in enumerate(values):
            if i > 0 and cuts[i]:
                ema = x
                delta = max(-2 * vjump, min(2 * vjump, ema - cur))
            else:
                ema = ema + EMA_ALPHA * (x - ema)
                delta = max(-vjump, min(vjump, ema - cur))
            cur = max(lo, min(hi, cur + delta))
            out_s.append(cur)
        return out_s

    real = [x for x in targets if x is not None]
    seed = max_x / 2 if not real else real[0]
    filled = fill(targets, seed)
    filled = [max(0.0, min(max_x, x - crop_w / 2)) for x in filled]

    # smooth camera-follow with scene-aware reset. The velocity clamp is
    # per SECOND scaled by the sample interval: it guards against outlier
    # detections without throttling genuinely fast subjects.
    step_s = (times[1] - times[0]) if len(times) > 1 else 2.0
    vjump_x = src_w * MAX_JUMP_FRACTION * step_s
    track.xs = smooth(filled, 0.0, max_x, vjump_x)
    track.ts = times

    # QC signal: how often the subject center stays inside the crop window
    try:
        inside_count = 0
        subject_x = [x + crop_w / 2 for x in filled]     # center targets
        for i, sx in enumerate(subject_x):
            cx = track.xs[min(i, len(track.xs) - 1)] + crop_w / 2
            if abs(sx - cx) <= crop_w * 0.45:
                inside_count += 1
        track.inside_ratio = inside_count / max(1, len(subject_x))
    except Exception:
        track.inside_ratio = -1.0

    # subject center-y track (used by 16:9 renders of taller sources)
    real_y = [y for y in targets_y if y is not None]
    seed_y = (src_h / 2 if not real_y else real_y[0])
    filled_y = fill(targets_y, seed_y)
    filled_y = [max(0.0, min(float(src_h), y)) for y in filled_y]
    vjump_y = src_h * MAX_JUMP_FRACTION * step_s
    track.cys = smooth(filled_y, 0.0, float(src_h), vjump_y)
    return track
