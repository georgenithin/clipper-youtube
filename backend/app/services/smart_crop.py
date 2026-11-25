"""Stage 6b: Optional MediaPipe face-aware horizontal crop estimation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def estimate_smart_crop_x_ratio(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    sample_count: int = 12,
) -> Optional[float]:
    """Sample frames in [start_sec, end_sec] and return median speaker face X ratio (0.0-1.0).

    Returns None if MediaPipe/OpenCV are not installed or no face is confidently detected,
    in which case the renderer gracefully falls back to center crop (0.5).
    """
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except Exception:
        logger.debug("MediaPipe/OpenCV not installed; using center crop.")
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    xs: list[float] = []
    try:
        duration = max(0.5, end_sec - start_sec)
        step = duration / max(sample_count, 1)
        face_det = mp.solutions.face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=0.55,
        )
        with face_det:
            for i in range(sample_count):
                t = start_sec + (i + 0.5) * step
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                res = face_det.process(rgb)
                if not res.detections:
                    continue
                # Pick the largest / most prominent face
                best = max(
                    res.detections,
                    key=lambda d: d.location_data.relative_bounding_box.width
                    * d.location_data.relative_bounding_box.height,
                )
                bb = best.location_data.relative_bounding_box
                cx = float(bb.xmin) + float(bb.width) / 2.0
                xs.append(max(0.15, min(0.85, cx)))
    except Exception as exc:
        logger.warning("Smart crop face detection failed (%s); falling back to center", exc)
        return None
    finally:
        cap.release()

    if not xs:
        return None
    xs.sort()
    mid = len(xs) // 2
    return float(xs[mid])
