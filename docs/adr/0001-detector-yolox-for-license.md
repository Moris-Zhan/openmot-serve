# Use YOLOX instead of Ultralytics YOLO as the detector

This project is MIT-licensed and is served over a network API. Ultralytics YOLO is licensed under AGPL-3.0, whose obligations extend to software offered as a network service, so using it would force the whole repository to AGPL-3.0. We use YOLOX (Apache-2.0) as the pretrained detector, which keeps the repository MIT and compatible with ByteTrack (MIT) and TrackEval (MIT).

## Considered Options

- **Ultralytics YOLO, relicense the repo to AGPL-3.0** — least effort, but anyone reusing the code inherits AGPL obligations.
- **YOLOX (Apache-2.0)** — chosen. Permissive, widely used with ByteTrack, pretrained COCO weights available.
- **torchvision detectors (BSD-3-Clause)** — permissive, but weaker speed/accuracy trade-off for real-time tracking.
- **MOTChallenge public detections only** — no detector licensing at all, but the service could not process arbitrary video.

## Consequences

- The detector wrapper targets the YOLOX API; switching back to Ultralytics later means a license change, not just a code change.
- MOT17/MOT20 data are licensed CC BY-NC-SA 3.0: the repository ships a download script only, never the data.
