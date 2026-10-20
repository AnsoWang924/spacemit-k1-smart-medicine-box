#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-"/run/user/$(id -u)"}
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
export FACE_DET_REC_ROOT=${FACE_DET_REC_ROOT:-"$SCRIPT_DIR/vendor/face_det_rec"}
export FACE_DET_MODEL=${FACE_DET_MODEL:-"$FACE_DET_REC_ROOT/onnx_model/static_int8_20260718_batch2_det/yolov5n-face_320_cut_static_qdq_int8.onnx"}
export FACE_REC_MODEL=${FACE_REC_MODEL:-"$FACE_DET_REC_ROOT/onnx_model/static_int8_20260717/arcface_mobilefacenet_cut_static_qdq_int8.onnx"}
export FACE_BANK_DIR=${FACE_BANK_DIR:-"$SCRIPT_DIR/../../data/faces"}

python3 "$SCRIPT_DIR/face_live_imx219_smooth_register.py" "$@"
