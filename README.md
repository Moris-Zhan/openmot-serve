# openmot-serve

Multi-object tracking (MOT17/MOT20) as a production service: FastAPI + Docker + Kubernetes + CI/CD, evaluated with HOTA.

> **Status: v0.2 done; v0.3 in progress — Kubernetes manifests are written but not yet verified on a live cluster; v0.4 CI done, observability planned — the workflow and the GHCR publish have run on GitHub.** The tracking pipeline and benchmark (v0.1) are unchanged: `python -m eval.run` tunes on the MOT17 tuning half and reports HOTA / MOTA / IDF1 on the validation half (see [`docs/evaluation.md`](docs/evaluation.md)). On top of it, an HTTP API, a Celery GPU worker and a Docker Compose stack are working; see [`docs/service.md`](docs/service.md) for the API, the container deployment and the end-to-end results. The Kubernetes manifests (kustomize base and a k3s single-node overlay) are covered by render and schema tests only; no k3s cluster was available, so deployment, GPU scheduling and pod recovery are not yet verified. [`docs/kubernetes.md`](docs/kubernetes.md) has the prerequisites, the deploy steps and a runbook for verifying them on an external cluster. The GitHub Actions workflow (lint, tests and an api image build on every push) has been run on GitHub-hosted runners and passes; publishing the images to GHCR from `main` has run once successfully (the push is confirmed in the job log; the images have not been pulled back yet). [`docs/ci.md`](docs/ci.md) describes the jobs and how to reproduce them locally. Observability is still _planned_ and is the design this repository is being built against. Design decisions live in [`docs/adr/`](docs/adr/); domain terms are defined in [`CONTEXT.md`](CONTEXT.md).

## What this project demonstrates

| Area | Evidence (when complete) |
|---|---|
| Reproducible evaluation | HOTA / MOTA / IDF1 on the MOT17 validation half, re-runnable with one command |
| Model serving | Async tracking-job API with FastAPI, Celery and Redis |
| Containers & Kubernetes | Docker images and k3s manifests with NVIDIA GPU scheduling |
| CI/CD | GitHub Actions: lint, test and build an image on every push |
| Observability | Prometheus metrics and Grafana dashboards for latency, throughput and job failures |
| Inference optimization | Detector INT8 quantization with before/after latency and HOTA |
| Engineering judgement | Detector chosen for license compatibility ([ADR-0001](docs/adr/0001-detector-yolox-for-license.md)) |

## How it works

A client submits a **sequence** (a zip of numbered frames) as a **tracking job**. The API stores the sequence and enqueues the job. A GPU worker detects people with **YOLOX**, links detections into **tracks** with **ByteTrack**, and writes the **result** in MOTChallenge format. The client polls the job until it succeeds, then downloads the result.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API (FastAPI)
    participant Q as Redis
    participant W as Worker (Celery, GPU)
    participant S as Storage (shared volume)

    C->>A: POST /jobs (sequence)
    A->>S: save sequence
    A->>Q: enqueue job
    A-->>C: 202 Accepted {job_id}
    Q->>W: deliver job
    W->>S: read sequence
    W->>W: YOLOX detection → ByteTrack tracking
    W->>S: write result (MOTChallenge format)
    W->>Q: status = succeeded
    C->>A: GET /jobs/{job_id}
    A-->>C: {status: succeeded}
    C->>A: GET /jobs/{job_id}/result
    A-->>C: tracks
```

## Deployment architecture _(v0.3 manifests written, not yet verified; monitoring planned, v0.4)_

```mermaid
flowchart LR
    subgraph k3s cluster
        subgraph app [namespace: openmot]
            API[api<br/>Deployment] --> REDIS[(redis)]
            REDIS --> WORKER[worker<br/>Deployment<br/>GPU node]
            API --- PV[(PersistentVolumeClaim)]
            WORKER --- PV
        end
        subgraph mon [namespace: monitoring - planned, v0.4]
            PROM[Prometheus] --> GRAF[Grafana]
        end
        GPUPLUGIN[NVIDIA device plugin<br/>assumed installed] -.advertises GPU.-> WORKER
        PROM -.scrapes /metrics.-> API
        PROM -.scrapes.-> WORKER
    end
    USER[Client] --> API
