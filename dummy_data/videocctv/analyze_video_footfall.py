#!/usr/bin/env python3
"""
analyze_video_footfall.py
==========================
Mengolah video CCTV/dummy toko (durasi pendek, mis. ~110 detik) menjadi data
terstruktur untuk layer silver & gold pada arsitektur data lake.

Asumsi penamaan file: <YYYYMMDD>_<camera_or_location_id>.mp4
  contoh: 20260614_137362787.mp4
  -> tanggal  = 2026-06-14  (dipakai sebagai dim_date / hari periodik)
  -> kamera   = "137362787" (dipakai sebagai dim_camera / lokasi)

Pipeline:
  1. BRONZE  -> ekstrak metadata teknis video (fps, durasi, resolusi, ukuran file)
  2. SILVER  -> deteksi orang per frame sampel (OpenCV HOG, tanpa download model)
               + tracking sederhana (centroid tracker) -> log per-track
  3. GOLD    -> agregasi jadi:
       - fact_footfall_summary : 1 baris per video (grain paling minimal)
       - fact_footfall_bin     : beberapa baris per video, per bin waktu (opsional/tambahan)

Catatan jujur soal keterbatasan:
  - HOG people detector itu detector klasik (bukan deep learning), akurasinya
    pas-pasan dibanding YOLO, tapi 100% offline & tidak butuh download model
    sehingga PASTI bisa jalan di environment manapun. Untuk versi produksi,
    bisa diganti `ultralytics` (YOLOv8) kalau ada akses download bobot model.
  - Centroid tracker yang dipakai sederhana (nearest-centroid + batas jarak),
    bisa terjadi ID switch/duplikasi pada kondisi crowded/overlap. Untuk
    insight demo & pembuktian pipeline data lake, ini cukup; bukan untuk
    akurasi people-counting tingkat produksi.

Pemakaian:
    python3 analyze_video_footfall.py --input "pool/*.mp4" --output-dir datalake \
        --bin-seconds 15 --sample-fps 2
"""

import argparse
import glob
import json
import math
import re
from datetime import datetime
from pathlib import Path

import cv2
import pandas as pd


# --------------------------------------------------------------------------- #
# 1. PARSING METADATA DARI NAMA FILE
# --------------------------------------------------------------------------- #
FNAME_PATTERN = re.compile(r"^(\d{8})_(.+)$")


def parse_filename_metadata(path: Path):
    """Ambil tanggal & camera_id dari konvensi <YYYYMMDD>_<id>.ext"""
    m = FNAME_PATTERN.match(path.stem)
    if m:
        date_str, camera_id = m.group(1), m.group(2)
        try:
            video_date = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            video_date = None
    else:
        video_date, camera_id = None, path.stem
    return video_date, camera_id


# --------------------------------------------------------------------------- #
# 2. CENTROID TRACKER SEDERHANA (tanpa dependency tambahan)
# --------------------------------------------------------------------------- #
class CentroidTracker:
    def __init__(self, max_disappeared=5, max_distance=80):
        self.next_id = 0
        self.objects = {}       # id -> (x, y)
        self.disappeared = {}   # id -> jumlah frame berturut hilang
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def register(self, centroid):
        oid = self.next_id
        self.objects[oid] = centroid
        self.disappeared[oid] = 0
        self.next_id += 1
        return oid

    def deregister(self, oid):
        del self.objects[oid]
        del self.disappeared[oid]

    def update(self, input_centroids):
        if len(input_centroids) == 0:
            for oid in list(self.disappeared.keys()):
                self.disappeared[oid] += 1
                if self.disappeared[oid] > self.max_disappeared:
                    self.deregister(oid)
            return {}

        if len(self.objects) == 0:
            return {self.register(c): c for c in input_centroids}

        object_ids = list(self.objects.keys())
        object_centroids = list(self.objects.values())

        pairs = []
        for r, oc in enumerate(object_centroids):
            for c, ic in enumerate(input_centroids):
                d = math.dist(oc, ic)
                pairs.append((d, r, c))
        pairs.sort(key=lambda x: x[0])

        assigned_rows, assigned_cols, matches = set(), set(), {}
        for dist, r, c in pairs:
            if r in assigned_rows or c in assigned_cols or dist > self.max_distance:
                continue
            oid = object_ids[r]
            matches[oid] = input_centroids[c]
            self.disappeared[oid] = 0
            assigned_rows.add(r)
            assigned_cols.add(c)

        for r, oid in enumerate(object_ids):
            if r not in assigned_rows:
                self.disappeared[oid] += 1
                if self.disappeared[oid] > self.max_disappeared:
                    self.deregister(oid)

        for c, ic in enumerate(input_centroids):
            if c not in assigned_cols:
                matches[self.register(ic)] = ic

        self.objects.update(matches)
        return matches


