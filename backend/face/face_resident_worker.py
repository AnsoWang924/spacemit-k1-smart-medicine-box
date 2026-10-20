#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mmap
import os
import queue
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Lock, Thread

import cv2
import numpy as np

import face_live_imx219_smooth_register as face_mod


def load_face_bank_dir(face_bank_dir: str | None) -> dict[str, np.ndarray]:
    root = Path(face_bank_dir or str(face_mod.FACE_BANK))
    bank: dict[str, np.ndarray] = {}
    if not root.exists():
        return bank
    for path in sorted(root.glob('*.npy')):
        try:
            vec = np.load(path)
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
            bank[path.stem] = vec
        except Exception as exc:
            print(f'[resident] failed to load face bank {path}: {exc}', file=sys.stderr)
    return bank


class ResidentWorker:
    DISPLAY_HEADER_SIZE = 8

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.stop = False
        self.display_open = False
        self.display_process: subprocess.Popen | None = None
        self.display_map: mmap.mmap | None = None
        self.display_frame_path: Path | None = None
        self.display_frame_size = args.display_width * args.display_height * 3
        self.display_sequence = 0
        self.cap = None
        self.det = None
        self.rec = None
        self.params = face_mod.RuntimeParams(
            threshold=args.threshold,
            detect_interval=args.detect_interval,
            target_fps=args.target_fps,
            min_face_area_ratio=args.min_face_area_ratio,
            brightness=args.brightness,
            contrast=args.contrast,
            gamma=args.gamma,
            lock=Lock(),
        )
        self.status = 'booting'
        self.task_id = 'boot'
        self.current_command: dict = {'action': 'preview', 'taskId': 'boot'}
        self.last_command_mtime = 0.0
        self.control_path = Path(args.control_file)
        self.result_path = Path(args.result_json)
        self.users: list[str] = []
        self.preview_frames = 0
        self.last_report_ts = time.time()
        self.last_frame_time = time.time()
        self.preview_fps = 0.0
        self.last_results: list[face_mod.FaceResult] = []
        self.last_infer_ms = 0.0
        self.result_q: queue.Queue[tuple[str, float, list[face_mod.FaceResult], float]] = queue.Queue(maxsize=1)
        self.frame_q: queue.Queue[tuple[str, np.ndarray]] = queue.Queue(maxsize=1)
        self.infer_idle = Event()
        self.infer_idle.set()
        self.verify_state: dict = {}
        self.register_state: dict = {}
        self.worker_thread: Thread | None = None

    def write_result(self, payload: dict) -> None:
        data = {
            'ready': True,
            'status': self.status,
            'taskId': self.task_id,
            'ts': time.time(),
        }
        data.update(payload)
        face_mod.write_result_json(str(self.result_path), data)

    def set_command(self, command: dict) -> None:
        action = str(command.get('action') or 'preview')
        if not self.infer_idle.wait(timeout=3.0):
            raise RuntimeError('previous_face_inference_not_quiescent')
        for pending in (self.frame_q, self.result_q):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
        self.current_command = command
        self.task_id = str(command.get('taskId') or f'{action}-{int(time.time() * 1000)}')
        if action in {'preview', 'idle', 'cancel'}:
            self.status = 'idle'
            self.verify_state = {}
            self.register_state = {}
            self.close_display()
            self.close_camera()
            self.write_result({'message': 'resident models ready; camera idle'})
            return
        if action == 'verify':
            self.ensure_camera()
            self.ensure_display()
            threshold = float(command.get('threshold') or self.args.threshold)
            stable_frames = int(command.get('stable_frames') or 3)
            expected_name = str(command.get('expected_name') or '')
            face_bank_dir = str(command.get('face_bank_dir') or '')
            self.rec.face_bank = load_face_bank_dir(face_bank_dir)
            self.users = sorted(self.rec.face_bank)
            with self.params.lock:
                self.params.threshold = threshold
            self.verify_state = {
                'expected_name': expected_name,
                'threshold': threshold,
                'stable_frames': stable_frames,
                'deadline': time.monotonic() + float(command.get('timeout_seconds') or 30.0),
                'stable_count': 0,
                'best_name': '',
                'score': 0.0,
            }
            self.status = 'verifying'
            self.write_result({
                'expected_name': expected_name,
                'stable_required': stable_frames,
                'score': 0.0,
                'stable_count': 0,
            })
            return
        if action == 'register':
            member_id = str(command.get('member_id') or command.get('name') or '').strip()
            display_name = str(command.get('display_name') or member_id).strip()
            self.ensure_camera()
            self.ensure_display()
            samples = int(command.get('samples') or 8)
            sample_interval = float(command.get('sample_interval') or 1.0)
            timeout_seconds = float(command.get('timeout_seconds') or 60.0)
            self.register_state = {
                'member_id': member_id,
                'display_name': display_name,
                'samples_target': samples,
                'sample_interval': sample_interval,
                'deadline': time.monotonic() + timeout_seconds,
                'samples': [],
                'last_sample_ts': 0.0,
                'last_captured_count': 0,
                'last_capture_time': 0.0,
                'stable_count': 0,
                'capture_started': False,
                'last_quality': face_mod.RegisterQuality(False, '请正对摄像头', None, None),
            }
            self.status = 'registering'
            self.write_result({'memberId': member_id, 'name': display_name, 'capturedSamples': 0, 'targetSamples': samples})
            return
        self.status = 'idle'
        self.close_display()
        self.close_camera()
        self.write_result({'message': f'unknown action {action}, fallback idle'})

    def ensure_camera(self) -> None:
        if self.cap is None:
            self.cap = face_mod.open_camera(self.args)

    def close_camera(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            finally:
                self.cap = None

    def ensure_display(self) -> None:
        if self.args.display == 'none' or self.display_open:
            return
        helper = Path(__file__).with_name('face_display_helper.py')
        if not helper.is_file():
            raise RuntimeError(f'display_helper_missing:{helper}')
        frame_path = self.result_path.parent / f'face-display-{os.getpid()}-{time.monotonic_ns()}.bgr'
        total_size = self.DISPLAY_HEADER_SIZE + self.display_frame_size * 2
        mapping = None
        process = None
        try:
            with frame_path.open('w+b') as frame_file:
                frame_file.truncate(total_size)
                mapping = mmap.mmap(frame_file.fileno(), total_size, access=mmap.ACCESS_WRITE)
            process = subprocess.Popen([
                sys.executable, '-u', str(helper),
                '--frame-file', str(frame_path),
                '--width', str(self.args.display_width),
                '--height', str(self.args.display_height),
                '--fps', str(self.args.target_fps),
                '--display', str(self.args.display),
            ], cwd=str(helper.parent), env={**os.environ}, start_new_session=True)
        except Exception as exc:
            if mapping is not None:
                mapping.close()
            try:
                frame_path.unlink()
            except FileNotFoundError:
                pass
            raise RuntimeError(f'display_unavailable:{exc}') from exc
        self.display_process = process
        self.display_map = mapping
        self.display_frame_path = frame_path
        self.display_sequence = 0
        self.display_open = True

    def show_frame(self, frame: np.ndarray) -> None:
        if self.args.display == 'none' or not self.display_open:
            return
        process = self.display_process
        mapping = self.display_map
        if process is None or mapping is None:
            raise RuntimeError('display_helper_not_initialized')
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f'display_helper_exited:returncode={return_code}')
        if frame.shape[:2] != (self.args.display_height, self.args.display_width):
            frame = cv2.resize(frame, (self.args.display_width, self.args.display_height), interpolation=cv2.INTER_AREA)
        frame = np.ascontiguousarray(frame, dtype=np.uint8)
        self.display_sequence += 1
        slot = self.display_sequence & 1
        offset = self.DISPLAY_HEADER_SIZE + slot * self.display_frame_size
        mapping[offset:offset + self.display_frame_size] = frame.reshape(-1).tobytes()
        struct.pack_into('<Q', mapping, 0, self.display_sequence)

    def close_display(self) -> None:
        if not self.display_open and self.display_process is None and self.display_map is None:
            return
        process = self.display_process
        mapping = self.display_map
        frame_path = self.display_frame_path
        self.display_process = None
        self.display_map = None
        self.display_frame_path = None
        self.display_open = False
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    print(f'[resident] display helper pid={process.pid} did not exit after SIGKILL', file=sys.stderr)
            except Exception as exc:
                print(f'[resident] display helper stop failed: {exc}', file=sys.stderr)
        if mapping is not None:
            try:
                mapping.close()
            except Exception as exc:
                print(f'[resident] display mmap close failed: {exc}', file=sys.stderr)
        if frame_path is not None:
            try:
                frame_path.unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:
                print(f'[resident] display frame cleanup failed: {exc}', file=sys.stderr)

    def expire_active_task(self) -> bool:
        now = time.monotonic()
        if self.status == 'verifying' and now >= float(self.verify_state.get('deadline') or 0.0):
            self.status = 'verify_timeout'
            self.write_result({
                'name': '',
                'score': 0.0,
                'stable_count': 0,
                'stable_required': int(self.verify_state.get('stable_frames') or 3),
            })
        elif self.status == 'registering' and now >= float(self.register_state.get('deadline') or 0.0):
            state = self.register_state
            self.status = 'register_failed'
            self.write_result({
                'memberId': state.get('member_id', ''),
                'name': state.get('display_name', ''),
                'capturedSamples': len(state.get('samples', [])),
                'targetSamples': state.get('samples_target', 0),
            })
        else:
            return False
        self.close_display()
        self.close_camera()
        return True

    def poll_command(self) -> None:
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        self.control_path.touch(exist_ok=True)
        mtime = self.control_path.stat().st_mtime
        if mtime <= self.last_command_mtime:
            return
        self.last_command_mtime = mtime
        try:
            raw = self.control_path.read_text(encoding='utf-8').strip()
            if not raw:
                return
            payload = json.loads(raw)
        except Exception as exc:
            print(f'[resident] bad command: {exc}', file=sys.stderr)
            return
        try:
            self.set_command(payload)
        except Exception as exc:
            self.status = 'camera_error'
            self.close_display()
            self.close_camera()
            message = str(exc)
            display_failed = message.startswith('display_unavailable:')
            self.write_result({
                'code': 'display_unavailable' if display_failed else 'camera_unavailable',
                'stage': 'display' if display_failed else 'camera',
                'message': message,
            })

    def infer_worker(self) -> None:
        while not self.stop:
            try:
                task_id, frame = self.frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.infer_idle.clear()
            try:
                with self.params.lock:
                    threshold = self.params.threshold
                    min_face_area_ratio = self.params.min_face_area_ratio
                expected_name = str(self.verify_state.get('expected_name') or '') if self.status == 'verifying' else ''
                results = face_mod.recognize_faces(
                    self.det,
                    self.rec,
                    frame,
                    threshold,
                    min_face_area_ratio,
                    True,
                    True,
                    getattr(self.args, 'center_tolerance', 0.08),
                )
                infer_ms = 0.0
                self.result_q.put_nowait((task_id, time.time(), results, infer_ms))
            except queue.Full:
                pass
            except Exception as exc:
                print(f'[resident] infer worker error: {exc}', file=sys.stderr)
            finally:
                self.infer_idle.set()

    def bootstrap(self) -> None:
        face_mod.require_official_package()
        face_mod.apply_cpu_affinity(getattr(self.args, 'cpu_affinity', face_mod.DEFAULT_CPU_AFFINITY))
        self.det, self.rec = face_mod.load_models(self.args)
        if getattr(self.rec, '_lazy_provider', None) == 'spacemit' and not getattr(self.rec, '_lazy_loaded', False):
            self.rec.sess = self.rec._lazy_make_session(self.rec._lazy_model_path, self.rec._lazy_provider)
            self.rec._lazy_loaded = True
        for session in (self.det.sess, self.rec.sess):
            input_meta = session.get_inputs()[0]
            shape = [int(value) if isinstance(value, int) and value > 0 else 1 for value in input_meta.shape]
            session.run(None, {input_meta.name: np.zeros(shape, dtype=np.float32)})
        # Official EP initialization may widen native-thread affinity.  Enforce
        # the declared CPU after both sessions and their threads exist.
        face_mod.apply_cpu_affinity(getattr(self.args, 'cpu_affinity', face_mod.DEFAULT_CPU_AFFINITY))
        self.users = sorted(self.rec.face_bank)
        self.worker_thread = Thread(target=self.infer_worker, daemon=True)
        self.worker_thread.start()
        self.status = 'idle'
        self.write_result({'message': 'resident face models ready', 'modelResident': True, 'cameraState': 'idle'})

    def handle_verify_results(self, results: list[face_mod.FaceResult]) -> None:
        state = self.verify_state
        expected_name = str(state.get('expected_name') or '')
        stable_frames = int(state.get('stable_frames') or 3)
        matched = face_mod.best_success_result(results, expected_name)
        if matched is not None:
            if state.get('best_name') == matched.name:
                state['stable_count'] = int(state.get('stable_count') or 0) + 1
            else:
                state['stable_count'] = 1
            state['best_name'] = matched.name
            state['score'] = float(matched.score)
            payload = {
                'status': 'recognized' if state['stable_count'] >= stable_frames else 'verifying',
                'name': matched.name,
                'score': matched.score,
                'stable_count': state['stable_count'],
                'stable_required': stable_frames,
                'box': list(matched.box),
            }
            if state['stable_count'] >= stable_frames:
                self.status = 'recognized'
                self.close_camera()
            self.write_result(payload)
            return
        if time.monotonic() >= float(state.get('deadline') or 0.0):
            top = max(results, key=lambda item: item.score) if results else None
            self.status = 'no_match' if top is not None else 'no_face'
            self.close_camera()
            self.write_result({
                'name': top.name if top else '',
                'score': top.score if top else 0.0,
                'stable_count': 0,
                'stable_required': stable_frames,
            })
            return
        self.write_result({'status': 'verifying' if results else 'preview', 'score': 0.0, 'stable_count': 0, 'stable_required': stable_frames})

    def handle_register_frame(self, frame: np.ndarray) -> None:
        state = self.register_state
        now = time.monotonic()
        if now >= float(state.get('deadline') or 0.0):
            self.status = 'register_failed'
            self.close_camera()
            self.write_result({'memberId': state.get('member_id', ''), 'name': state.get('display_name', ''), 'capturedSamples': len(state.get('samples', [])), 'targetSamples': state.get('samples_target', 0)})
            return
        infer_start = time.perf_counter()
        with face_mod.INFERENCE_RIGHT.acquire('face-det'):
            face_imgs, boxes = self.det.infer_face(frame)
        infer_ms = (time.perf_counter() - infer_start) * 1000.0
        quality = face_mod.evaluate_register_quality(frame, boxes, self.args)
        if quality.ok:
            state['stable_count'] = int(state.get('stable_count') or 0) + 1
        else:
            state['stable_count'] = 0
            state['capture_started'] = False
        if not state.get('capture_started') and state['stable_count'] >= int(self.args.stable_frames):
            state['capture_started'] = True
            state['last_sample_ts'] = 0.0
        if (
            quality.ok
            and state.get('capture_started')
            and quality.face_index is not None
            and face_imgs
            and now - float(state.get('last_sample_ts') or 0.0) >= float(state.get('sample_interval') or 1.0)
        ):
            idx = int(quality.face_index)
            face_img = face_imgs[idx]
            with face_mod.INFERENCE_RIGHT.acquire('face-rec', priority=True):
                if getattr(self.rec, '_lazy_provider', None) == 'spacemit' and not getattr(self.rec, '_lazy_loaded', False):
                    self.rec.sess = self.rec._lazy_make_session(self.rec._lazy_model_path, self.rec._lazy_provider)
                    self.rec._lazy_loaded = True
                vec = self.rec.infer(face_img)
            state['samples'].append(vec)
            state['last_sample_ts'] = now
            state['last_captured_count'] = len(state['samples'])
            state['last_capture_time'] = now
            if len(state['samples']) >= int(state.get('samples_target') or 0):
                avg = np.mean(state['samples'], axis=0)
                avg = avg / (np.linalg.norm(avg) + 1e-12)
                out_path = Path(face_mod.FACE_BANK) / f"{state['member_id']}.npy"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                np.save(out_path, avg)
                self.rec.face_bank = load_face_bank_dir(str(face_mod.FACE_BANK))
                self.users = sorted(self.rec.face_bank)
                self.status = 'registered'
                self.close_camera()
                self.write_result({'memberId': state['member_id'], 'name': state['display_name'], 'capturedSamples': len(state['samples']), 'targetSamples': state['samples_target']})
                return
        state['last_quality'] = quality
        self.last_infer_ms = infer_ms
        self.write_result({'status': 'registering', 'memberId': state.get('member_id', ''), 'name': state.get('display_name', ''), 'capturedSamples': len(state.get('samples', [])), 'targetSamples': state.get('samples_target', 0)})

    def annotate_preview(self, frame: np.ndarray) -> np.ndarray:
        status = self.status
        if self.status in {'verifying', 'recognized', 'no_match', 'no_face', 'verify_timeout'}:
            return face_mod.annotate_live_fast(frame, self.last_results, self.preview_fps, self.users, self.args, status, self.params, 0.0, self.last_infer_ms)
        if self.status in {'registering', 'registered', 'register_failed'}:
            state = self.register_state
            # The shared renderer expects the active display name on args.
            self.args.name = str(state.get('display_name') or getattr(self.args, 'name', ''))
            quality = state.get('last_quality') or face_mod.RegisterQuality(False, '请正对摄像头', None, None)
            return face_mod.annotate_register_fast(
                frame,
                self.args,
                quality,
                int(state.get('stable_count') or 0),
                len(state.get('samples', [])),
                int(state.get('samples_target') or 0),
                self.last_infer_ms,
                int(state.get('last_captured_count') or 0),
                time.monotonic() - float(state.get('last_capture_time') or 0.0) if state.get('last_capture_time') else 999.0,
            )
        return face_mod.annotate_live_fast(frame, [], self.preview_fps, self.users, self.args, 'preview', self.params, 0.0, 0.0)

    def loop(self) -> None:
        while not self.stop:
            self.poll_command()
            if self.status not in {'verifying', 'registering'}:
                time.sleep(0.05)
                continue
            if self.expire_active_task():
                continue
            try:
                self.ensure_camera()
            except Exception as exc:
                self.status = 'camera_error'
                self.close_display()
                self.close_camera()
                self.write_result({'code': 'camera_unavailable', 'stage': 'camera', 'message': str(exc)})
                time.sleep(0.2)
                continue
            frame = face_mod.read_bgr(self.cap, self.args.width, self.args.height)
            if frame is None:
                time.sleep(0.02)
                continue
            if getattr(self.args, 'mirror', True):
                frame = cv2.flip(frame, 1)
            frame = face_mod.enhance_frame(frame, self.args.brightness, self.args.contrast, self.args.gamma)
            now = time.time()
            dt = now - self.last_frame_time
            self.last_frame_time = now
            if dt > 0:
                self.preview_fps = self.preview_fps * 0.85 + (1.0 / dt) * 0.15
            if self.status == 'verifying':
                try:
                    self.frame_q.put_nowait((self.task_id, frame.copy()))
                except queue.Full:
                    pass
                try:
                    result_task_id, _ts, results, infer_ms = self.result_q.get_nowait()
                    if result_task_id == self.task_id:
                        self.last_results, self.last_infer_ms = results, infer_ms
                        self.handle_verify_results(self.last_results)
                except queue.Empty:
                    self.expire_active_task()
            elif self.status == 'registering':
                self.handle_register_frame(frame.copy())
            if self.status not in {'verifying', 'registering'}:
                self.close_display()
                continue
            annotated = self.annotate_preview(frame)
            try:
                self.show_frame(annotated)
            except Exception as exc:
                print(f'[resident] display write failed: {exc}', file=sys.stderr)
                self.status = 'camera_error'
                self.close_display()
                self.close_camera()
                self.write_result({'code': 'display_unavailable', 'stage': 'display', 'message': str(exc)})
            if now - self.last_report_ts >= 1.0:
                print(f'[resident] status={self.status} preview_fps={self.preview_fps:.2f} users={len(self.users)}')
                self.last_report_ts = now
            delay = (1.0 / max(1.0, self.args.target_fps))
            time.sleep(max(0.0, delay * 0.2))

    def close(self) -> None:
        self.stop = True
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=2.0)
        if self.cap is not None:
            self.close_camera()
        self.close_display()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Smart Medicine face resident worker')
    parser.add_argument('--camera-json', default=face_mod.IMX219_JSON)
    parser.add_argument('--width', type=int, default=face_mod.CAM_W)
    parser.add_argument('--height', type=int, default=face_mod.CAM_H)
    parser.add_argument('--display-width', type=int, default=face_mod.DISPLAY_W)
    parser.add_argument('--display-height', type=int, default=face_mod.DISPLAY_H)
    parser.add_argument('--display', choices=['gst', 'gst-gl', 'opencv', 'none'], default='gst')
    parser.add_argument('--threshold', type=float, default=face_mod.DEFAULT_THRESHOLD)
    parser.add_argument('--det-conf', type=float, default=face_mod.DEFAULT_DET_CONF)
    parser.add_argument('--target-fps', type=float, default=10.0)
    parser.add_argument('--provider', choices=['spacemit', 'cpu'], default='spacemit')
    parser.add_argument('--rec-provider', choices=['spacemit', 'cpu'], default='spacemit')
    parser.add_argument('--ep-threads', type=int, default=1)
    parser.add_argument('--cpu-affinity', default=face_mod.DEFAULT_CPU_AFFINITY)
    parser.add_argument('--min-face-area-ratio', type=float, default=face_mod.DEFAULT_MIN_FACE_AREA_RATIO)
    parser.add_argument('--max-temp-c', type=float, default=face_mod.DEFAULT_MAX_TEMP_C)
    parser.add_argument('--thermal-check-interval', type=float, default=2.0)
    parser.add_argument('--brightness', type=float, default=0.0)
    parser.add_argument('--contrast', type=float, default=1.0)
    parser.add_argument('--gamma', type=float, default=1.0)
    parser.add_argument('--mirror', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--detect-interval', type=float, default=0.0)
    parser.add_argument('--center-tolerance', type=float, default=0.08)
    parser.add_argument('--stable-frames', type=int, default=2)
    parser.add_argument('--register-min-face-area-ratio', type=float, default=0.035)
    parser.add_argument('--register-max-face-area-ratio', type=float, default=0.30)
    parser.add_argument('--sharpness-threshold', type=float, default=45.0)
    parser.add_argument('--face-brightness-min', type=float, default=45.0)
    parser.add_argument('--face-brightness-max', type=float, default=235.0)
    parser.add_argument('--control-file', required=True)
    parser.add_argument('--result-json', required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    worker = ResidentWorker(args)

    def _stop(_signum, _frame):
        worker.stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        worker.bootstrap()
        worker.loop()
    finally:
        worker.close()


if __name__ == '__main__':
    main()
