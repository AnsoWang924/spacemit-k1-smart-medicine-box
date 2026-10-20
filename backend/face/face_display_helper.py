#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mmap
import os
import signal
import struct
import time
from pathlib import Path

import cv2
import numpy as np

import face_live_imx219_smooth_register as face_mod


HEADER_SIZE = 8
WINDOW_NAME = 'Smart Medicine Face ID - IMX219'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Isolated Wayland display helper for face preview')
    parser.add_argument('--frame-file', required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--fps', type=float, default=8.0)
    parser.add_argument('--display', choices=['gst', 'gst-gl', 'opencv'], default='gst')
    return parser


def main() -> None:
    args = build_parser().parse_args()
    frame_size = args.width * args.height * 3
    expected_size = HEADER_SIZE + frame_size * 2
    frame_path = Path(args.frame_file)
    stop = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    fd = os.open(frame_path, os.O_RDONLY)
    shared = mmap.mmap(fd, expected_size, access=mmap.ACCESS_READ)
    os.close(fd)
    display_args = argparse.Namespace(
        display=args.display,
        display_width=args.width,
        display_height=args.height,
        target_fps=args.fps,
    )
    writer = None
    try:
        writer = face_mod.open_display(display_args)
        if writer is None:
            cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        transition = cv2.imread(str(Path(__file__).with_name('face_transition_mock.png')))
        if transition is None:
            transition = np.zeros((args.height, args.width, 3), dtype=np.uint8)
        elif transition.shape[:2] != (args.height, args.width):
            transition = cv2.resize(transition, (args.width, args.height), interpolation=cv2.INTER_AREA)
        if writer is not None:
            writer.write(transition)
        else:
            cv2.imshow(WINDOW_NAME, transition)
            cv2.waitKey(1)

        last_sequence = 0
        delay = 1.0 / max(1.0, args.fps * 2.0)
        while not stop:
            sequence = struct.unpack_from('<Q', shared, 0)[0]
            if sequence == 0 or sequence == last_sequence:
                time.sleep(delay)
                continue
            slot = sequence & 1
            offset = HEADER_SIZE + slot * frame_size
            frame = np.frombuffer(shared, dtype=np.uint8, count=frame_size, offset=offset).reshape(
                (args.height, args.width, 3)
            ).copy()
            if struct.unpack_from('<Q', shared, 0)[0] != sequence:
                continue
            if writer is not None:
                writer.write(frame)
            else:
                cv2.imshow(WINDOW_NAME, frame)
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
            last_sequence = sequence
    finally:
        shared.close()
        if writer is not None:
            writer.release()
        else:
            try:
                cv2.destroyWindow(WINDOW_NAME)
            except cv2.error:
                pass


if __name__ == '__main__':
    main()
