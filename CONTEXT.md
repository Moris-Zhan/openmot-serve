# openmot-serve

A multi-object tracking service: it takes a video or an image sequence, detects and tracks people, and returns trajectories with persistent identities. Evaluated on the public MOT17/MOT20 benchmarks.

## Language

### Input

**Sequence**:
One video clip, stored either as a video file or as an ordered folder of frames.
_Avoid_: clip, video, scene

**Frame**:
A single image within a sequence, addressed by its 1-based frame number.
_Avoid_: image, picture

### Tracking

**Detection**:
One bounding box with a confidence score for one object in one frame, before any identity is assigned.
_Avoid_: box, prediction

**Track**:
The ordered set of detections across frames that belong to one identity.
_Avoid_: trajectory, tracklet, path

**Identity**:
The persistent ID that links detections of the same object across frames.
_Avoid_: track ID, object ID, label

### Service

**Tracking Job**:
One request to track one sequence, processed asynchronously and identified by its job ID.
_Avoid_: task, request, run

**Job Status**:
The lifecycle state of a tracking job: queued, running, succeeded, or failed.
_Avoid_: state, progress

**Result**:
The tracks produced by a succeeded tracking job, in MOTChallenge format.
_Avoid_: output, response

### Evaluation

**Tuning Half**:
The first half of each MOT17 training sequence, used to choose tracker parameters.
_Avoid_: train split, dev set

**Validation Half**:
The second half of each MOT17 training sequence, used only to report HOTA.
_Avoid_: test set, val split

**Benchmark Run**:
One evaluation of the full pipeline on the validation half, producing HOTA and related scores.
_Avoid_: experiment, eval
