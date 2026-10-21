# -*- coding: utf-8 -*-
"""Cluster per-line clone reference clips by speaker and pick one reference per speaker.

Run with the IndexTTS2 venv (it has torch/torchaudio/sklearn and the CAMPPlus
weights).  The dubbing stage calls this script after cutting one reference clip
per subtitle line; the returned map lets every line point at a clean reference
for its own speaker instead of collapsing the whole video onto line 1.

Input JSON:  [{"line": 1, "path": "clone-1.wav"}, ...]
Output JSON: {"n_clusters": 2, "assign": {"1": 0, ...},
              "reps": {"0": "...wav", "1": "...wav"}, ...}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torchaudio


def _find_weight(indextts_home: Path) -> str | None:
    pattern = str(indextts_home / "checkpoints" / "hf_cache" /
                  "models--funasr--campplus" / "snapshots" / "*" /
                  "campplus_cn_common.bin")
    hits = glob.glob(pattern)
    return hits[0] if hits else None


def _load_audio(path: str, target_sr: int = 16000) -> torch.Tensor | None:
    try:
        wav, sr = torchaudio.load(path)
    except Exception:
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            wav = torch.from_numpy(data.T)
        except Exception:
            return None
    if wav.numel() == 0:
        return None
    if wav.size(0) > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
    return wav


def _embed(model, wav: torch.Tensor) -> np.ndarray | None:
    if wav.size(1) < 4800:  # < 0.3 s
        return None
    feat = torchaudio.compliance.kaldi.fbank(
        wav, num_mel_bins=80, dither=0, sample_frequency=16000)
    feat = feat - feat.mean(dim=0, keepdim=True)
    with torch.no_grad():
        emb = model(feat.unsqueeze(0))
    vec = emb.squeeze(0).cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else None


def _merge_to_max(labels: np.ndarray, sim: np.ndarray, max_speakers: int) -> np.ndarray:
    """Merge the two closest clusters until at most max_speakers remain."""
    labels = labels.copy()
    while len(set(labels.tolist())) > max_speakers:
        ids = sorted(set(labels.tolist()))
        best = None
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ia = np.where(labels == a)[0]
                ib = np.where(labels == b)[0]
                s = float(sim[np.ix_(ia, ib)].mean())
                if best is None or s > best[0]:
                    best = (s, a, b)
        if best is None:
            break
        _, a, b = best
        labels[labels == b] = a
    return labels


def _self_consistency(model, wav: torch.Tensor) -> float | None:
    """A real speaker stays similar across the two halves of a clip.

    Mixed/noisy clips (two speakers, music, cutting artefacts) do not.  This is
    the acoustic test that lets a *one-line* speaker survive: we never discard a
    cluster just because it is small.
    """
    if wav.size(1) < 16000:  # < 1.0 s
        return None
    half = wav.size(1) // 2
    a = _embed(model, wav[:, :half])
    b = _embed(model, wav[:, half:])
    if a is None or b is None:
        return None
    return float(a @ b)


def _centroids(labels: np.ndarray, X: np.ndarray, ids: list[int]) -> dict[int, np.ndarray]:
    out = {}
    for cid in ids:
        idx = np.where(labels == cid)[0]
        cen = X[idx].mean(axis=0)
        n = np.linalg.norm(cen)
        out[cid] = cen / n if n > 0 else cen
    return out


def _merge_noise(labels: np.ndarray, X: np.ndarray, sim: np.ndarray,
                 items, valid: list[int], model,
                 merge_sim: float, self_consistency: float
                 ) -> tuple[np.ndarray, dict]:
    """Fold a small cluster only when its *voice* says it is not a new speaker.

    Merge condition (never the number of lines alone):
      * its centroid is nearly identical to another speaker (>= merge_sim), or
      * its clips are internally inconsistent (< self_consistency), i.e. mixed/noise.
    A clean, distinct one-line speaker is kept.
    """
    labels = labels.copy()
    while True:
        ids = sorted(set(labels.tolist()))
        if len(ids) <= 1:
            break
        cents = _centroids(labels, X, ids)
        sizes = {cid: int((labels == cid).sum()) for cid in ids}
        candidates = []
        for cid in ids:
            idx = np.where(labels == cid)[0]
            size = sizes[cid]
            # Only a smaller/equal cluster may be folded into a larger anchor.
            others = [o for o in ids if o != cid and sizes[o] >= size]
            if not others:
                continue
            nearest_sim, other = max(
                (float(cents[cid] @ cents[o]), o) for o in others)
            sc_vals = []
            if size <= 5:  # self-consistency only makes sense for short clusters
                for p in idx:
                    wav = _load_audio(items[valid[p]]["path"])
                    if wav is not None:
                        sc = _self_consistency(model, wav)
                        if sc is not None:
                            sc_vals.append(sc)
            sc = float(np.mean(sc_vals)) if sc_vals else None
            # A single line needs a looser duplicate threshold (its embedding
            # is noisy), and low self-consistency there means a mixed cut.
            # A multi-line cluster is only merged when its voice is nearly
            # identical to a larger cluster -- never because it is short.
            if size == 1:
                dup_limit = 0.60
            else:
                dup_limit = merge_sim
            noisy_single = size == 1 and sc is not None and sc < self_consistency
            if nearest_sim >= dup_limit or noisy_single:
                candidates.append((nearest_sim, sc if sc is not None else 1.0,
                                   int(cid), int(other)))
        if not candidates:
            break
        # Merge the most clearly duplicate/noisy candidate first.
        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        _, _, cid, other = candidates[0]
        labels[labels == cid] = other
    return labels, {}


def _confidence(labels: np.ndarray, X: np.ndarray, sim: np.ndarray,
                items, valid: list[int], model) -> dict:
    ids = sorted(set(labels.tolist()))
    cents = _centroids(labels, X, ids)
    out = {}
    for cid in ids:
        idx = np.where(labels == cid)[0]
        size = len(idx)
        if size > 1:
            tri = sim[np.ix_(idx, idx)][np.triu_indices(size, 1)]
            cohesion = float(tri.mean()) if len(tri) else 1.0
        else:
            cohesion = 1.0
        nearest_sim = 0.0
        if len(ids) > 1:
            nearest_sim = max(float(cents[cid] @ cents[o])
                              for o in ids if o != cid)
        sc_vals = []
        if size <= 5:
            for p in idx:
                wav = _load_audio(items[valid[p]]["path"])
                if wav is not None:
                    sc = _self_consistency(model, wav)
                    if sc is not None:
                        sc_vals.append(sc)
        out[str(cid)] = {
            "size": int(size),
            "cohesion": cohesion,
            "nearest_sim": float(nearest_sim),
            "self_consistency": float(np.mean(sc_vals)) if sc_vals else None,
        }
    return out


def _medoid(positions: np.ndarray, valid: list[int], sim: np.ndarray,
            durations: list[float], min_rep: float) -> int:
    """positions index sim; valid maps those positions back to item indices."""
    if len(positions) == 1:
        return int(valid[int(positions[0])])
    sub = sim[np.ix_(positions, positions)]
    avg = sub.mean(axis=1)
    order = np.argsort(-avg)
    # Prefer a clean-length clip; fall back to the medoid / longest.
    for pos in order:
        idx = int(valid[int(positions[pos])])
        if durations[idx] >= min_rep:
            return idx
    return int(valid[int(positions[order[0]])])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--indextts-home", default=os.environ.get("INDEXTTS2_HOME", ""))
    ap.add_argument("--threshold", type=float,
                    default=float(os.environ.get("VTV_SPK_THRESHOLD", "0.72")))
    ap.add_argument("--max-speakers", type=int,
                    default=int(os.environ.get("VTV_SPK_MAX", "0") or 0))
    ap.add_argument("--min-cluster-size", type=int,
                    default=int(os.environ.get("VTV_SPK_MIN_SIZE", "0") or 0),
                    help="deprecated: size alone never merges a cluster")
    ap.add_argument("--merge-sim", type=float,
                    default=float(os.environ.get("VTV_SPK_MERGE_SIM", "0.80")),
                    help="merge a small cluster only if its voice is this close to another")
    ap.add_argument("--self-consistency", type=float,
                    default=float(os.environ.get("VTV_SPK_SELF_CONSISTENCY", "0.55")),
                    help="merge a small cluster if its two halves are less similar than this")
    ap.add_argument("--min-rep-duration", type=float, default=3.0)
    ap.add_argument("--device", default=os.environ.get("VTV_SPK_DEVICE", "cpu"))
    ap.add_argument("--no-force-first", action="store_true",
                    help="do not pin the first line's clip as its cluster representative")
    args = ap.parse_args()

    items = json.loads(Path(args.items).read_text(encoding="utf-8"))
    if not items:
        Path(args.out).write_text(json.dumps({"n_clusters": 1, "assign": {},
                                              "reps": {}, "error": "no items"}),
                                  encoding="utf-8")
        return 0

    home = Path(args.indextts_home).resolve()
    if not home.is_dir():
        raise SystemExit(f"INDEXTTS2_HOME not found: {home}")
    sys.path.insert(0, str(home))
    from indextts.s2mel.modules.campplus.DTDNN import CAMPPlus

    weight = _find_weight(home)
    if not weight:
        raise SystemExit(f"campplus weight not found under {home}/checkpoints/hf_cache")
    model = CAMPPlus(feat_dim=80, embedding_size=192)
    model.load_state_dict(torch.load(weight, map_location="cpu"))
    model.eval().to(args.device)

    embs: list[np.ndarray | None] = []
    durations: list[float] = []
    for it in items:
        wav = _load_audio(it["path"])
        if wav is None:
            embs.append(None)
            durations.append(0.0)
            continue
        durations.append(wav.size(1) / 16000.0)
        embs.append(_embed(model, wav.to(args.device)))

    valid = [i for i, e in enumerate(embs) if e is not None and durations[i] >= 1.0]
    if len(valid) < 2:
        out = {"n_clusters": 1, "assign": {str(it["line"]): 0 for it in items},
               "reps": {"0": items[0]["path"]}, "cluster_sizes": {"0": len(items)},
               "threshold": args.threshold, "error": "too few valid clips"}
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return 0

    X = np.stack([embs[i] for i in valid])
    sim = X @ X.T
    np.fill_diagonal(sim, 1.0)

    # Average-linkage agglomerative clustering on cosine distance.
    from sklearn.cluster import AgglomerativeClustering
    dist = np.clip(1.0 - sim, 0.0, 2.0)
    model_cl = AgglomerativeClustering(
        n_clusters=None, distance_threshold=max(0.0, 1.0 - args.threshold),
        metric="precomputed", linkage="average")
    labels_valid = model_cl.fit_predict(dist)

    labels_valid, _ = _merge_noise(
        labels_valid, X, sim, items, valid, model,
        args.merge_sim, args.self_consistency)
    if args.max_speakers > 0:
        labels_valid = _merge_to_max(labels_valid, sim, args.max_speakers)
    confidence = _confidence(labels_valid, X, sim, items, valid, model)

    # Centroids for assigning short/invalid clips.
    ids = sorted(set(labels_valid.tolist()))
    centroids = []
    for cid in ids:
        idx = np.where(labels_valid == cid)[0]
        cen = X[idx].mean(axis=0)
        n = np.linalg.norm(cen)
        centroids.append(cen / n if n > 0 else cen)
    centroids = np.stack(centroids)

    assign: dict[str, int] = {}
    prev = 0
    valid_pos = {vi: pos for pos, vi in enumerate(valid)}
    for i, it in enumerate(items):
        pos = valid_pos.get(i)
        if pos is not None and embs[i] is not None:
            cid_pos = int(np.argmax(centroids @ embs[i]))
            cid = ids[cid_pos]
        else:
            cid = prev
        assign[str(it["line"])] = cid
        prev = cid

    # Representative clip per cluster.
    reps: dict[str, str] = {}
    cluster_sizes: dict[str, int] = {}
    first_line = str(items[0]["line"])
    for cid in ids:
        member_pos = [p for p, v in enumerate(valid) if labels_valid[p] == cid]
        member_idx = [valid[p] for p in member_pos]
        cluster_sizes[str(cid)] = len(member_idx)
        if not args.no_force_first and assign.get(first_line) == cid:
            reps[str(cid)] = items[0]["path"]
            continue
        chosen = _medoid(np.array(member_pos), valid, sim, durations,
                         args.min_rep_duration)
        reps[str(cid)] = items[chosen]["path"]

    out = {
        "n_clusters": len(ids),
        "assign": assign,
        "reps": reps,
        "cluster_sizes": cluster_sizes,
        "cluster_confidence": confidence,
        "threshold": args.threshold,
        "merge_sim": args.merge_sim,
        "self_consistency": args.self_consistency,
        "force_first": not args.no_force_first,
        "similarity": {
            "min": float(sim.min()),
            "median": float(np.median(sim)),
            "max": float(sim.max()),
        },
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n_clusters", "cluster_sizes",
                                         "cluster_confidence", "threshold",
                                         "merge_sim", "self_consistency",
                                         "similarity")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
