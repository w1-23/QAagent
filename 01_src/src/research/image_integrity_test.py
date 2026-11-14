"""Deterministic synthetic fixtures only; no research data, model or network.

Run: python -B -X utf8 01_src/src/research/image_integrity_test.py
"""
from dataclasses import replace
import hashlib
import importlib.util
import io
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

_spec = importlib.util.spec_from_file_location(
    "_image_integrity_under_test", Path(__file__).with_name("image_integrity.py"))
integrity = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = integrity
_spec.loader.exec_module(integrity)


def texture(seed=1, size=(96, 80)):
    rng = random.Random(seed)
    return Image.frombytes("RGB", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3)))


def encode(image, fmt="PNG", **kwargs):
    output = io.BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def source(name, image, **kwargs):
    return integrity.ImageSource(name, encode(image, **kwargs))


class ImageReuseTests(unittest.TestCase):
    def analyze(self, a, b, **kwargs):
        return integrity.analyze_image_reuse([source("a", a), source("b", b)], **kwargs)

    def assert_geometry(self, finding, a, b):
        abox, bbox = finding["source_a"]["bbox"], finding["source_b"]["bbox"]
        matrix = finding["transform_b_to_a"]["matrix"]
        for x in range(bbox[0], bbox[2]):
            for y in range(bbox[1], bbox[3]):
                ax = matrix[0][0] * (x + 0.5) + matrix[0][1] * (y + 0.5) + matrix[0][2]
                ay = matrix[1][0] * (x + 0.5) + matrix[1][1] * (y + 0.5) + matrix[1][2]
                self.assertTrue(abox[0] <= ax < abox[2])
                self.assertTrue(abox[1] <= ay < abox[3])
                self.assertEqual(a.getpixel((int(ax), int(ay))), b.getpixel((x, y)))

    def test_same_pixels_different_encoding_has_two_original_digests(self):
        image = texture()
        inputs = [integrity.ImageSource("a", encode(image, compress_level=0)),
                  integrity.ImageSource("b", encode(image, compress_level=9))]
        report = integrity.analyze_image_reuse(inputs)
        finding = report["findings"][0]
        self.assertEqual(finding["kind"], "exact_full_image")
        self.assertNotEqual(finding["source_a"]["sha256"], finding["source_b"]["sha256"])
        for entry in inputs:
            side = finding["source_" + entry.source_id]
            self.assertEqual(side["sha256"], hashlib.sha256(entry.data).hexdigest())
        self.assertEqual(finding["verification"]["different_pixels"], 0)
        self.assertTrue(finding["human_review_required"])
        self.assert_geometry(finding, image, image)

    def test_all_eight_orthogonal_transform_matrices(self):
        original = texture(size=(70, 54))
        for _, operation, _ in integrity._TRANSFORMS:
            with self.subTest(operation=operation):
                transformed = original.copy() if operation is None else original.transpose(operation)
                report = self.analyze(original, transformed)
                self.assertEqual(len(report["findings"]), 1)
                finding = report["findings"][0]
                self.assertEqual(finding["kind"], "exact_full_image")
                self.assert_geometry(finding, original, transformed)

    def test_full_rotation_is_checked_before_crop_search_budget(self):
        original = texture(size=(80, 80))
        rotated = original.transpose(Image.Transpose.ROTATE_90)
        report = self.analyze(original, rotated, limits=integrity.Limits(max_search_bytes=1))
        self.assertEqual(report["findings"][0]["kind"], "exact_full_image")
        self.assertEqual(report["coverage"]["search_bytes"], 0)
        self.assertTrue(report["coverage"]["complete_within_declared_search"])

    def test_exact_crop_detected_in_both_source_orders(self):
        original = texture(size=(120, 100))
        cropped = original.crop((17, 23, 81, 71))
        for a, b in ((original, cropped), (cropped, original)):
            with self.subTest(size_a=a.size):
                report = self.analyze(a, b)
                finding = report["findings"][0]
                self.assertEqual(finding["kind"], "exact_crop_correspondence")
                self.assert_geometry(finding, a, b)
                large_bbox = finding["source_a" if a is original else "source_b"]["bbox"]
                self.assertEqual(large_bbox, [17, 23, 81, 71])

    def test_rotated_and_mirrored_crops_are_verified_in_original_coordinates(self):
        original = texture(size=(120, 100))
        crop = original.crop((11, 19, 83, 75))
        for operation in (Image.Transpose.ROTATE_90, Image.Transpose.FLIP_LEFT_RIGHT,
                          Image.Transpose.TRANSVERSE):
            with self.subTest(operation=operation):
                other = crop.transpose(operation)
                report = self.analyze(original, other)
                finding = report["findings"][0]
                self.assertEqual(finding["source_a"]["bbox"], [11, 19, 83, 75])
                self.assert_geometry(finding, original, other)

    def test_shared_rectangular_region_inside_two_different_figures(self):
        a, b = texture(1, (128, 128)), texture(2, (128, 128))
        shared = texture(3, (64, 64))
        a.paste(shared, (32, 32))
        b.paste(shared, (47, 20))
        finding = self.analyze(a, b)["findings"][0]
        self.assertEqual(finding["source_a"]["bbox"], [32, 32, 96, 96])
        self.assertEqual(finding["source_b"]["bbox"], [47, 20, 111, 84])
        self.assert_geometry(finding, a, b)

    def test_solid_color_and_shared_frame_are_not_informative_evidence(self):
        uniform = Image.new("RGB", (96, 96), "white")
        frame = uniform.copy()
        ImageDraw.Draw(frame).rectangle((5, 5, 90, 90), outline="black", width=2)
        for image in (uniform, frame):
            with self.subTest(frame=image is frame):
                report = self.analyze(image, image)
                self.assertEqual(report["findings"], [])
                self.assertEqual(report["observations"][0]["kind"], "low_information_exact_match")
        self.assertEqual(self.analyze(uniform, frame)["findings"], [])

    def test_legitimate_shared_control_requires_context_not_misconduct_verdict(self):
        control = texture()
        report = self.analyze(control, control)
        self.assertEqual(len(report["findings"]), 1)
        self.assertTrue(report["human_review_required"])
        self.assertIn("Legitimately shared control", " ".join(report["counterexamples"]))
        self.assertIn("No conclusion about fabrication", " ".join(report["limitations"]))
        self.assertNotIn("fraud_score", report)
        self.assertIn("scientific context", report["findings"][0]["interpretation"])

    def test_distinct_textures_produce_no_correspondence(self):
        report = self.analyze(texture(1), texture(2))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["status"], "completed_with_declared_limits")
        self.assertIn("not exhaustive", report["coverage"]["crop_search"])

    def test_jpeg_recompression_is_outside_exact_pixel_coverage(self):
        image = texture()
        report = integrity.analyze_image_reuse(
            [source("png", image), source("jpeg", image, fmt="JPEG", quality=85)])
        self.assertEqual(report["findings"], [])
        self.assertIn("JPEG recompression", " ".join(report["limitations"]))

    def test_exif_is_normalized_without_rescaling(self):
        a = texture(size=(80, 60))
        b = a.transpose(Image.Transpose.ROTATE_90)
        exif = Image.Exif()
        exif[274] = 6
        report = integrity.analyze_image_reuse([source("a", a), source("b", b, exif=exif)])
        self.assertEqual(report["sources"][1]["original_size"], [60, 80])
        self.assertEqual(report["sources"][1]["normalized_size"], [80, 60])
        self.assertEqual(report["findings"][0]["kind"], "exact_full_image")
        self.assertEqual(report["findings"][0]["source_b"]["bbox"], [0, 0, 80, 60])

    def test_invisible_alpha_texture_does_not_pass_texture_gate(self):
        invisible = texture().convert("RGBA")
        invisible.putalpha(0)
        report = self.analyze(invisible, invisible)
        self.assertEqual(report["findings"], [])

    def test_registered_checksum_is_required_to_match_when_supplied(self):
        image = source("a", texture())
        bad = integrity.ImageSource("b", image.data, "0" * 64)
        with self.assertRaises(integrity.ImageIntegrityError) as raised:
            integrity.analyze_image_reuse([image, bad])
        self.assertEqual(raised.exception.code, "checksum_mismatch")

    def test_archives_and_corrupt_bytes_are_rejected(self):
        valid = source("valid", texture())
        for data, code in ((b"PK\x03\x04" + b"not expanded", "archive_unsupported"),
                           (b"Rar!not expanded", "archive_unsupported"),
                           (b"this is not an image", "decode_failed")):
            with self.subTest(code=code):
                with self.assertRaises(integrity.ImageIntegrityError) as raised:
                    integrity.analyze_image_reuse([valid, integrity.ImageSource("bad", data)])
                self.assertEqual(raised.exception.code, code)

    def test_animation_and_high_bit_depth_rejected(self):
        valid = source("valid", texture())
        frames = [Image.new("RGB", (40, 40), color) for color in ("red", "blue")]
        stream = io.BytesIO()
        frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:], duration=100)
        high_depth = encode(Image.new("I;16", (40, 40), 500))
        for payload, code in ((stream.getvalue(), "animation_unsupported"), (high_depth, "mode_unsupported")):
            with self.subTest(code=code):
                with self.assertRaises(integrity.ImageIntegrityError) as raised:
                    integrity.analyze_image_reuse([valid, integrity.ImageSource("other", payload)])
                self.assertEqual(raised.exception.code, code)

    def test_encoded_byte_and_image_count_limits(self):
        images = [source("a", texture()), source("b", texture(2))]
        for limits, expected in ((integrity.Limits(max_images=1), "image_count"),
                                 (integrity.Limits(max_image_bytes=1), "byte_limit"),
                                 (integrity.Limits(max_total_bytes=1), "byte_limit")):
            with self.subTest(expected=expected):
                with self.assertRaises(integrity.ImageIntegrityError) as raised:
                    integrity.analyze_image_reuse(images, limits=limits)
                self.assertEqual(raised.exception.code, expected)

    def test_pixel_dimensions_checked_before_decode(self):
        images = [source("a", texture()), source("b", texture(2))]
        with patch.object(Image.Image, "load", side_effect=AssertionError("pixels decoded before bounds check")):
            with self.assertRaises(integrity.ImageIntegrityError) as raised:
                integrity.analyze_image_reuse(images, limits=integrity.Limits(max_image_pixels=100))
        self.assertEqual(raised.exception.code, "pixel_limit")
        with self.assertRaises(integrity.ImageIntegrityError) as raised:
            integrity.analyze_image_reuse(images, limits=integrity.Limits(max_total_pixels=8000))
        self.assertEqual(raised.exception.code, "pixel_limit")

    def test_work_budgets_return_explicit_partial_coverage(self):
        a, b = texture(1), texture(2)
        for field in ("max_search_bytes", "max_verification_pixels"):
            with self.subTest(field=field):
                limits = replace(integrity.Limits(), **{field: 1})
                report = self.analyze(a, b, limits=limits)
                self.assertEqual(report["status"], "partial")
                self.assertFalse(report["coverage"]["complete_within_declared_search"])
                self.assertEqual(report["coverage"]["stop_reason"], field[4:])
                self.assertLessEqual(report["coverage"][field[4:]], 1)

    def test_candidate_budget_stops_repeated_false_row_matches(self):
        a, b = texture(1, (24, 24)), texture(2, (80, 80))
        row = a.crop((0, 0, 24, 1))
        b.paste(row, (0, 0))
        b.paste(row, (0, 32))
        report = self.analyze(a, b, limits=integrity.Limits(max_candidate_comparisons=1))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["coverage"]["stop_reason"], "candidate_comparisons")
        self.assertEqual(report["coverage"]["candidate_comparisons"], 1)

    def test_pair_budget_retains_completed_evidence(self):
        image = texture()
        report = integrity.analyze_image_reuse(
            [source(str(i), image) for i in range(3)], limits=integrity.Limits(max_pairs=1))
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["coverage"]["completed_pairs"], 1)
        self.assertEqual(report["coverage"]["total_pairs"], 3)
        self.assertEqual(report["coverage"]["stop_reason"], "pairs")

    def test_deterministic_output_and_unique_source_ids(self):
        images = [source("a", texture()), source("b", texture())]
        self.assertEqual(integrity.analyze_image_reuse(images), integrity.analyze_image_reuse(images))
        with self.assertRaises(integrity.ImageIntegrityError) as raised:
            integrity.analyze_image_reuse([images[0], images[0]])
        self.assertEqual(raised.exception.code, "source_id")


if __name__ == "__main__":
    unittest.main(verbosity=2)
