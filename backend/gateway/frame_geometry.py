from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class LetterboxTransform:
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    resized_width: int
    resized_height: int
    scale: float
    scale_x: float
    scale_y: float
    pad_left: int
    pad_top: int
    pad_right: int
    pad_bottom: int

    def forward_points(self, points: np.ndarray) -> np.ndarray:
        values = np.asarray(points, dtype=np.float32).copy()
        values[..., 0] = values[..., 0] * self.scale_x + self.pad_left
        values[..., 1] = values[..., 1] * self.scale_y + self.pad_top
        return values

    def inverse_points(self, points: np.ndarray, *, clip: bool = True) -> np.ndarray:
        values = np.asarray(points, dtype=np.float32).copy()
        values[..., 0] = (values[..., 0] - self.pad_left) / self.scale_x
        values[..., 1] = (values[..., 1] - self.pad_top) / self.scale_y
        if clip:
            values[..., 0] = np.clip(values[..., 0], 0, self.source_width - 1)
            values[..., 1] = np.clip(values[..., 1], 0, self.source_height - 1)
        return values

    def public(self) -> dict:
        return asdict(self)


def letterbox_image(
    image: np.ndarray,
    target_width: int,
    target_height: int,
    *,
    fill: int | tuple[int, int, int] = 114,
    interpolation: int | None = None,
) -> tuple[np.ndarray, LetterboxTransform]:
    if image is None or image.ndim != 3 or image.shape[0] < 1 or image.shape[1] < 1:
        raise ValueError("letterbox source must be a non-empty HWC image")
    source_height, source_width = image.shape[:2]
    target_width = int(target_width)
    target_height = int(target_height)
    if target_width < 1 or target_height < 1:
        raise ValueError("letterbox target dimensions must be positive")

    scale = min(target_width / source_width, target_height / source_height)
    resized_width = max(1, min(target_width, int(round(source_width * scale))))
    resized_height = max(1, min(target_height, int(round(source_height * scale))))
    if interpolation is None:
        interpolation = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=interpolation)

    pad_left = (target_width - resized_width) // 2
    pad_top = (target_height - resized_height) // 2
    pad_right = target_width - resized_width - pad_left
    pad_bottom = target_height - resized_height - pad_top
    canvas = np.full((target_height, target_width, image.shape[2]), fill, dtype=image.dtype)
    canvas[pad_top:pad_top + resized_height, pad_left:pad_left + resized_width] = resized

    transform = LetterboxTransform(
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
        resized_width=resized_width,
        resized_height=resized_height,
        scale=float(scale),
        scale_x=float(resized_width / source_width),
        scale_y=float(resized_height / source_height),
        pad_left=pad_left,
        pad_top=pad_top,
        pad_right=pad_right,
        pad_bottom=pad_bottom,
    )
    return canvas, transform
