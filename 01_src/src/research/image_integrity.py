"""Bounded, deterministic cross-image pixel-correspondence evidence.

This module accepts already-authorized bytes, never paths/URLs, and performs no
model calls. It does not decide whether reuse is legitimate or deceptive.
Exact RGBA8 correspondence is the baseline; opt-in OpenCV geometric candidates
and bounded within-image observations retain separate methods and coverage.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, fields
import hashlib
import io
import math
from typing import Sequence
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

METHOD_VERSION = "exact-rgba-dihedral-crops/1.0"


class ImageIntegrityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageSource:
    source_id: str
    data: bytes
    expected_sha256: str | None = None


@dataclass(frozen=True)
class Limits:
    max_images: int = 8
    max_image_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 40 * 1024 * 1024
    max_image_pixels: int = 4_000_000
    max_total_pixels: int = 12_000_000
    max_pairs: int = 28
    max_search_bytes: int = 128 * 1024 * 1024
    max_candidate_comparisons: int = 256
    max_verification_pixels: int = 64_000_000
    max_anchor_tiles: int = 16
    min_region_side: int = 24
    max_local_findings_per_image: int = 6

    def __post_init__(self):
        for field in fields(self):
            if type(getattr(self, field.name)) is not int or getattr(self, field.name) <= 0:
                raise ValueError(f"{field.name} must be a positive integer")
        if self.min_region_side < 8:
            raise ValueError("min_region_side must be at least 8")


class _BudgetExceeded(Exception):
    pass


@dataclass
class _Budget:
    limits: Limits
    search_bytes: int = 0
    candidate_comparisons: int = 0
    verification_pixels: int = 0

    def use(self, name: str, amount: int):
        if getattr(self, name) + amount > getattr(self.limits, "max_" + name):
            raise _BudgetExceeded(name)
        setattr(self, name, getattr(self, name) + amount)


@dataclass
class _Decoded:
    source_id: str
    sha256: str
    image: Image.Image
    raw: bytes
    original_size: tuple[int, int]
    orientation: int
    format: str
    provenance: dict = field(default_factory=dict)


# Matrices map original pixel-edge coordinates to transformed coordinates.
_TRANSFORMS = (
    ("identity", None, lambda w, h: (1, 0, 0, 0, 1, 0)),
    ("rotate_90_ccw", Image.Transpose.ROTATE_90, lambda w, h: (0, 1, 0, -1, 0, w)),
    ("rotate_180", Image.Transpose.ROTATE_180, lambda w, h: (-1, 0, w, 0, -1, h)),
    ("rotate_270_ccw", Image.Transpose.ROTATE_270, lambda w, h: (0, -1, h, 1, 0, 0)),
    ("mirror_horizontal", Image.Transpose.FLIP_LEFT_RIGHT, lambda w, h: (-1, 0, w, 0, 1, 0)),
    ("mirror_vertical", Image.Transpose.FLIP_TOP_BOTTOM, lambda w, h: (1, 0, 0, 0, -1, h)),
    ("transpose", Image.Transpose.TRANSPOSE, lambda w, h: (0, 1, 0, 1, 0, 0)),
    ("transverse", Image.Transpose.TRANSVERSE, lambda w, h: (0, -1, h, -1, 0, w)),
)


def _decode(source: ImageSource, limits: Limits, remaining_pixels: int) -> _Decoded:
    data = source.data
    if not isinstance(data, bytes) or not data:
        raise ImageIntegrityError("invalid_bytes", "Each source must contain nonempty bytes.")
    if len(data) > limits.max_image_bytes:
        raise ImageIntegrityError("byte_limit", "An image exceeds the encoded byte limit.")
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"Rar!", b"7z\xbc\xaf\x27\x1c", b"\x1f\x8b")):
        raise ImageIntegrityError("archive_unsupported", "Archives are not extracted or analyzed; provide individual images.")
    digest = hashlib.sha256(data).hexdigest()
    if source.expected_sha256 is not None and source.expected_sha256 != digest:
        raise ImageIntegrityError("checksum_mismatch", "Source bytes do not match their registered SHA256.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                fmt = original.format
                if fmt not in {"PNG", "JPEG", "WEBP", "BMP", "GIF"}:
                    raise ImageIntegrityError("format_unsupported", "Supported inputs are static PNG/JPEG/WebP/BMP/GIF.")
                w, h = original.size
                if w <= 0 or h <= 0 or w * h > min(limits.max_image_pixels, remaining_pixels):
                    raise ImageIntegrityError("pixel_limit", "Decoded dimensions exceed the image or total pixel limit.")
                # Reject animation before loading all frames; do not enumerate them.
                if getattr(original, "is_animated", False):
                    raise ImageIntegrityError("animation_unsupported", "Animated images are not supported.")
                if original.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                    raise ImageIntegrityError("mode_unsupported", "High-bit-depth, floating-point and CMYK images are not normalized silently.")
                exif = original.getexif()
                orientation = int(exif.get(274, 1))
                selected_tags = {271: "camera_make", 272: "camera_model", 305: "software", 306: "declared_datetime",
                                 33434: "exposure_time", 33437: "f_number", 34855: "iso", 36867: "declared_original_datetime"}
                declared = {}
                try:
                    capture_ifd = exif.get_ifd(0x8769)
                except (ValueError, TypeError, KeyError, OSError):
                    capture_ifd = {}
                for tag, name in selected_tags.items():
                    if tag in exif or tag in capture_ifd:
                        value = exif.get(tag, capture_ifd.get(tag))
                        if isinstance(value, (str, int, float)) or type(value).__name__ == "IFDRational":
                            declared[name] = str(value)[:256]
                for key in ("Software", "Creation Time", "Copyright", "Source", "Description"):
                    value = original.info.get(key)
                    if isinstance(value, str):
                        declared["image_" + key.lower().replace(" ", "_")] = value[:256]
                profile = original.info.get("icc_profile")
                provenance = {"method_version": "image-provenance-inventory/1", "source_sha256": digest,
                              "encoded_bytes": len(data), "declared_fields": declared,
                              "missing_fields": [name for name in selected_tags.values() if name not in declared],
                              "icc_sha256": hashlib.sha256(profile).hexdigest() if isinstance(profile, bytes) else None,
                              "metadata_authenticity": "not_assessed", "missing_metadata_is_suspicious": False,
                              "private_gps_and_serial_fields": "not_collected"}
                original.load()
                oriented = ImageOps.exif_transpose(original)
                try:
                    image = oriented.convert("RGBA")
                finally:
                    oriented.close()
        return _Decoded(source.source_id, digest, image, image.tobytes(), (w, h), orientation, fmt, provenance)
    except ImageIntegrityError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageIntegrityError("pixel_limit", "Pillow refused oversized decoded dimensions.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageIntegrityError("decode_failed", "Image bytes could not be decoded safely.") from exc


def _texture(image: Image.Image, box: tuple[int, int, int, int]) -> dict:
    """Conservative visible-texture gate; not a scientific-content classifier."""
    patch = image.crop(box)
    try:
        patch.thumbnail((64, 64), Image.Resampling.NEAREST)
        white = Image.new("RGBA", patch.size, (255, 255, 255, 255))
        try:
            white.alpha_composite(patch)
            gray = white.convert("L")
            try:
                pixels = list(gray.tobytes())
                width = gray.width
            finally:
                gray.close()
        finally:
            white.close()
    finally:
        patch.close()
    counts = Counter(pixels)
    n = len(pixels)
    entropy = -sum((count / n) * math.log2(count / n) for count in counts.values())
    edges = sum(abs(pixels[i] - pixels[i - 1]) >= 8 for i in range(1, n) if i % width)
    edge_count = max(1, n - n // width)
    edge_fraction = edges / edge_count
    return {"eligible": len(counts) >= 16 and entropy >= 3.0 and edge_fraction >= 0.15,
            "gray_levels": len(counts), "entropy_bits": round(entropy, 4),
            "edge_fraction": round(edge_fraction, 4)}


def _equal(a: bytes, aw: int, b: bytes, bw: int, ax: int, ay: int,
           bx: int, by: int, width: int, height: int, budget: _Budget) -> bool:
    budget.use("verification_pixels", width * height)
    for row in range(height):
        ai, bi = ((ay + row) * aw + ax) * 4, ((by + row) * bw + bx) * 4
        if a[ai:ai + width * 4] != b[bi:bi + width * 4]:
            return False
    return True


def _anchors(image: Image.Image, limits: Limits):
    side = limits.min_region_side
    if min(image.size) < side:
        return
    # Always probe top-left and center; then a deterministic bounded grid.
    positions = [(0, 0), ((image.width - side) // 2, (image.height - side) // 2)]
    grid = max(2, math.isqrt(limits.max_anchor_tiles))
    positions.extend((x * (image.width - side) // (grid - 1),
                      y * (image.height - side) // (grid - 1))
                     for y in range(grid) for x in range(grid))
    for x, y in list(dict.fromkeys(positions))[:limits.max_anchor_tiles]:
        box = (x, y, x + side, y + side)
        if _texture(image, box)["eligible"]:
            yield x, y


def _matches(template: bytes, tw: int, tx: int, ty: int,
             target: bytes, width: int, height: int, side: int, budget: _Budget):
    start = (ty * tw + tx) * 4
    needle = template[start:start + side * 4]
    offset = 0
    while offset < len(target):
        # Charge the whole remaining search span before calling C-level find.
        # This deliberately overcounts successful early matches for a hard bound.
        budget.use("search_bytes", len(target) - offset)
        found = target.find(needle, offset)
        if found < 0:
            return
        offset = found + 1
        if found % 4:
            continue
        y, x = divmod(found // 4, width)
        if x + side > width or y + side > height:
            continue
        budget.use("candidate_comparisons", 1)
        if _equal(template, tw, target, width, tx, ty, x, y, side, side, budget):
            yield x, y


def _expand(a: _Decoded, b: Image.Image, raw: bytes, ax: int, ay: int,
            bx: int, by: int, side: int, budget: _Budget):
    """Expand a verified seed to an exact rectangle, checking every new edge."""
    width = height = side
    while ax > 0 and bx > 0 and _equal(a.raw, a.image.width, raw, b.width,
                                      ax - 1, ay, bx - 1, by, 1, height, budget):
        ax -= 1; bx -= 1; width += 1
    while ax + width < a.image.width and bx + width < b.width and _equal(
            a.raw, a.image.width, raw, b.width, ax + width, ay, bx + width, by, 1, height, budget):
        width += 1
    while ay > 0 and by > 0 and _equal(a.raw, a.image.width, raw, b.width,
                                      ax, ay - 1, bx, by - 1, width, 1, budget):
        ay -= 1; by -= 1; height += 1
    while ay + height < a.image.height and by + height < b.height and _equal(
            a.raw, a.image.width, raw, b.width, ax, ay + height, bx, by + height, width, 1, budget):
        height += 1
    if not _equal(a.raw, a.image.width, raw, b.width, ax, ay, bx, by, width, height, budget):
        raise RuntimeError("Expanded candidate did not pass final pixel verification.")
    return (ax, ay, ax + width, ay + height), (bx, by, bx + width, by + height)


def _inverse_box(box, matrix):
    a, b, c, d, e, f = matrix
    determinant = a * e - b * d
    points = [((e * (x - c) - b * (y - f)) // determinant,
               (-d * (x - c) + a * (y - f)) // determinant)
              for x in (box[0], box[2]) for y in (box[1], box[3])]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _finding(a, b, abox, bbox, transform, matrix, texture):
    original_bbox = _inverse_box(bbox, matrix)
    m = list(matrix)
    m[2] += abox[0] - bbox[0]
    m[5] += abox[1] - bbox[1]
    full = (abox == (0, 0, *a.image.size) and original_bbox == [0, 0, *b.image.size])
    pixels = (abox[2] - abox[0]) * (abox[3] - abox[1])
    return {
        "kind": "exact_full_image" if full else "exact_crop_correspondence",
        "source_a": {"source_id": a.source_id, "sha256": a.sha256, "bbox": list(abox)},
        "source_b": {"source_id": b.source_id, "sha256": b.sha256, "bbox": original_bbox},
        "transform_b_to_a": {"orthogonal_operation": transform,
                             "matrix": [m[:3], m[3:], [0, 0, 1]]},
        "verification": {"method": "all_rgba_pixels_equal", "verified_pixels": pixels,
                         "different_pixels": 0, "texture": texture},
        "method_version": METHOD_VERSION,
        "human_review_required": True,
        "interpretation": "Pixel correspondence only; authorization, shared controls, provenance and scientific context require human review.",
    }


def _compare(a, b, budget, observations):
    limits = budget.limits
    side = limits.min_region_side
    # Check ALL full-image transforms before spending the crop-search budget.
    # Otherwise a failed identity crop scan could hide a full rotated match.
    for full_only in (True, False):
        for transform, operation, matrix_fn in _TRANSFORMS:
            oriented = b.image.copy() if operation is None else b.image.transpose(operation)
            try:
                raw = oriented.tobytes()
                matrix = matrix_fn(*b.image.size)
                if full_only:
                    if a.image.size != oriented.size or not _equal(
                            a.raw, a.image.width, raw, oriented.width, 0, 0, 0, 0,
                            a.image.width, a.image.height, budget):
                        continue
                    box = (0, 0, *a.image.size)
                    texture = _texture(a.image, box)
                    if texture["eligible"]:
                        return _finding(a, b, box, (0, 0, *oriented.size), transform, matrix, texture)
                    observations.append({"source_ids": [a.source_id, b.source_id],
                                         "kind": "low_information_exact_match",
                                         "reason": "Uniform backgrounds, frames and low-texture designs do not establish informative reuse.",
                                         "texture": texture})
                    return None
                if min(*a.image.size, *oriented.size) < side:
                    continue
                anchor_a = a.image.width * a.image.height <= oriented.width * oriented.height
                template_image, template = (a.image, a.raw) if anchor_a else (oriented, raw)
                target_image, target = (oriented, raw) if anchor_a else (a.image, a.raw)
                for tx, ty in _anchors(template_image, limits):
                    for x, y in _matches(template, template_image.width, tx, ty, target,
                                         target_image.width, target_image.height, side, budget):
                        ax, ay, bx, by = (tx, ty, x, y) if anchor_a else (x, y, tx, ty)
                        abox, bbox = _expand(a, oriented, raw, ax, ay, bx, by, side, budget)
                        texture = _texture(a.image, abox)
                        if texture["eligible"]:
                            return _finding(a, b, abox, bbox, transform, matrix, texture)
            finally:
                oriented.close()
    return None


def _near_match(a, b, feature_a, feature_b, cv2, np):
    """ORB candidates + partial-affine RANSAC + spatial/photometric checks.

    ORB is a retrieval heuristic. Only geometrically supported, distributed
    matches with correlated overlapping intensities become review candidates.
    Fixed thumbnail/feature/iteration limits bound work independently of inputs.
    """
    gray_a, points_a, desc_a, scale_a = feature_a
    gray_b, points_b, desc_b, scale_b = feature_b
    if desc_a is None or desc_b is None or min(len(points_a), len(points_b)) < 16:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_b, desc_a, k=2)
    good = [first for pair in matches if len(pair) == 2
            for first, second in [pair] if first.distance < 0.7 * second.distance]
    if len(good) < 16:
        return None
    pa = np.float32([points_a[m.trainIdx].pt for m in good])
    pb = np.float32([points_b[m.queryIdx].pt for m in good])
    matrix, mask = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC,
        ransacReprojThreshold=2.5, maxIters=1000, confidence=0.995, refineIters=10)
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        return None
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    if count < 12 or count / len(good) < 0.6:
        return None
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    if not 0.25 <= scale <= 4:
        return None
    # Require broad support in both originals, rather than a logo/scale bar.
    hull_a = float(cv2.contourArea(cv2.convexHull(pa[inliers])))
    hull_b = float(cv2.contourArea(cv2.convexHull(pb[inliers])))
    if min(hull_a / gray_a.size, hull_b / gray_b.size) < 0.08:
        return None
    warped = cv2.warpAffine(gray_b, matrix, (gray_a.shape[1], gray_a.shape[0]))
    support = cv2.warpAffine(np.ones_like(gray_b), matrix,
                            (gray_a.shape[1], gray_a.shape[0]), flags=cv2.INTER_NEAREST)
    support = cv2.erode(support, np.ones((5, 5), dtype=np.uint8)).astype(bool)
    pixels = int(support.sum())
    if pixels < 1024 or pixels / min(gray_a.size, gray_b.size * scale * scale) < 0.2:
        return None
    av, bv = gray_a[support].astype(np.float64), warped[support].astype(np.float64)
    if min(float(av.std()), float(bv.std())) < 10:
        return None
    correlation = float(np.corrcoef(av, bv)[0, 1])
    mae = float(np.abs(av - bv).mean() / 255)
    if not math.isfinite(correlation) or correlation < 0.80 or mae > 0.18:
        return None
    residual = np.linalg.norm((pb[inliers] @ matrix[:, :2].T + matrix[:, 2]) - pa[inliers], axis=1)
    full = np.eye(3)
    full[:2] = matrix
    sax, say = scale_a
    sbx, sby = scale_b
    restore_a = np.array([[1/sax, 0, (1/sax-1)/2], [0, 1/say, (1/say-1)/2], [0, 0, 1]])
    shrink_b = np.array([[sbx, 0, (sbx-1)/2], [0, sby, (sby-1)/2], [0, 0, 1]])
    full = restore_a @ full @ shrink_b
    def bounds(points, factor, image):
        lo = np.maximum(0, np.floor((points.min(axis=0) + 0.5) / factor - 0.5)).astype(int)
        hi = np.minimum(image.size, np.ceil((points.max(axis=0) + 0.5) / factor - 0.5) + 1).astype(int)
        return [int(lo[0]), int(lo[1]), int(hi[0]), int(hi[1])]
    return {"kind": "near_duplicate_geometric_candidate",
            "source_a": {"source_id": a.source_id, "sha256": a.sha256,
                         "bbox": bounds(pa[inliers], scale_a, a.image)},
            "source_b": {"source_id": b.source_id, "sha256": b.sha256,
                         "bbox": bounds(pb[inliers], scale_b, b.image)},
            "transform_b_to_a": {"matrix": full.tolist(), "model": "partial_affine_similarity",
                                 "coordinate_system": "OpenCV integer pixel centers; EXIF-oriented originals; approximate registration"},
            "verification": {"method": "opencv_orb_ransac_grayscale_overlap", "opencv_version": cv2.__version__,
                             "ratio_matches": len(good), "inliers": count, "inlier_fraction": count / len(good),
                             "median_reprojection_error_thumbnail_px": float(np.median(residual)),
                             "overlap_pixels": pixels, "grayscale_correlation": correlation, "normalized_mae": mae,
                             "thresholds": {"ratio_test": 0.7, "min_inliers": 12, "min_inlier_fraction": 0.6,
                                            "min_hull_fraction": 0.08, "min_correlation": 0.80, "max_normalized_mae": 0.18}},
            "method_version": "opencv-orb-affine-photometric/1", "human_review_required": True,
            "interpretation": "Geometric and grayscale similarity candidate; not proof of common origin or misconduct."}


def _near_features(decoded, cv2, np):
    result = []
    detector = cv2.ORB_create(nfeatures=800, edgeThreshold=15, fastThreshold=10)
    for item in decoded:
        # Transparent RGB is not evidence. Composite against the documented white.
        background = Image.new("RGBA", item.image.size, "white")
        try:
            background.alpha_composite(item.image)
            gray_image = background.convert("L")
            try:
                factor = min(1.0, 640 / max(gray_image.size))
                target = (max(1, round(gray_image.width * factor)), max(1, round(gray_image.height * factor)))
                factors = np.array([target[0] / gray_image.width, target[1] / gray_image.height])
                thumb = gray_image.resize(target, Image.Resampling.LANCZOS)
                try:
                    gray = np.array(thumb, dtype=np.uint8)
                finally:
                    thumb.close()
            finally:
                gray_image.close()
        finally:
            background.close()
        points, descriptors = detector.detectAndCompute(gray, None)
        result.append((gray, points, descriptors, factors))
    return result


def _copy_move_candidate(item, feature, cv2, np):
    """Self-match separated ORB regions, rejecting identity and ambiguous textures."""
    gray, points, descriptors, factors = feature
    if descriptors is None or len(points) < 24:
        return None
    neighbors = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(descriptors, descriptors, k=min(6, len(points)))
    separation = max(24, 0.08 * min(gray.shape))
    pairs, seen = [], set()
    for index, group in enumerate(neighbors):
        origin = np.asarray(points[index].pt)
        remote = [match for match in group if match.trainIdx != index
                  and np.linalg.norm(np.asarray(points[match.trainIdx].pt) - origin) >= separation]
        if len(remote) < 2 or remote[0].distance >= 0.7 * remote[1].distance:
            continue
        other = remote[0].trainIdx
        key = tuple(sorted((index, other)))
        if key in seen:
            continue
        seen.add(key)
        left, right = points[index].pt, points[other].pt
        pairs.append((left, right) if left < right else (right, left))
    if len(pairs) < 12:
        return None
    source, target = np.float32([p[0] for p in pairs]), np.float32([p[1] for p in pairs])
    matrix, mask = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC,
        ransacReprojThreshold=2.5, maxIters=1000, confidence=0.995, refineIters=10)
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        return None
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    if count < 12 or count / len(pairs) < 0.5:
        return None
    p, q = source[inliers], target[inliers]
    hull_p, hull_q = cv2.convexHull(p), cv2.convexHull(q)
    min_area = max(200, gray.size * 0.005)
    if min(cv2.contourArea(hull_p), cv2.contourArea(hull_q)) < min_area:
        return None
    p_low, p_high, q_low, q_high = p.min(axis=0), p.max(axis=0), q.min(axis=0), q.max(axis=0)
    intersection = np.maximum(0, np.minimum(p_high, q_high) - np.maximum(p_low, q_low)).prod()
    if intersection / max(1, min((p_high - p_low).prod(), (q_high - q_low).prod())) > 0.1:
        return None
    support = np.zeros_like(gray)
    cv2.fillConvexPoly(support, np.rint(hull_q).astype(np.int32), 1)
    support = cv2.erode(support, np.ones((5, 5), dtype=np.uint8)).astype(bool)
    warped_support = cv2.warpAffine(np.ones_like(gray), matrix, (gray.shape[1], gray.shape[0]), flags=cv2.INTER_NEAREST)
    support &= warped_support.astype(bool)
    verified_pixels = int(support.sum())
    if verified_pixels < 200:
        return None
    shifted = cv2.warpAffine(gray, matrix, (gray.shape[1], gray.shape[0]))
    actual, predicted = gray[support].astype(float), shifted[support].astype(float)
    if min(actual.std(), predicted.std()) < 10:
        return None
    correlation = float(np.corrcoef(actual, predicted)[0, 1])
    mae = float(np.abs(actual - predicted).mean() / 255)
    if not math.isfinite(correlation) or correlation < 0.85 or mae > 0.12:
        return None
    def box(low, high):
        low = np.maximum(0, np.floor((low + .5) / factors - .5)).astype(int)
        high = np.minimum(item.image.size, np.ceil((high + .5) / factors - .5) + 1).astype(int)
        return [int(low[0]), int(low[1]), int(high[0]), int(high[1])]
    sx, sy = factors
    shrink = np.array([[sx, 0, (sx-1)/2], [0, sy, (sy-1)/2], [0, 0, 1]])
    full = np.eye(3)
    full[:2] = matrix
    full = np.linalg.inv(shrink) @ full @ shrink
    return {"boxes": [box(p_low, p_high), box(q_low, q_high)],
            "evidence": {"method": "opencv_orb_selfmatch_affine_photometric", "opencv_version": cv2.__version__,
                         "matches": len(pairs), "inliers": count, "verified_thumbnail_pixels": verified_pixels,
                         "correlation": correlation, "normalized_mae": mae, "source_to_target_matrix": full.tolist(),
                         "coordinate_system": "EXIF-oriented OpenCV integer pixel centers; approximate registration",
                         "thresholds": {"min_separation_thumbnail_px": separation, "min_inliers": 12,
                                        "min_inlier_fraction": .5, "min_correlation": .85, "max_normalized_mae": .12}}}


def _local_image_checks(decoded, limits):
    """Bounded signals, not a general image-authenticity classifier."""
    from src.research.integrity_review import finding
    import numpy as np
    try:
        import cv2
    except ImportError:
        cv2 = None
    signals, inventories, receipts = [], [], []
    total_omitted = 0
    for item in decoded:
        width, height = item.image.size
        whole = [0, 0, width, height]
        def location(box):
            return {"source_id": item.source_id, "sha256": item.sha256, "bbox": box}
        inventory = finding(rule="image_source_provenance", version="image-provenance-inventory/1",
            locations=[location(whole)], evidence=item.provenance,
            applicability=["Fields declared in the supplied file, not authenticated acquisition records"],
            counterexamples=["Publishers, scanners and ordinary export software strip or replace metadata", "Metadata can be edited; its presence is not proof of authenticity"],
            coverage={"status": "inventory_only", "gps_serial_fields": "not_collected", "authenticity": "not_assessed"},
            summary="源文件哈希与声明字段清单；元数据缺失不构成异常，已有字段也不证明真实性。")
        inventory["severity"] = "information"
        inventories.append(inventory)
        background = Image.new("RGBA", item.image.size, "white")
        try:
            background.alpha_composite(item.image)
            gray_image = background.convert("L")
            try:
                gray_image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                gray = np.array(gray_image, dtype=np.uint8)
            finally:
                gray_image.close()
        finally:
            background.close()
        sy, sx = gray.shape[0] / height, gray.shape[1] / width
        image_signals = []
        checks = {"panel_boundaries": "completed_for_declared_method", "dynamic_range": "completed_for_declared_method",
                  "copy_move": "unavailable" if cv2 is None else "completed_for_declared_method",
                  "provenance": "inventory_only"}
        # Strong, long axis-aligned discontinuities, not arbitrary seams. A
        # natural horizon or intentionally separated panels can satisfy this.
        for axis in (0, 1):
            if min(gray.shape) < 48:
                checks["panel_boundaries"] = "not_applicable_small_image"
                break
            values = gray.astype(np.float64)
            delta = np.abs(np.diff(values, axis=axis))
            scores = np.median(delta, axis=1-axis)
            base = float(np.median(scores))
            mad = float(np.median(np.abs(scores-base)))
            threshold = max(35., base + 8 * max(mad, 1.))
            extent = gray.shape[axis]
            interior = range(max(8, int(extent*.1)), min(len(scores)-8, int(extent*.9)))
            candidates = [pos for pos in interior if scores[pos] >= threshold
                          and float(np.mean(np.take(delta, pos, axis=axis) >= 30)) >= .85]
            chosen = []
            for pos in sorted(candidates, key=lambda pos: (-scores[pos], pos)):
                if any(abs(pos-previous) < 8 for previous in chosen):
                    continue
                chosen.append(pos)
                if len(chosen) >= 2:
                    break
            for pos in chosen:
                if axis == 1:
                    cut, pad = int(round((pos+1)/sx)), max(4, int(12/sx))
                    boxes = [[max(0, cut-pad), 0, cut, height], [cut, 0, min(width, cut+pad), height]]
                    bands = (values[:, max(0,pos-6):pos+1], values[:, pos+1:pos+8])
                else:
                    cut, pad = int(round((pos+1)/sy)), max(4, int(12/sy))
                    boxes = [[0, max(0, cut-pad), width, cut], [0, cut, width, min(height, cut+pad)]]
                    bands = (values[max(0,pos-6):pos+1, :], values[pos+1:pos+8, :])
                image_signals.append(finding(rule="panel_boundary_background_step", version="axis-boundary-background/1",
                    locations=[location(box) for box in boxes],
                    evidence={"axis": "horizontal" if axis == 0 else "vertical", "original_boundary_position": cut,
                              "median_cross_boundary_gray_step": float(scores[pos]), "threshold": threshold,
                              "background_gray_means": [float(b.mean()) for b in bands],
                              "background_gray_sd": [float(b.std()) for b in bands], "analysis_shape": list(gray.shape)},
                    applicability=["Long strong axis-aligned boundaries within the interior 10–90% of an 8-bit image"],
                    counterexamples=["Declared panel assembly or separator", "Natural horizon, specimen boundary or illumination change", "Scanner/export seams and different exposures"],
                    coverage={"scope": "axis_aligned_interior_boundaries", "max_boundaries_per_axis": 2,
                              "max_analysis_side": 512, "arbitrary_seams": "not_assessed"},
                    summary="检测到贯穿区域的边界与背景阶跃；需结合面板声明和原图判断。"))
        low, high = np.percentile(gray, [1, 99])
        low_fraction, high_fraction = float(np.mean(gray <= 1)), float(np.mean(gray >= 254))
        interior = gray[(gray >= 8) & (gray <= 247)]
        if high-low >= 24 and interior.size >= max(100, gray.size*.1) and float(interior.std()) >= 10 and max(low_fraction, high_fraction) >= .2:
            image_signals.append(finding(rule="dynamic_range_endpoint_accumulation", version="grayscale-endpoint-histogram/1",
                locations=[location(whole)],
                evidence={"black_endpoint_fraction": low_fraction, "white_endpoint_fraction": high_fraction,
                          "p01_gray": float(low), "p99_gray": float(high), "interior_gray_sd": float(interior.std()),
                          "endpoint_threshold_fraction": .2, "histogram": np.bincount(gray.ravel(), minlength=256).tolist()},
                applicability=["8-bit visible luminance after compositing transparency onto white", "Enough varying non-endpoint pixels"],
                counterexamples=["Legitimate white chart backgrounds or black gel backgrounds", "Normal contrast/exposure choices", "Thresholded segmentation and clipped publication exports"],
                coverage={"scope": "global_thumbnail_luminance", "max_analysis_side": 512,
                          "original_sensor_range": "unknown", "hidden_pixel_recovery": "not_performed"},
                summary="灰度端点存在集中分布；可能与背景、曝光或显示处理有关，不能据此认定局部篡改。"))
        if cv2 is not None:
            try:
                feature = _near_features([item], cv2, np)[0]
                candidate = _copy_move_candidate(item, feature, cv2, np)
                if candidate:
                    image_signals.insert(0, finding(rule="within_image_geometric_repetition", version="opencv-selfmatch-affine/1",
                        locations=[location(box) for box in candidate["boxes"]], evidence=candidate["evidence"],
                        applicability=["Separated textured regions with distributed feature and grayscale support"],
                        counterexamples=["Legitimate repeated panels, controls or annotations", "Periodic tissue, instrument patterns or templates"],
                        coverage={"max_features": 800, "max_thumbnail_side": 640, "max_neighbors": 6,
                                  "max_ransac_iterations": 1000, "max_supported_regions_per_image": 1,
                                  "nonrigid_or_featureless_reuse": "not_exhaustively_checked"},
                        summary="同一图片的分离区域出现几何与灰度对应；需人工核对原始面板和合理复用。"))
            except (cv2.error, ValueError, MemoryError) as exc:
                checks["copy_move"] = "partial:" + type(exc).__name__
        omitted = max(0, len(image_signals) - limits.max_local_findings_per_image)
        total_omitted += omitted
        signals.extend(image_signals[:limits.max_local_findings_per_image])
        receipts.append({"source_id": item.source_id, "checks": checks, "findings_omitted": omitted,
                         "limits": {"max_thumbnail_side": 640, "max_intensity_side": 512,
                                    "max_findings": limits.max_local_findings_per_image}})
    return signals, inventories, {"status": "partial" if total_omitted or any(
        any(value == "unavailable" or value.startswith("partial:") for value in receipt["checks"].values()) for receipt in receipts) else "completed_for_declared_methods",
        "sources": receipts, "findings_omitted": total_omitted,
        "limitations": ["Boundary and histogram observations are not evidence of manipulation on their own", "Metadata inventories cannot authenticate capture history", "No exhaustive arbitrary seam, local tone-edit or nonrigid copy-move detection"]}


def analyze_image_reuse(sources: Sequence[ImageSource], *, limits: Limits | None = None,
                        near_duplicates: bool = False, local_checks: bool = False) -> dict:
    """Return bounded evidence for pairs of authorized in-memory image sources.

    The adapter must resolve account-owned file IDs and pass registered sha256
    as expected_sha256. This module neither authorizes IDs nor registers output.
    Bboxes use EXIF-oriented, unscaled pixel-edge coordinates [x0,y0,x1,y1).
    A finding's matrix maps source B coordinates into source A coordinates.
    At most one verified region is returned per pair. Missing findings are not
    a clean bill of health: crop anchors are sampled and budgets may stop work.
    Invalid inputs reject the request; budget exhaustion returns partial evidence
    with coverage.complete_within_declared_search=False and a stop reason.
    """
    limits = limits or Limits()
    minimum = 1 if local_checks else 2
    if not isinstance(sources, (list, tuple)) or not minimum <= len(sources) <= limits.max_images:
        raise ImageIntegrityError("image_count", f"Provide between {minimum} and max_images sources.")
    seen, total_bytes = set(), 0
    for source in sources:
        if (not isinstance(source, ImageSource) or not isinstance(source.source_id, str)
                or not source.source_id or len(source.source_id) > 200 or source.source_id in seen):
            raise ImageIntegrityError("source_id", "Sources need unique nonempty IDs of at most 200 characters.")
        if not isinstance(source.data, bytes):
            raise ImageIntegrityError("invalid_bytes", "Source data must be bytes.")
        seen.add(source.source_id)
        total_bytes += len(source.data)
    if total_bytes > limits.max_total_bytes:
        raise ImageIntegrityError("byte_limit", "Encoded images exceed the total byte limit.")
    decoded, total_pixels = [], 0
    try:
        for source in sources:
            item = _decode(source, limits, limits.max_total_pixels - total_pixels)
            decoded.append(item)
            total_pixels += item.image.width * item.image.height
        budget = _Budget(limits)
        findings, observations = [], []
        attempted = completed = 0
        stop_reason = None
        try:
            for i, a in enumerate(decoded):
                for b in decoded[i + 1:]:
                    if attempted >= limits.max_pairs:
                        raise _BudgetExceeded("pairs")
                    attempted += 1
                    finding = _compare(a, b, budget, observations)
                    if finding:
                        findings.append(finding)
                    completed += 1
        except _BudgetExceeded as exc:
            stop_reason = str(exc)
        near_coverage = {"requested": near_duplicates, "status": "not_requested", "attempted_pairs": 0,
                         "max_thumbnail_side": 640, "max_features_per_image": 800,
                         "max_ransac_iterations_per_pair": 2000,
                         "reflection_candidates_per_pair": 2,
                         "unsupported": ["splice boundaries", "local retouching", "capture metadata authenticity", "nonrigid warps"]}
        if near_duplicates:
            try:
                import cv2
                import numpy as np
            except ImportError:
                near_coverage.update(status="unavailable", reason="opencv_or_numpy_not_installed")
            else:
                try:
                    features = _near_features(decoded, cv2, np)
                    exact_pairs = {(f["source_a"]["source_id"], f["source_b"]["source_id"]) for f in findings}
                    for i, a in enumerate(decoded):
                        for j in range(i + 1, len(decoded)):
                            if near_coverage["attempted_pairs"] >= limits.max_pairs:
                                raise _BudgetExceeded("near_pairs")
                            b = decoded[j]
                            if (a.source_id, b.source_id) in exact_pairs:
                                continue
                            near_coverage["attempted_pairs"] += 1
                            candidate = _near_match(a, b, features[i], features[j], cv2, np)
                            if candidate is None:
                                mirrored = b.image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                                try:
                                    reflected = _Decoded(b.source_id, b.sha256, mirrored, b"", b.original_size, b.orientation, b.format)
                                    mirrored_features = _near_features([reflected], cv2, np)[0]
                                    candidate = _near_match(a, reflected, features[i], mirrored_features, cv2, np)
                                    if candidate:
                                        box = candidate["source_b"]["bbox"]
                                        candidate["source_b"]["bbox"] = [b.image.width - box[2], box[1], b.image.width - box[0], box[3]]
                                        reflection = np.array([[-1., 0, b.image.width - 1], [0, 1, 0], [0, 0, 1]])
                                        candidate["transform_b_to_a"]["matrix"] = (np.asarray(candidate["transform_b_to_a"]["matrix"]) @ reflection).tolist()
                                        candidate["transform_b_to_a"]["model"] = "reflected_partial_affine_similarity"
                                finally:
                                    mirrored.close()
                            if candidate:
                                findings.append(candidate)
                    near_coverage.update(status="completed_for_declared_method", opencv_version=cv2.__version__)
                except _BudgetExceeded:
                    near_coverage.update(status="partial", reason="max_pairs")
                except (cv2.error, ValueError, MemoryError) as exc:
                    near_coverage.update(status="partial", reason=type(exc).__name__)
        from src.research.integrity_review import finding as common_finding
        common = [common_finding(
            rule=item["kind"], version=item["method_version"],
            locations=[item["source_a"], item["source_b"]],
            evidence={"verification": item["verification"], "transform_b_to_a": item["transform_b_to_a"]},
            applicability=["Authorized static images with visible texture", "EXIF-oriented coordinates; inspect both originals"],
            counterexamples=["Legitimate shared controls or licensed references", "Common labels, diagram layouts and image templates"],
            coverage={"scope": "located_regions_only", "near_duplicate": item["kind"].startswith("near_"),
                      "not_checked": ["splice", "local_edit", "metadata_authenticity"]}, summary=item["interpretation"])
            for item in findings]
        local_findings, provenance_findings = [], []
        local_coverage = {"status": "not_requested"}
        if local_checks:
            local_findings, provenance_findings, local_coverage = _local_image_checks(decoded, limits)
            common.extend(local_findings)
            common.extend(provenance_findings)
        partial = bool(stop_reason) or near_coverage["status"] in {"unavailable", "partial"} or local_coverage["status"] == "partial"
        return {
            "common_findings": common, "near_duplicate_coverage": near_coverage,
            "local_findings": local_findings, "provenance_findings": provenance_findings,
            "local_analysis_coverage": local_coverage,
            "unsupported_checks": {"splice": "bounded_axis_boundary_observations" if local_checks else "not_checked",
                                   "local_edit": "bounded_selfmatch_and_endpoint_observations" if local_checks else "not_checked",
                                   "metadata_authenticity": "not_assessed; declared_field_inventory_only" if local_checks else "not_checked"},
            "method_version": METHOD_VERSION,
            "status": "partial" if partial else "completed_with_declared_limits",
            "human_review_required": True,
            "sources": [{"source_id": item.source_id, "sha256": item.sha256,
                         "format": item.format, "original_size": list(item.original_size),
                         "normalized_size": list(item.image.size), "exif_orientation": item.orientation,
                         "provenance": item.provenance}
                        for item in decoded],
            "coordinate_system": "EXIF-oriented unscaled pixel edges; half-open bboxes; RGBA8 equality; no ICC color management",
            "findings": findings, "observations": observations,
            "coverage": {"input_images": len(decoded), "total_pairs": len(decoded) * (len(decoded) - 1) // 2,
                         "attempted_pairs": attempted, "completed_pairs": completed,
                         "complete_within_declared_search": not partial,
                         "stop_reason": stop_reason, "decoded_pixels": total_pixels,
                         "search_bytes": budget.search_bytes,
                         "candidate_comparisons": budget.candidate_comparisons,
                         "verification_pixels": budget.verification_pixels,
                         "crop_search": "texture-gated sampled anchors; first verified rectangle per pair; not exhaustive",
                         "texture_gate": {"min_gray_levels": 16, "min_entropy_bits": 3.0,
                                          "min_edge_fraction": 0.15, "min_edge_delta": 8,
                                          "sample_max_side": 64, "alpha_background": "white"},
                         "limits": asdict(limits)},
            "limitations": [
                "No conclusion about fabrication, misconduct, authorship or legitimate authorization follows from a match.",
                "No-match is not evidence of integrity; sampling and work budgets can miss reuse.",
                "Exact search verifies decoded pixels under translation, orthogonal rotations and mirrors. JPEG recompression, scaling and arbitrary rotation are outside exact-pixel coverage. Optional ORB/RANSAC search provides heuristic candidates only when distributed geometry and grayscale overlap pass thresholds; it is not exhaustive.",
                "No ELA accusation, automatic scientific interpretation or OCR. Optional local checks cover one separated ORB region pair, long axis boundaries and global endpoint histograms; this is limited detector coverage, not capture authentication.",
                "Static 8-bit-compatible PNG/JPEG/WebP/BMP/GIF only; archives, animated, TIFF, high-bit-depth and CMYK sources are rejected.",
            ],
            "counterexamples": [
                "Identical solid colors or chart frames are low-information coincidences and are suppressed as reuse evidence.",
                "Legitimately shared control panels, declared reference images, templates or licensed material can produce fully verified matches.",
                "A verified rectangle may contain common labels or design elements; a reviewer must inspect both originals and their provenance.",
            ],
        }
    finally:
        for item in decoded:
            item.image.close()