```

| Component | Choice | Notes |
|---|---|---|
| API | FastAPI | Async job submission and polling |
| Queue | Celery + Redis | One job per sequence |
| Detector | YOLOX, pretrained COCO weights | Apache-2.0, see ADR-0001 |
| Tracker | ByteTrack | MIT |
| Storage | Kubernetes PersistentVolumeClaim (k3s `local-path`, single node) | S3-compatible storage later |
| Orchestration | k3s single node + NVIDIA device plugin | GPU Operator is not managed here; target GPU: RTX 5090 (CUDA 12.8+) |
| Monitoring | Prometheus + Grafana | Latency, throughput, job failures |
| CI | GitHub Actions | Lint, test, build image |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Submit a zip of numbered frames; returns `job_id` with `202 Accepted` |
| `GET` | `/jobs/{job_id}` | Job status: `queued`, `running`, `succeeded`, `failed` |
| `GET` | `/jobs/{job_id}/result` | Tracks in MOTChallenge format (only when `succeeded`) |
| `GET` | `/health` | Readiness: `200` when the queue and storage are usable, otherwise `503` with the problems |
| `GET` | `/metrics` | Prometheus metrics _(planned, v0.4)_ |

## Repository layout

```
openmot-serve/
├── app/          # FastAPI service: routes, upload validation, entrypoint
├── jobs/         # Job records, state machine, Redis store, settings, storage layout
├── worker/       # Celery worker: job processing, startup checks, healthcheck
├── tracking/     # Detection (YOLOX) + tracking (ByteTrack) pipeline
├── eval/         # TrackEval benchmark runs on the MOT17 validation half
├── deploy/
│   ├── docker/   # Dockerfiles, docker-compose
│   └── k8s/      # kustomize base + k3s single-node overlay
├── .github/workflows/  # CI: lint, test, api image build, publish from main
├── monitoring/   # Prometheus rules, Grafana dashboards (planned, v0.4)
├── data/         # download_mot.sh (datasets are never committed)
├── tests/
└── docs/
    └── adr/      # Architecture decision records
```

## Evaluation protocol

MOT17 test-set ground truth is not public, so each MOT17 training sequence is split in half:

- **Tuning half** (first half of each sequence): choose tracker parameters
- **Validation half** (second half): report scores with TrackEval

MOT20 is evaluated after v0.5.

## Results _(TBD)_

### Tracking accuracy — MOT17 validation half

| Detector | Tracker | Precision | HOTA | MOTA | IDF1 |
|---|---|---|---|---|---|
| YOLOX | ByteTrack | FP16 | 50.78 | 46.14 | 59.82 |
| YOLOX | ByteTrack | INT8 | TBD | TBD | TBD |

COCO 預訓練權重，未針對 MOT17 微調；數字不可與 ByteTrack 論文並列比較。7 個 FRCNN 訓練序列的合併分數，重現方式見 [`docs/evaluation.md`](docs/evaluation.md)。

### Serving performance — RTX 5090

| Precision | Latency per frame (ms) | Throughput (FPS) | GPU memory (GB) |
|---|---|---|---|
| FP16 | TBD | TBD | TBD |
| INT8 | TBD | TBD | TBD |

### Dashboards

<!-- Grafana screenshot goes here once v0.4 is done -->

## Roadmap

### v0.1 — Tracking pipeline and benchmark
- [x] Verify CUDA / PyTorch on the target GPU (RTX 5090, CUDA 12.8+)
- [x] YOLOX detection + ByteTrack tracking on MOT17 image sequences
- [x] Tuning / validation half split
- [x] HOTA, MOTA, IDF1 on the validation half with TrackEval

### v0.2 — Async service and containers
- [x] Tracking-job API (`/jobs`, `/jobs/{id}`, `/jobs/{id}/result`)
- [x] Celery worker + Redis queue
- [x] Dockerfiles and docker-compose for API, worker and Redis

### v0.3 — Kubernetes
- [x] k3s manifests: api, worker, redis, persistent volume (kustomize base + k3s overlay; render and schema tests only)
- [x] Health checks and resource limits (probes declared; resource values are uncalibrated)
- [ ] GPU scheduling for the worker (`nvidia.com/gpu` request is declared; scheduling is unverified, and the NVIDIA GPU Operator is out of scope — a device plugin is assumed)
- [ ] Verify the deployment, GPU scheduling and pod recovery on a live k3s cluster (runbook in [`docs/kubernetes.md`](docs/kubernetes.md))

### v0.4 — CI/CD and observability
- [x] GitHub Actions: lint, test, build image on every push (verified on GitHub-hosted runners)
- [x] Publish the api and worker images to GHCR from `main` (ran once on `main`; push confirmed in the job log)
- [ ] Prometheus metrics: request latency, job duration, throughput, failures
- [ ] Grafana dashboard and alert rules

### v0.5 — Inference optimization
- [ ] Export the detector to ONNX / TensorRT
- [ ] INT8 quantization with calibration
- [ ] Before/after comparison of latency, throughput and HOTA

### Later
- [ ] MOT20 benchmark
- [ ] S3-compatible storage
- [ ] Natural-language queries over tracking results (RAG agent)

## Data

```bash
./data/download_mot.sh              # MOT17
./data/download_mot.sh --with-mot20
```

MOTChallenge data are licensed CC BY-NC-SA 3.0 (non-commercial) and are never committed to this repository.

## License

MIT. Third-party components: YOLOX (Apache-2.0), ByteTrack (MIT), TrackEval (MIT).