# --------------------------------------------------------------------------- #
# 3. EKSTRAKSI METADATA TEKNIS (BRONZE)
# --------------------------------------------------------------------------- #
def extract_bronze_metadata(path: Path, video_date, camera_id):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_sec = (frame_count / fps) if fps > 0 else 0
    cap.release()

    return {
        "file_name": path.name,
        "video_date": video_date.isoformat() if video_date else None,
        "camera_id": camera_id,
        "fps": round(fps, 2),
        "frame_count": int(frame_count),
        "width": width,
        "height": height,
        "duration_sec": round(duration_sec, 2),
        "file_size_bytes": path.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# 4. DETEKSI + TRACKING (SILVER)
# --------------------------------------------------------------------------- #
def analyze_video(path: Path, sample_fps: float, bin_seconds: float):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    sample_interval = max(1, int(round(fps / sample_fps)))

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    tracker = CentroidTracker(max_disappeared=int(sample_fps * 2), max_distance=90)

    detection_log = []          # untuk silver: per-sample log
    bin_occupancy = {}          # bin_idx -> list occupancy per sample
    bin_first_seen_count = {}   # bin_idx -> jumlah track id baru muncul di bin ini
    known_ids = set()

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        if frame_idx % sample_interval == 0:
            t_sec = frame_idx / fps
            bin_idx = int(t_sec // bin_seconds)

            # downscale supaya deteksi lebih cepat pada frame resolusi besar
            scale = 640 / frame.shape[1] if frame.shape[1] > 640 else 1.0
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame

            boxes, _ = hog.detectMultiScale(small, winStride=(8, 8))
            centroids = [
                ((x + w / 2) / scale, (y + h / 2) / scale) for (x, y, w, h) in boxes
            ]

            matches = tracker.update(centroids)
            current_ids = set(matches.keys())
            new_ids = current_ids - known_ids
            known_ids.update(new_ids)

            bin_occupancy.setdefault(bin_idx, []).append(len(matches))
            bin_first_seen_count[bin_idx] = bin_first_seen_count.get(bin_idx, 0) + len(new_ids)

            for oid, (cx, cy) in matches.items():
                detection_log.append({
                    "frame_idx": frame_idx,
                    "t_sec": round(t_sec, 2),
                    "bin_idx": bin_idx,
                    "track_id": oid,
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                })

        frame_idx += 1

    cap.release()

    total_unique_visitors = tracker.next_id
    all_occ = [v for lst in bin_occupancy.values() for v in lst]
    summary_metrics = {
        "total_unique_visitors": total_unique_visitors,
        "avg_occupancy": round(sum(all_occ) / len(all_occ), 2) if all_occ else 0.0,
        "peak_occupancy": max(all_occ) if all_occ else 0,
        "sampled_frames": len(all_occ),
    }

    bin_rows = []
    for bin_idx in sorted(bin_occupancy.keys()):
        occ = bin_occupancy[bin_idx]
        bin_rows.append({
            "bin_idx": bin_idx,
            "bin_start_sec": bin_idx * bin_seconds,
            "avg_occupancy": round(sum(occ) / len(occ), 2),
            "max_occupancy": max(occ),
            "new_visitors": bin_first_seen_count.get(bin_idx, 0),
            "sampled_frames": len(occ),
        })

    return summary_metrics, bin_rows, detection_log


# --------------------------------------------------------------------------- #
# 5. ORKESTRASI: ingest -> analyze -> structure (gold)
# --------------------------------------------------------------------------- #
def run(input_glob, output_dir, sample_fps, bin_seconds):
    out = Path(output_dir)
    bronze_dir = out / "bronze" / "mp4"
    silver_dir = out / "silver" / "video"
    gold_dir = out / "gold" / "fact_table"
    for d in (bronze_dir, silver_dir, gold_dir):
        d.mkdir(parents=True, exist_ok=True)

    bronze_rows, summary_rows, bin_rows_all = [], [], []

    paths = sorted(Path(p) for p in glob.glob(input_glob))
    if not paths:
        print(f"[!] Tidak ada file ditemukan untuk pattern: {input_glob}")
        return

    for path in paths:
        print(f"[i] Memproses {path.name} ...")
        video_date, camera_id = parse_filename_metadata(path)

        meta = extract_bronze_metadata(path, video_date, camera_id)
        bronze_rows.append(meta)

        summary_metrics, bin_rows, detection_log = analyze_video(
            path, sample_fps=sample_fps, bin_seconds=bin_seconds
        )

        # silver: simpan log deteksi per video
        det_df = pd.DataFrame(detection_log)
        det_df.to_csv(silver_dir / f"{path.stem}_detections.csv", index=False)

        # gabungkan info dimensi (tanggal, kamera) ke setiap baris
        for r in bin_rows:
            r.update({
                "file_name": path.name,
                "video_date": meta["video_date"],
                "camera_id": camera_id,
            })
        bin_rows_all.extend(bin_rows)

        summary_rows.append({
            "file_name": path.name,
            "video_date": meta["video_date"],
            "camera_id": camera_id,
            "duration_sec": meta["duration_sec"],
            **summary_metrics,
        })

        print(f"    -> unique_visitors={summary_metrics['total_unique_visitors']} "
              f"avg_occupancy={summary_metrics['avg_occupancy']} "
              f"peak={summary_metrics['peak_occupancy']}")

    # bronze metadata
    with (bronze_dir / "video_metadata.jsonl").open("w", encoding="utf-8") as f:
        for r in bronze_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # gold: fact tables siap di-load ke Data Warehouse
    pd.DataFrame(summary_rows).to_parquet(gold_dir / "fact_footfall_summary.parquet", index=False)
    pd.DataFrame(bin_rows_all).to_parquet(gold_dir / "fact_footfall_bin.parquet", index=False)

    print("\n[OK] Selesai.")
    print(f"     Bronze metadata : {bronze_dir / 'video_metadata.jsonl'}")
    print(f"     Silver detections : {silver_dir}/*_detections.csv")
    print(f"     Gold fact_footfall_summary.parquet ({len(summary_rows)} baris)")
    print(f"     Gold fact_footfall_bin.parquet     ({len(bin_rows_all)} baris)")


def main():
    ap = argparse.ArgumentParser(description="Analisis video footfall untuk data lake.")
    ap.add_argument("--input", required=True, help="glob path video, mis. 'pool/*.mp4'")
    ap.add_argument("--output-dir", default="datalake", help="folder root data lake")
    ap.add_argument("--sample-fps", type=float, default=2.0,
                    help="berapa frame per detik yang dianalisis (default 2)")
    ap.add_argument("--bin-seconds", type=float, default=15.0,
                    help="lebar bin waktu dalam detik untuk fact_footfall_bin (default 15)")
    args = ap.parse_args()
    run(args.input, args.output_dir, args.sample_fps, args.bin_seconds)


if __name__ == "__main__":
    main()
