# Graph Report - openmot-serve  (2026-11-04)

## Corpus Check
- Corpus is ~1,682 words - fits in a single context window. You may not need a graph.

## Summary
- 41 nodes · 63 edges · 8 communities (6 shown, 2 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.83)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- 評測與資料集
- 偵測器授權決策 (ADR-0001)
- 追蹤工作 API 與結果
- 非同步服務架構
- 部署、觀測與量化
- 追蹤核心概念
- 資料下載腳本
- CI 與容器化

## God Nodes (most connected - your core abstractions)
1. `GPU worker (Celery)` - 9 edges
2. `ADR-0001: YOLOX instead of Ultralytics YOLO` - 8 edges
3. `API service (FastAPI)` - 6 edges
4. `YOLOX detector (Apache-2.0)` - 5 edges
5. `Roadmap v0.1 to v0.5` - 5 edges
6. `Sequence` - 4 edges
7. `Track` - 4 edges
8. `Tracking Job` - 4 edges
9. `Result` - 4 edges
10. `Validation Half` - 4 edges

## Surprising Connections (you probably didn't know these)
- `torchvision detectors (BSD-3-Clause), rejected` --semantically_similar_to--> `YOLOX detector (Apache-2.0)`  [INFERRED] [semantically similar]
  docs/adr/0001-detector-yolox-for-license.md → README.md
- `ADR-0001: YOLOX instead of Ultralytics YOLO` --references--> `ByteTrack tracker (MIT)`  [EXTRACTED]
  docs/adr/0001-detector-yolox-for-license.md → README.md
- `ADR-0001: YOLOX instead of Ultralytics YOLO` --references--> `TrackEval (MIT)`  [EXTRACTED]
  docs/adr/0001-detector-yolox-for-license.md → README.md
- `ADR-0001: YOLOX instead of Ultralytics YOLO` --references--> `MOTChallenge data license (CC BY-NC-SA 3.0)`  [EXTRACTED]
  docs/adr/0001-detector-yolox-for-license.md → README.md
- `ADR-0001: YOLOX instead of Ultralytics YOLO` --references--> `YOLOX detector (Apache-2.0)`  [EXTRACTED]
  docs/adr/0001-detector-yolox-for-license.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Async tracking-job flow (API -> queue -> worker -> storage)** — readme_api, readme_redis_queue, readme_worker, readme_storage [EXTRACTED 1.00]
- **Detection -> tracking -> result pipeline** — readme_yolox, readme_bytetrack, readme_motchallenge_format [EXTRACTED 1.00]
- **Evaluation protocol (halves + TrackEval + HOTA)** — context_tuning_half, context_validation_half, readme_trackeval, readme_hota_metrics [INFERRED 0.85]

## Communities (8 total, 2 thin omitted)

### Community 0 - "評測與資料集"
Cohesion: 0.24
Nodes (9): Benchmark Run, Frame, Sequence, Tuning Half, Validation Half, HOTA / MOTA / IDF1 metrics, MOT17 dataset, MOT20 dataset (after v0.5) (+1 more)

### Community 1 - "偵測器授權決策 (ADR-0001)"
Cohesion: 0.38
Nodes (5): ADR-0001: YOLOX instead of Ultralytics YOLO, MOTChallenge public detections only, rejected, torchvision detectors (BSD-3-Clause), rejected, Ultralytics YOLO (AGPL-3.0), rejected, YOLOX detector (Apache-2.0)

### Community 2 - "追蹤工作 API 與結果"
Cohesion: 0.60
Nodes (5): Job Status, Result, Tracking Job, Job endpoints (/jobs, /jobs/{id}, /result), MOTChallenge result format

### Community 3 - "非同步服務架構"
Cohesion: 0.60
Nodes (5): API service (FastAPI), openmot-serve (MOT as a production service), Queue (Celery + Redis), Storage (Kubernetes PersistentVolume), GPU worker (Celery)

### Community 4 - "部署、觀測與量化"
Cohesion: 0.40
Nodes (5): INT8 quantization (ONNX / TensorRT), k3s cluster + NVIDIA GPU Operator, Prometheus + Grafana monitoring, Roadmap v0.1 to v0.5, Target GPU: RTX 5090 (CUDA 12.8+)

### Community 5 - "追蹤核心概念"
Cohesion: 0.83
Nodes (4): Detection, Identity, Track, ByteTrack tracker (MIT)

## Knowledge Gaps
- **4 isolated node(s):** `Frame`, `MOT20 dataset (after v0.5)`, `Target GPU: RTX 5090 (CUDA 12.8+)`, `MOTChallenge public detections only, rejected`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 4 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ADR-0001: YOLOX instead of Ultralytics YOLO` connect `偵測器授權決策 (ADR-0001)` to `評測與資料集`, `追蹤核心概念`?**
  _High betweenness centrality (0.305) - this node is a cross-community bridge._
- **What connects `Frame`, `MOT20 dataset (after v0.5)`, `Target GPU: RTX 5090 (CUDA 12.8+)` to the rest of the system?**
  _4 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Why does `GPU worker (Celery)` connect `非同步服務架構` to `偵測器授權決策 (ADR-0001)`, `追蹤工作 API 與結果`, `部署、觀測與量化`, `追蹤核心概念`, `CI 與容器化`?**
  _High betweenness centrality (0.254) - this node is a cross-community bridge._
- **Why does `Evaluation protocol (tuning/validation split)` connect `評測與資料集` to `部署、觀測與量化`?**
  _High betweenness centrality (0.216) - this node is a cross-community bridge._