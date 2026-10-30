#!/bin/sh
# 下载 MediaPipe Face Landmarker 模型（官方 Google 存储，约 3.6 MB）
set -e
cd "$(dirname "$0")"
mkdir -p models
curl -fL -o models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
ls -l models/face_landmarker.task
