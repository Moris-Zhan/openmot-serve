#!/usr/bin/env bash
# Downloads MOT17 (and optionally MOT20) benchmark data into this directory.
# Source: https://motchallenge.net/data/MOT17/ and https://motchallenge.net/data/MOT20/
# License: MOTChallenge data are CC BY-NC-SA 3.0 (non-commercial). Never commit the data to this repo.
# Migrated from the 2026-09-04 local scaffold (open-mot-tracking-service).
set -euo pipefail

DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

download() {
    local name="$1" url="$2"
    if [ -d "${DATA_DIR}/${name}" ]; then
        echo "${name} already present, skipping."
        return
    fi
    echo "Downloading ${name}..."
    curl -L -o "${DATA_DIR}/${name}.zip" "${url}"
    unzip -q "${DATA_DIR}/${name}.zip" -d "${DATA_DIR}"
    rm "${DATA_DIR}/${name}.zip"
}

download "MOT17" "https://motchallenge.net/data/MOT17.zip"

if [ "${1:-}" = "--with-mot20" ]; then
    download "MOT20" "https://motchallenge.net/data/MOT20.zip"
fi

echo "Done. Data in ${DATA_DIR}"
