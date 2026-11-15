"""Visual analysis - deliberately minimal.

Design rule (spec): only analyze visuals where it helps identify or frame a
SELECTED clip. There is no full-video scan anymore:
  - ranking uses `quick_visual_score`: 3 tiny frames per candidate (milliseconds)
  - framing uses `plan_framing` in renderer.py: face sampling only for the
    clips that are actually rendered
No full-resolution frame-by-frame processing happens anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 is a hard dep; guard anyway
    cv2 = None


@dataclass
class QuickVisual:
    """Cheap visual summary of a short time range (3 tiny frames)."""
    brightness: float = 0.5       # 0..1
    motion: float = 0.0           # 0..1 avg frame-to-frame change
    face_found: bool = False
    face_cx: float = 0.5          # normalized x center of best face
    frames: int = 0

    def to_dict(self) -> dict:
        return {
            "brightness": round(self.brightness, 3),
            "motion": round(self.motion, 3),
            "face_found": self.face_found,
            "face_cx": round(self.face_cx, 3),
            "frames": self.frames,
        }


_FACE_CACHE: dict = {}


def _face_cascade():
    if "cascade" not in _FACE_CACHE:
        _FACE_CACHE["checked"] = True
        try:
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            _FACE_CACHE["cascade"] = cascade if not cascade.empty() else None
        except Exception:
            _FACE_CACHE["cascade"] = None
    return _FACE_CACHE["cascade"]


class FrameSampler:
    """Opens a video once per instance; samples tiny frames cheaply."""

    def __init__(self, video, low_memory: bool = False):
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed")
        self.video = str(video)
        self.low_memory = low_memory

    # ------------------------------------------------------------------
    def quick_score(self, start: float, end: float,
                    want_face: bool = False) -> QuickVisual:
        """Sample ~3 small frames across [start, end]. Cost: milliseconds."""
        q = QuickVisual()
        cap = cv2.VideoCapture(self.video)
        try:
            dur = max(0.5, end - start)
            stamps = [start + dur * f for f in (0.2, 0.5, 0.85)]
            prev = None
            det = _face_cascade() if want_face else None
            for t in stamps:
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                q.frames += 1
                small = cv2.resize(frame, (160, 90))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                q.brightness += float(gray.mean()) / 255.0
                if prev is not None:
                    q.motion += cv2.absdiff(gray, prev).mean() / 255.0
                prev = gray
                if det is not None:
                    # detect on a half-size copy: 4x cheaper, plenty accurate
                    half = cv2.cvtColor(cv2.resize(frame, None, fx=0.5, fy=0.5),
                                        cv2.COLOR_BGR2GRAY)
                    faces = det.detectMultiScale(half, 1.1, 4, minSize=(40, 40))
                    if len(faces):
                        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                        q.face_found = True
                        q.face_cx = (x + w / 2) / half.shape[1]
            if q.frames:
                q.brightness /= q.frames
                q.motion /= max(1, q.frames - 1)
        finally:
            cap.release()
        return q

    # ------------------------------------------------------------------
    def extract_frame_jpg(self, t: float, out, width: int = 480):
        """Grab one preview frame (for results thumbnails)."""
        cap = cv2.VideoCapture(self.video)
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                return None
            h, w = frame.shape[:2]
            if w > width:
                frame = cv2.resize(frame, (width, int(h * width / w)))
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), frame)
            return out
        finally:
            cap.release()


def broll_suggestions(video, duration: float, exclude: tuple[float, float],
                      count: int = 3, low_memory: bool = False) -> list[dict]:
    """Suggest the most visually active moments OUTSIDE the clip's own span
    as potential B-roll. Honest local heuristic (motion sampling) - the user
    inserts them manually in the editor."""
    sampler = FrameSampler(video, low_memory)
    buckets = 12
    scored = []
    step = duration / buckets
    for i in range(buckets):
        a, b = i * step, (i + 1) * step
        if b <= exclude[0] + 2 or a >= exclude[1] - 2:
            continue
        if b - a < 1:
            continue
        q = sampler.quick_score(a, b)
        scored.append({"start": round(a, 1), "end": round(b, 1),
                       "motion": round(q.motion, 3),
                       "face": q.face_found})
    scored.sort(key=lambda d: d["motion"], reverse=True)
    return scored[:count]
