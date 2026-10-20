#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path


class FaceV2Service:
    def __init__(self, project_root: str | Path, camera_lock: threading.Lock | None = None):
        self.root = Path(project_root).resolve()
        self.run_script = self.root / 'run_face.sh'
        self.face_bank_root = Path(os.environ.get("FACE_BANK_DIR", str(self.root.parent.parent / "data" / "faces")))
        self.index_path = self.face_bank_root / 'member_index.json'
        self.detector_model = 'yolov5n-face_320_cut.q.onnx'
        self.recognizer_model = 'arcface_mobilefacenet_cut.q.onnx'
        self.provider = 'spacemit'
        self.ep_threads = 1
        self.cpu_affinity = [3]
        self.target_fps = 10
        self.camera_name = 'imx219'
        self.preview_pages = set()
        self.preview_display = 'gst'
        self.preview_process_timeout = 8.0
        self._lock = threading.RLock()
        self._camera_lock = camera_lock or threading.Lock()
        self._sessions: dict[str, dict] = {}

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _save_index(self, data: dict) -> None:
        self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def _ready_data(self, session_id: str) -> dict:
        with self._lock:
            item = self._sessions.get(session_id) or {}
            preview_running = self._preview_alive(item)
        return {
            'ready': True,
            'module': 'face',
            'sessionId': session_id,
            'provider': self.provider,
            'epThreads': self.ep_threads,
            'cpuAffinity': self.cpu_affinity,
            'camera': self.camera_name,
            'detectorModel': self.detector_model,
            'recognizerModel': self.recognizer_model,
            'deployedRoot': str(self.root),
            'previewResident': True,
            'previewRunning': preview_running,
        }

    def preload(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get('sessionId') or '').strip()
        page = str(payload.get('page') or 'face').strip()
        if not session_id:
            return 400, None, {'code': 'invalid_request', 'message': 'sessionId is required', 'retryable': False}
        if not self.run_script.exists():
            return 503, None, {'code': 'model_unavailable', 'message': 'face module not deployed', 'retryable': True}
        with self._lock:
            session = self._sessions.get(session_id) or {}
            self._sessions[session_id] = {
                'ready': True,
                'page': page,
                'updatedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'previewProc': session.get('previewProc'),
                'previewStartedAt': session.get('previewStartedAt'),
            }
            # Formal UI preload only marks the face module ready; do not auto-start
            # the legacy full-screen display-menu preview.
        return 200, self._ready_data(session_id), None

    def status(self, session_id: str = '') -> tuple[int, dict, None]:
        with self._lock:
            item = self._sessions.get(session_id) if session_id else None
            ready = bool(item and item.get('ready')) or bool(self.run_script.exists())
            preview_running = self._preview_alive(item)
        return 200, {
            'module': 'face',
            'ready': ready,
            'busy': self._camera_lock.locked(),
            'sessionId': session_id or None,
            'provider': self.provider,
            'epThreads': self.ep_threads,
            'cpuAffinity': self.cpu_affinity,
            'camera': self.camera_name,
            'cameraState': 'busy' if self._camera_lock.locked() else 'ready',
            'previewRunning': preview_running,
            'detectorModel': self.detector_model,
            'recognizerModel': self.recognizer_model,
            'deployedRoot': str(self.root),
        }, None

    def release(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get('sessionId') or '').strip()
        if not session_id:
            return 400, None, {'code': 'invalid_request', 'message': 'sessionId is required', 'retryable': False}
        with self._lock:
            self._stop_preview_locked(session_id)
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
        return 200, {
            'released': True,
            'alreadyReleased': not existed,
            'module': 'face',
            'sessionId': session_id,
        }, None

    def _face_path(self, face_key: str) -> Path | None:
        key = str(face_key or '').strip()
        if not key or Path(key).name != key or key in {'.', '..'}:
            return None
        return self.face_bank_root / f'{key}.npy'

    @staticmethod
    def _face_key_from_name(name: str) -> str:
        # Keep display names intact in the index while making the storage key safe.
        key = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(name or ''))
        key = key.replace('..', '').strip().strip('.')
        return key

    def _affinity_command(self, cmd: list[str]) -> list[str]:
        mask = ','.join(str(core) for core in self.cpu_affinity)
        return ['taskset', '-c', mask, *cmd]

    def _preview_command(self) -> list[str]:
        return [
            'bash', str(self.run_script), 'display-menu',
            '--display', self.preview_display,
            '--provider', self.provider,
            '--ep-threads', str(self.ep_threads),
            '--target-fps', str(self.target_fps),
            '--detect-interval', '0.0',
            '--display-width', '1024',
            '--display-height', '600',
        ]

    def _preview_alive(self, item: dict | None) -> bool:
        proc = (item or {}).get('previewProc')
        return proc is not None and proc.poll() is None

    def _stop_preview_locked(self, session_id: str) -> None:
        item = self._sessions.get(session_id) or {}
        proc = item.get('previewProc')
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=self.preview_process_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        item['previewProc'] = None
        item['previewStartedAt'] = None

    def _start_preview_locked(self, session_id: str, page: str) -> None:
        if page not in self.preview_pages or not self.run_script.exists():
            return
        item = self._sessions.setdefault(session_id, {})
        if self._preview_alive(item):
            return
        if self._camera_lock.locked():
            return
        proc = subprocess.Popen(
            self._affinity_command(self._preview_command()),
            cwd=str(self.root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        item['previewProc'] = proc
        item['previewStartedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')

    def remove(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        member_id = str(payload.get('memberId') or '').strip()
        if not member_id:
            return 400, None, {'code': 'invalid_request', 'message': 'memberId is required', 'retryable': False}
        if not self._camera_lock.acquire(blocking=False):
            return 423, None, {'code': 'camera_busy', 'message': 'IMX219 is busy', 'retryable': True}
        try:
            with self._lock:
                index = self._load_index()
                member = index.pop(member_id, None) or {}
                removed_file = False
                # New records use the real name; legacy records used memberId.
                face_keys = [
                    self._face_key_from_name(str(member.get('name') or '')),
                    str(member.get('faceKey') or ''),
                    member_id,
                ]
                for face_key in dict.fromkeys(face_keys):
                    face_path = self._face_path(face_key)
                    if face_path and face_path.exists():
                        face_path.unlink()
                        removed_file = True
                self._save_index(index)
            return 200, {
                'removed': True,
                'memberId': member_id,
                'removedFile': removed_file,
                'module': 'face',
            }, None
        finally:
            self._camera_lock.release()

    def reset(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        if str(payload.get('scope') or '') != 'all':
            return 400, None, {'code': 'invalid_request', 'message': 'scope=all is required', 'retryable': False}
        if not self._camera_lock.acquire(blocking=False):
            return 423, None, {'code': 'camera_busy', 'message': 'IMX219 is busy', 'retryable': True}
        try:
            with self._lock:
                indexed_count = len(self._load_index())
                removed_files = 0
                if self.face_bank_root.exists():
                    for face_path in self.face_bank_root.glob('*.npy'):
                        face_path.unlink()
                        removed_files += 1
                self._save_index({})
            return 200, {
                'reset': True,
                'module': 'face',
                'removedMembers': indexed_count,
                'removedFaceFiles': removed_files,
            }, None
        finally:
            self._camera_lock.release()

    def register(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get('sessionId') or '').strip()
        member_id = str(payload.get('memberId') or '').strip()
        name = str(payload.get('name') or '').strip()
        samples = int(payload.get('samples') or 8)
        timeout_seconds = int(payload.get('timeoutSeconds') or 60)
        if not session_id or not member_id or not name:
            return 400, None, {'code': 'invalid_request', 'message': 'sessionId/memberId/name are required', 'retryable': False}
        face_key = self._face_key_from_name(name)
        if not face_key:
            return 400, None, {'code': 'invalid_request', 'message': 'name must contain valid filename characters', 'retryable': False}
        with self._lock:
            item = self._sessions.get(session_id)
        if not item or not item.get('ready'):
            return 503, None, {'code': 'module_not_ready', 'message': 'face module is not preloaded', 'retryable': True}
        if not self._camera_lock.acquire(blocking=False):
            return 423, None, {'code': 'camera_busy', 'message': 'IMX219 is busy', 'retryable': True}
        started = time.perf_counter()
        try:
            with self._lock:
                self._stop_preview_locked(session_id)
            out_path = self._face_path(face_key)
            if out_path is None:
                return 400, None, {'code': 'invalid_request', 'message': 'name must contain valid filename characters', 'retryable': False}
            if out_path.exists():
                out_path.unlink()
            cmd = [
                'bash', str(self.run_script), 'register', face_key,
                '--display', 'gst', '--provider', self.provider, '--rec-provider', self.provider, '--ep-threads', str(self.ep_threads),
                '--target-fps', str(self.target_fps), '--detect-interval', '0.0',
                '--samples', str(samples), '--max-seconds', str(timeout_seconds), '--center-tolerance', '0.08',
            ]
            result = subprocess.run(self._affinity_command(cmd), cwd=str(self.root), capture_output=True, text=True, timeout=timeout_seconds + 20)
            if result.returncode != 0 or not out_path.exists():
                return 503, None, {'code': 'model_unavailable', 'message': 'face registration failed', 'retryable': True}
            index = self._load_index()
            previous_key = str((index.get(member_id) or {}).get('faceKey') or member_id)
            if previous_key != face_key:
                previous_path = self._face_path(previous_key)
                if previous_path and previous_path.exists():
                    previous_path.unlink()
            index[member_id] = {'name': name, 'faceKey': face_key, 'updatedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
            self._save_index(index)
            return 200, {
                'status': 'registered',
                'memberId': member_id,
                'name': name,
                'samples': samples,
                'capturedSamples': samples,
                'provider': self.provider,
                'epThreads': self.ep_threads,
                'cpuAffinity': self.cpu_affinity,
                'capturedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'durationMs': round((time.perf_counter() - started) * 1000, 1),
            }, None
        except subprocess.TimeoutExpired:
            return 504, None, {'code': 'inference_timeout', 'message': 'face registration timed out', 'retryable': True}
        finally:
            self._camera_lock.release()

    def verify(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get('sessionId') or '').strip()
        member_id = str(payload.get('memberId') or '').strip()
        expected_name = str(payload.get('expectedName') or '').strip()
        timeout_seconds = int(payload.get('timeoutSeconds') or 30)
        threshold = float(payload.get('threshold') or 0.6)
        stable_frames = int(payload.get('stableFrames') or 3)
        if not session_id or not member_id:
            return 400, None, {'code': 'invalid_request', 'message': 'sessionId/memberId are required', 'retryable': False}
        with self._lock:
            item = self._sessions.get(session_id)
        if not item or not item.get('ready'):
            return 503, None, {'code': 'module_not_ready', 'message': 'face module is not preloaded', 'retryable': True}
        index = self._load_index()
        member = index.get(member_id)
        if not member:
            return 404, None, {'code': 'session_not_found', 'message': 'memberId is not registered', 'retryable': False}
        preferred_key = self._face_key_from_name(str(expected_name or member.get('name') or ''))
        legacy_key = str(member.get('faceKey') or member_id)
        preferred_path = self._face_path(preferred_key)
        face_key = preferred_key if preferred_path and preferred_path.exists() else legacy_key
        face_path = self._face_path(face_key)
        if not face_path or not face_path.exists():
            return 400, None, {'code': 'invalid_request', 'message': 'registered face filename is invalid', 'retryable': False}
        display_name = str(member.get('name') or expected_name or member_id)
        if not self._camera_lock.acquire(blocking=False):
            return 423, None, {'code': 'camera_busy', 'message': 'IMX219 is busy', 'retryable': True}
        started = time.perf_counter()
        try:
            with self._lock:
                self._stop_preview_locked(session_id)
            safe_session = re.sub(r'[^A-Za-z0-9_.-]', '_', session_id)
            result_json = Path(f'/tmp/smart_medicine_face/face_verify_{safe_session}.json')
            if result_json.exists():
                result_json.unlink()
            verify_bank = Path(f'/tmp/smart_medicine_face/face_bank_{safe_session}')
            verify_bank.mkdir(parents=True, exist_ok=True)
            for stale_path in verify_bank.glob('*.npy'):
                stale_path.unlink()
            shutil.copy2(face_path, verify_bank / f'{face_key}.npy')
            cmd = [
                'bash', str(self.run_script), 'live',
                '--display', 'gst', '--provider', self.provider, '--rec-provider', self.provider, '--ep-threads', str(self.ep_threads),
                '--target-fps', str(self.target_fps), '--detect-interval', '0.0',
                '--duration', str(timeout_seconds), '--threshold', str(threshold), '--expected-name', face_key,
                '--exit-on-success', '--success-stable-frames', str(stable_frames), '--result-json', str(result_json),
                '--display-width', '1024', '--display-height', '600', '--no-async-infer',
                '--verify-center-gate', '--center-tolerance', '0.08',
            ]
            env = os.environ.copy()
            env['FACE_BANK_DIR'] = str(verify_bank)
            result = subprocess.run(self._affinity_command(cmd), cwd=str(self.root), env=env, capture_output=True, text=True, timeout=timeout_seconds + 20)
            recognized = '[live] recognized:' in ((result.stdout or '') + (result.stderr or ''))
            score = 0.0
            result_detail = {}
            if result_json.exists():
                try:
                    payload_json = json.loads(result_json.read_text(encoding='utf-8'))
                    result_detail = payload_json
                    score = float(payload_json.get('score') or 0.0)
                    recognized = bool(payload_json.get('ok')) or recognized
                except Exception:
                    pass
            if recognized:
                return 200, {
                    'status': 'recognized',
                    'memberId': member_id,
                    'expectedName': expected_name or display_name,
                    'verified': True,
                    'name': display_name,
                    'bestName': result_detail.get('name') or face_key,
                    'score': score,
                    'threshold': threshold,
                    'stableFrames': stable_frames,
                    'model': self.recognizer_model,
                    'provider': self.provider,
                    'epThreads': self.ep_threads,
                    'cpuAffinity': self.cpu_affinity,
                    'capturedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'durationMs': round((time.perf_counter() - started) * 1000, 1),
                }, None
            return 200, {
                'status': 'no_match',
                'memberId': member_id,
                'expectedName': expected_name or display_name,
                'verified': False,
                'name': '',
                'bestName': result_detail.get('name') or '',
                'score': score,
                'threshold': threshold,
                'stableFrames': stable_frames,
                'model': self.recognizer_model,
                'durationMs': round((time.perf_counter() - started) * 1000, 1),
            }, None
        except subprocess.TimeoutExpired:
            return 504, None, {'code': 'inference_timeout', 'message': 'face verification timed out', 'retryable': True}
        finally:
            self._camera_lock.release()
