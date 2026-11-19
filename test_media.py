from __future__ import annotations

import builtins
import copy
import http.client
import io
import json
import os
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import quote, urlencode

import app


WAIT = 5
BASE_MTIME = 1_700_000_000


class _Workers:
    """Release test gates and join every worker before surrounding mocks stop."""

    def __init__(self, *gates):
        self.gates = gates
        self.threads = []

    def __enter__(self):
        return self

    def start(self, callback):
        future = Future()

        def run():
            try:
                future.set_result(callback())
            except BaseException as exc:
                future.set_exception(exc)

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return future

    def __exit__(self, exc_type, exc, tb):
        for gate in self.gates:
            gate.set()
        for thread in self.threads:
            thread.join(WAIT)
        if any(thread.is_alive() for thread in self.threads):
            raise AssertionError("A media test worker did not stop")


class _ObservedRLock:
    """Expose lock attempts without depending on thread scheduling or sleeps."""

    def __init__(self, expected_attempts):
        self.lock = threading.RLock()
        self.counter_lock = threading.Lock()
        self.expected_attempts = expected_attempts
        self.attempts = 0
        self.all_attempted = threading.Event()

    def acquire(self, *args, **kwargs):
        with self.counter_lock:
            self.attempts += 1
            if self.attempts >= self.expected_attempts:
                self.all_attempted.set()
        return self.lock.acquire(*args, **kwargs)

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


class _IsolatedCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        temporary = TemporaryDirectory(prefix="icloudpd-media-test-")
        self.addCleanup(temporary.cleanup)
        self.temp_root = Path(temporary.name).resolve()
        self.media_root = self.temp_root / "downloads"
        self.thumb_root = self.temp_root / "thumbs"
        self.media_root.mkdir()
        self.thumb_root.mkdir()
        self.patch_app("THUMB_DIR", self.thumb_root)
        self.patch_app("MEDIA_CACHE", {"at": 0.0, "items": [], "stats": {}, "dir": None})
        self.patch_app("MEDIA_LOCK", threading.RLock())
        self.patch_app("THUMB_SEMAPHORE", threading.BoundedSemaphore(4))
        self.config = {
            "username": "",
            "domain": "com",
            "download_dir": str(self.media_root),
            "cookie_dir": str(self.temp_root / "cookies"),
            "recent_dirs": [],
        }
        self.patch_app("load_config", side_effect=lambda: copy.deepcopy(self.config))

    def patch_app(self, name, *args, **kwargs):
        patcher = mock.patch.object(app, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def media_file(self, relative, data=b"photo", mtime=BASE_MTIME):
        path = self.media_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))
        return path

    def scan(self, force=False):
        return app.scan_media(str(self.media_root), force=force)

    def assert_permits_restored(self, semaphore, capacity):
        acquired = 0
        try:
            for _ in range(capacity):
                self.assertTrue(semaphore.acquire(blocking=False), "Thumbnail permit leaked")
                acquired += 1
            extra = semaphore.acquire(blocking=False)
            if extra:
                acquired += 1
            self.assertFalse(extra, "Thumbnail semaphore was over-released")
        finally:
            for _ in range(acquired):
                semaphore.release()


class ScanMediaTests(_IsolatedCase):
    def test_default_media_limit_is_30000(self):
        self.assertEqual(getattr(app, "MAX_MEDIA_ITEMS", None), 30000)

    def test_private_scan_uses_scandir_and_accepts_a_path(self):
        self.media_file("first.jpg", b"123")
        self.media_file("album/second.MOV", b"4567")
        with mock.patch.object(app.os, "scandir", wraps=os.scandir) as scandir, mock.patch.object(
            app.os, "walk", side_effect=AssertionError("Use scandir, not os.walk")
        ):
            items, stats = app._scan_media(self.media_root)
        self.assertGreaterEqual(scandir.call_count, 2)
        self.assertEqual({item["rel"] for item in items}, {"first.jpg", "album/second.MOV"})
        self.assertEqual((stats["count"], stats["photos"], stats["videos"], stats["bytes"]), (2, 1, 1, 7))

    def test_latest_n_items_keep_full_statistics_and_reverse_path_tiebreak(self):
        self.patch_app("MAX_MEDIA_ITEMS", 3)
        fixtures = [
            ("old.jpg", 2, 10),
            ("middle.mov", 3, 20),
            ("a.jpg", 5, 30),
            ("z.png", 7, 30),
            ("nested/newest.mp4", 11, 40),
            ("nested/older.jpeg", 13, 15),
        ]
        for relative, size, age in fixtures:
            self.media_file(relative, b"x" * size, BASE_MTIME + age)
        items, stats = self.scan()
        self.assertEqual([item["rel"] for item in items], ["nested/newest.mp4", "z.png", "a.jpg"])
        keys = [(item["mtime"], item["rel"]) for item in items]
        self.assertEqual(keys, sorted(keys, reverse=True))
        self.assertEqual((stats["count"], stats["photos"], stats["videos"], stats["bytes"]), (6, 4, 2, 41))
        self.assertTrue(stats["truncated"])
        self.assertEqual(stats["latest_at"], items[0]["modified_at"])

    def test_truncated_is_true_only_when_count_exceeds_limit(self):
        self.patch_app("MAX_MEDIA_ITEMS", 3)
        for count in range(5):
            with self.subTest(count=count):
                if count:
                    self.media_file(f"{count}.jpg", b"x")
                items, stats = self.scan(force=True)
                self.assertEqual(len(items), min(count, 3))
                self.assertEqual(stats["count"], count)
                self.assertEqual(stats["bytes"], count)
                self.assertEqual(stats["truncated"], count > 3)

    def test_hidden_entries_and_non_media_are_excluded(self):
        self.media_file("visible.JPG", b"12")
        self.media_file("album/clip.Mp4", b"345")
        for relative in (
            ".hidden.jpg", ".hidden/inside.jpg", "album/.hidden.mov",
            "album/.cache/inside.png", "notes.txt", "album/sidecar.json",
        ):
            self.media_file(relative, b"ignored")
        (self.media_root / "empty.jpg").mkdir()
        items, stats = self.scan()
        self.assertEqual({item["rel"] for item in items}, {"visible.JPG", "album/clip.Mp4"})
        self.assertEqual((stats["count"], stats["photos"], stats["videos"], stats["bytes"]), (2, 1, 1, 5))
        self.assertFalse(stats["truncated"])

    def test_symbolic_link_files_and_directories_are_excluded(self):
        original = self.media_file("original.jpg", b"123")
        outside = self.temp_root / "other-album"
        outside.mkdir()
        (outside / "outside.mov").write_bytes(b"not part of downloads")
        try:
            (self.media_root / "alias.jpg").symlink_to(original)
            (self.media_root / "linked-album").symlink_to(outside, target_is_directory=True)
            (self.media_root / "dangling.jpg").symlink_to(self.temp_root / "missing.jpg")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable: {exc}")
        items, stats = self.scan()
        self.assertEqual([item["rel"] for item in items], ["original.jpg"])
        self.assertEqual((stats["count"], stats["bytes"]), (1, 3))

    @unittest.skipUnless(os.name == "nt", "Windows directory junctions only")
    def test_directory_junctions_are_not_traversed(self):
        junction = mock.Mock()
        junction.name = "junction"
        junction.path = str(self.media_root / "junction")
        junction.is_dir.return_value = True
        junction.stat.return_value.st_reparse_tag = app.stat.IO_REPARSE_TAG_MOUNT_POINT
        entries = mock.MagicMock()
        entries.__enter__.return_value = [junction]

        def scan_directory(path):
            self.assertEqual(str(path), str(self.media_root), "Junction must not be traversed")
            return entries

        with mock.patch.object(app.os, "scandir", side_effect=scan_directory) as scanner:
            items, stats = self.scan()
        scanner.assert_called_once()
        self.assertEqual(items, [])
        self.assertEqual(stats["count"], 0)

    def test_missing_directory_has_empty_statistics(self):
        items, stats = app.scan_media(str(self.temp_root / "missing"))
        self.assertEqual(items, [])
        self.assertFalse(stats["exists"])
        self.assertFalse(stats["truncated"])
        self.assertEqual((stats["count"], stats["photos"], stats["videos"], stats["bytes"]), (0, 0, 0, 0))

    def test_cache_expires_at_twenty_seconds_without_sleeping(self):
        clock = mock.Mock(wraps=time)
        clock.time.return_value = clock.monotonic.return_value = 1000.0
        self.patch_app("time", clock)
        self.media_file("first.jpg")
        with mock.patch.object(app, "_scan_media", wraps=app._scan_media) as scanner:
            self.assertEqual(self.scan()[1]["count"], 1)
            self.media_file("second.jpg")
            clock.time.return_value = clock.monotonic.return_value = 1019.99
            self.assertEqual(self.scan()[1]["count"], 1)
            self.assertEqual(scanner.call_count, 1)
            clock.time.return_value = clock.monotonic.return_value = 1020.0
            self.assertEqual(self.scan()[1]["count"], 2)
            self.assertEqual(scanner.call_count, 2)
        self.assertIsInstance(scanner.call_args.args[0], Path)

    def test_force_refresh_bypasses_fresh_same_directory_cache(self):
        self.media_file("first.jpg")
        with mock.patch.object(app, "_scan_media", wraps=app._scan_media) as scanner:
            self.assertEqual(self.scan()[1]["count"], 1)
            self.media_file("second.jpg")
            self.assertEqual(self.scan()[1]["count"], 1)
            self.assertEqual(self.scan(force=True)[1]["count"], 2)
            self.scan(force=True)
            self.assertEqual(scanner.call_count, 3)

    def test_switching_directories_does_not_reuse_other_directory_items(self):
        self.media_file("first.jpg")
        other = self.temp_root / "other-downloads"
        other.mkdir()
        (other / "second.mp4").write_bytes(b"video")
        with mock.patch.object(app, "_scan_media", wraps=app._scan_media) as scanner:
            first, _ = self.scan()
            second, _ = app.scan_media(str(other))
            again, _ = self.scan()
        self.assertEqual([item["rel"] for item in first], ["first.jpg"])
        self.assertEqual([item["rel"] for item in second], ["second.mp4"])
        self.assertEqual(again, first)
        self.assertEqual(scanner.call_count, 3)

    def test_media_lock_is_held_during_the_scan(self):
        self.media_file("first.jpg")
        entered, release = threading.Event(), threading.Event()
        real_scan = app._scan_media

        def blocked(root):
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError("Scan gate timed out")
            return real_scan(root)

        with mock.patch.object(app, "_scan_media", side_effect=blocked), _Workers(release) as workers:
            result = workers.start(self.scan)
            self.assertTrue(entered.wait(WAIT))
            acquired = app.MEDIA_LOCK.acquire(blocking=False)
            if acquired:
                app.MEDIA_LOCK.release()
            release.set()
            self.assertEqual(result.result(WAIT)[1]["count"], 1)
            self.assertFalse(acquired, "MEDIA_LOCK must cover the filesystem scan")

    def test_concurrent_normal_requests_share_one_scan(self):
        self.media_file("first.jpg")
        entered, release = threading.Event(), threading.Event()
        lock = self.patch_app("MEDIA_LOCK", _ObservedRLock(4))
        real_scan = app._scan_media

        def blocked(root):
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError("Scan gate timed out")
            return real_scan(root)

        with mock.patch.object(app, "_scan_media", side_effect=blocked) as scanner, _Workers(release) as workers:
            first = workers.start(self.scan)
            self.assertTrue(entered.wait(WAIT))
            others = [workers.start(self.scan) for _ in range(3)]
            self.assertTrue(lock.all_attempted.wait(WAIT))
            release.set()
            expected = first.result(WAIT)
            for result in others:
                self.assertEqual(result.result(WAIT), expected)
            self.assertEqual(scanner.call_count, 1)

    def test_concurrent_force_request_performs_its_own_refresh(self):
        self.media_file("first.jpg")
        entered, release = threading.Event(), threading.Event()
        lock = self.patch_app("MEDIA_LOCK", _ObservedRLock(2))
        real_scan = app._scan_media
        calls = 0

        def snapshot_then_block(root):
            nonlocal calls
            calls += 1
            result = real_scan(root)
            if calls == 1:
                entered.set()
                if not release.wait(WAIT):
                    raise AssertionError("Scan gate timed out")
            return result

        with mock.patch.object(app, "_scan_media", side_effect=snapshot_then_block) as scanner, _Workers(release) as workers:
            first = workers.start(self.scan)
            self.assertTrue(entered.wait(WAIT))
            self.media_file("second.jpg")
            forced = workers.start(lambda: self.scan(force=True))
            self.assertTrue(lock.all_attempted.wait(WAIT))
            release.set()
            self.assertEqual(first.result(WAIT)[1]["count"], 1)
            self.assertEqual(forced.result(WAIT)[1]["count"], 2)
            self.assertEqual(scanner.call_count, 2)


class ThumbnailTests(_IsolatedCase):
    @staticmethod
    def write_thumbnail(src, dest, width):
        dest.write_bytes(b"generated thumbnail")
        return True

    def test_successful_result_is_cached(self):
        source = self.media_file("source.jpg")
        generator = self.patch_app("make_image_thumb", side_effect=self.write_thumbnail)
        first = app.get_thumbnail(source, 320)
        self.assertIsNotNone(first)
        self.assertEqual(first.read_bytes(), b"generated thumbnail")
        self.assertEqual(app.get_thumbnail(source, 320), first)
        generator.assert_called_once()
        self.assertEqual(list(self.thumb_root.iterdir()), [first])

    def test_concurrent_success_generates_same_path_and_width_only_once(self):
        source = self.media_file("source.jpg")
        entered, release, duplicate = (threading.Event() for _ in range(3))
        barrier = threading.Barrier(4)
        counter_lock = threading.Lock()
        calls = 0

        def generate(src, dest, width):
            nonlocal calls
            with counter_lock:
                calls += 1
                if calls > 1:
                    duplicate.set()
            entered.set()
            if not release.wait(WAIT):
                raise AssertionError("Thumbnail gate timed out")
            return self.write_thumbnail(src, dest, width)

        def request():
            barrier.wait(WAIT)
            return app.get_thumbnail(source, 320)

        with mock.patch.object(app, "make_image_thumb", side_effect=generate) as generator, _Workers(release) as workers:
            results = [workers.start(request) for _ in range(4)]
            self.assertTrue(entered.wait(WAIT))
            # 保持首个生成器在执行中，观察是否出现重复生成。
            duplicate.wait(0.2)
            release.set()
            paths = [result.result(WAIT) for result in results]
            self.assertEqual(generator.call_count, 1)
            self.assertIsNotNone(paths[0])
            self.assertEqual(paths, [paths[0]] * 4)
            self.assertEqual(paths[0].read_bytes(), b"generated thumbnail")
        self.assertEqual(list(self.thumb_root.iterdir()), [paths[0]])
        self.assert_permits_restored(app.THUMB_SEMAPHORE, 4)

    def check_failed_generation(self, exception=False):
        for extension, generator_name in (("jpg", "make_image_thumb"), ("mp4", "make_video_thumb")):
            with self.subTest(extension=extension):
                source = self.media_file(f"source.{extension}")
                failure = RuntimeError("intentional generator failure")
                temporary_paths = []

                def fail(src, dest, width):
                    temporary_paths.append(dest)
                    dest.write_bytes(b"partial jpg")
                    if exception:
                        raise failure
                    return False

                with mock.patch.object(app, generator_name, side_effect=fail):
                    try:
                        result = app.get_thumbnail(source, 320)
                    except RuntimeError as exc:
                        # 允许异常向上传递，但必须释放缓存文件与锁。
                        self.assertTrue(exception)
                        self.assertIs(exc, failure)
                    else:
                        self.assertIsNone(result)
                self.assert_permits_restored(app.THUMB_SEMAPHORE, 4)
                self.assertTrue(temporary_paths, "The generator was not invoked")
                self.assertTrue(all(not path.exists() for path in temporary_paths))
                self.assertFalse(app.thumb_cache_path(source, 320).exists())
                with mock.patch.object(app, generator_name, side_effect=self.write_thumbnail), _Workers() as workers:
                    retry = workers.start(lambda: app.get_thumbnail(source, 320)).result(WAIT)
                self.assertIsNotNone(retry)
                self.assertEqual(retry.read_bytes(), b"generated thumbnail")
                retry.unlink()
                self.assertEqual(list(self.thumb_root.iterdir()), [])

    def test_failed_generators_clean_temporary_jpg_and_release_permits(self):
        self.check_failed_generation()

    def test_generator_exceptions_clean_temporary_jpg_and_release_permits(self):
        self.check_failed_generation(exception=True)

    def test_publish_exception_cleans_temporary_jpg_and_releases_permits(self):
        source = self.media_file("source.jpg")
        self.patch_app("make_image_thumb", side_effect=self.write_thumbnail)
        failure = OSError("intentional publish failure")
        with mock.patch.object(app.os, "replace", side_effect=failure):
            try:
                result = app.get_thumbnail(source, 320)
            except OSError as exc:
                self.assertIs(exc, failure)
            else:
                self.assertIsNone(result)
        self.assert_permits_restored(app.THUMB_SEMAPHORE, 4)
        self.assertEqual(list(self.thumb_root.iterdir()), [])

    def test_empty_generator_output_is_not_cached(self):
        source = self.media_file("source.jpg")

        def empty(src, dest, width):
            dest.write_bytes(b"")
            return True

        self.patch_app("make_image_thumb", side_effect=empty)
        self.assertIsNone(app.get_thumbnail(source, 320))
        self.assertEqual(list(self.thumb_root.iterdir()), [])
        self.assert_permits_restored(app.THUMB_SEMAPHORE, 4)

    def test_cache_key_changes_with_source_mtime_size_and_requested_width(self):
        source = self.media_file("source.jpg", b"abc")
        original_stat = source.stat()
        original = app.thumb_cache_path(source, 320)
        self.assertEqual(app.thumb_cache_path(source, 320), original)
        self.assertNotEqual(app.thumb_cache_path(source, 640), original)
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 2_000_000_000))
        self.assertNotEqual(app.thumb_cache_path(source, 320), original)
        source.write_bytes(b"different size")
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertNotEqual(app.thumb_cache_path(source, 320), original)
        other = self.media_file("other.jpg", b"abc")
        os.utime(other, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertNotEqual(app.thumb_cache_path(other, 320), original)

    def test_changed_source_and_width_trigger_new_generation(self):
        source = self.media_file("source.jpg", b"abc")
        generator = self.patch_app("make_image_thumb", side_effect=self.write_thumbnail)
        first = app.get_thumbnail(source, 320)
        resized = app.get_thumbnail(source, 640)
        source.write_bytes(b"new source contents")
        updated = app.get_thumbnail(source, 320)
        self.assertEqual(len({first, resized, updated}), 3)
        self.assertNotIn(None, (first, resized, updated))
        self.assertEqual(generator.call_count, 3)


@unittest.skipIf(app.Image is None or app.ImageOps is None, "Pillow is not installed")
class ImageThumbnailTests(_IsolatedCase):
    def test_width_preserves_aspect_ratio_for_landscape_and_portrait(self):
        for size, expected in (((200, 100), (40, 20)), ((100, 300), (40, 120))):
            with self.subTest(size=size):
                source = self.media_root / "source.png"
                dest = self.thumb_root / "result.jpg"
                with app.Image.new("RGB", size, "red") as image:
                    image.save(source)
                self.assertTrue(app.make_image_thumb(source, dest, 40))
                with app.Image.open(dest) as thumbnail:
                    self.assertEqual(thumbnail.size, expected)
                    self.assertEqual(thumbnail.mode, "RGB")
                    self.assertEqual(thumbnail.format, "JPEG")

    def test_small_images_are_not_enlarged(self):
        source = self.media_root / "small.png"
        dest = self.thumb_root / "small.jpg"
        with app.Image.new("RGB", (20, 40), "blue") as image:
            image.save(source)
        self.assertTrue(app.make_image_thumb(source, dest, 200))
        with app.Image.open(dest) as thumbnail:
            self.assertEqual(thumbnail.size, (20, 40))

    def test_transparent_modes_use_the_existing_light_background(self):
        for mode in ("RGBA", "LA", "P"):
            with self.subTest(mode=mode):
                source = self.media_root / f"transparent-{mode}.png"
                dest = self.thumb_root / f"transparent-{mode}.jpg"
                color = {"RGBA": (0, 0, 0, 0), "LA": (0, 0), "P": 0}[mode]
                with app.Image.new(mode, (16, 16), color) as image:
                    if mode == "P":
                        image.putpalette([0, 0, 0] * 256)
                        image.info["transparency"] = 0
                    image.save(source)
                self.assertTrue(app.make_image_thumb(source, dest, 16))
                with app.Image.open(dest) as thumbnail:
                    self.assertEqual(thumbnail.mode, "RGB")
                    pixel = thumbnail.getpixel((8, 8))
                for actual, expected in zip(pixel, (246, 244, 239)):
                    self.assertAlmostEqual(actual, expected, delta=3)

    def test_partial_alpha_is_composited_not_discarded(self):
        source = self.media_root / "alpha.png"
        dest = self.thumb_root / "alpha.jpg"
        with app.Image.new("RGBA", (16, 16), (200, 0, 0, 128)) as image:
            image.save(source)
        self.assertTrue(app.make_image_thumb(source, dest, 16))
        with app.Image.open(dest) as thumbnail:
            pixel = thumbnail.getpixel((8, 8))
        expected = tuple(round(fg * 128 / 255 + bg * 127 / 255) for fg, bg in zip((200, 0, 0), (246, 244, 239)))
        for actual, channel in zip(pixel, expected):
            self.assertAlmostEqual(actual, channel, delta=3)

    def test_exif_orientation_is_applied_before_width_resize(self):
        source = self.media_root / "oriented.jpg"
        dest = self.thumb_root / "oriented-thumb.jpg"
        with app.Image.new("RGB", (80, 40), "red") as image:
            image.paste((0, 0, 255), (40, 0, 80, 40))
            exif = image.getexif()
            exif[274] = 6  # Rotate 90 degrees clockwise.
            image.save(source, exif=exif, quality=100, subsampling=0)
        self.assertTrue(app.make_image_thumb(source, dest, 20))
        with app.Image.open(dest) as thumbnail:
            self.assertEqual(thumbnail.size, (20, 40))
            self.assertIn(thumbnail.getexif().get(274), (None, 1))
            top, bottom = thumbnail.getpixel((10, 5)), thumbnail.getpixel((10, 35))
            self.assertGreater(top[0], top[2] + 100)
            self.assertGreater(bottom[2], bottom[0] + 100)
        with app.Image.open(source) as original:
            self.assertEqual(original.size, (80, 40))
            self.assertEqual(original.getexif().get(274), 6)


class MediaHTTPTests(_IsolatedCase):
    def setUp(self):
        super().setUp()
        self.content = b"0123456789"
        self.source = self.media_file("sample.jpg", self.content)
        self.empty = self.media_file("empty.mp4", b"")
        self.cached_thumb = self.thumb_root / "cached.jpg"
        self.thumb_content = b"\xff\xd8test thumbnail\xff\xd9"
        self.cached_thumb.write_bytes(self.thumb_content)
        self.thumbnail = self.patch_app("get_thumbnail", return_value=self.cached_thumb)
        self.server = app.StudioServer(("127.0.0.1", 0), app.Handler)
        # 请求线程必须在还原 mock 和删除临时目录之前退出。
        self.server.daemon_threads = False
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.server_thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(WAIT)
        self.assertFalse(self.server_thread.is_alive(), "HTTP test server did not stop")

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=WAIT)
        try:
            connection.request("GET", path, headers={"Connection": "close", **(headers or {})})
            response = connection.getresponse()
            try:
                return response.status, dict((key.lower(), value) for key, value in response.getheaders()), response.read()
            finally:
                response.close()
        finally:
            connection.close()

    @staticmethod
    def endpoint(route="media", relative="sample.jpg", **query):
        return f"/api/{route}?" + urlencode({"path": relative, **query})

    def assert_private_validator(self, headers):
        self.assertEqual(headers.get("cache-control"), "private, no-cache")
        tag = headers.get("etag")
        self.assertIsNotNone(tag, "Media responses must include an ETag")
        self.assertRegex(tag, r'^"[^"\r\n]+"$')
        return tag

    def assert_full(self, response, content=None):
        status, headers, body = response
        expected = self.content if content is None else content
        self.assertEqual(status, 200)
        self.assertEqual(body, expected)
        self.assertEqual(headers.get("content-length"), str(len(expected)))
        self.assertNotIn("content-range", headers)

    def assert_partial(self, range_value, start, end, extra_headers=None):
        status, headers, body = self.request(
            self.endpoint(), {"Range": range_value, **(extra_headers or {})}
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, self.content[start:end + 1])
        self.assertEqual(headers.get("content-length"), str(end - start + 1))
        self.assertEqual(headers.get("content-range"), f"bytes {start}-{end}/{len(self.content)}")
        self.assertEqual(headers.get("accept-ranges"), "bytes")
        self.assert_private_validator(headers)

    def test_full_media_has_private_cache_policy_and_stable_etag(self):
        first = self.request(self.endpoint())
        self.assert_full(first)
        self.assertEqual(first[1].get("accept-ranges"), "bytes")
        tag = self.assert_private_validator(first[1])
        self.assertEqual(self.request(self.endpoint())[1].get("etag"), tag)

    def test_closed_ranges_and_clamped_end(self):
        for value, start, end in (("bytes=0-0", 0, 0), ("bytes=2-5", 2, 5), ("bytes=2-999", 2, 9)):
            with self.subTest(range=value):
                self.assert_partial(value, start, end)

    def test_open_range(self):
        self.assert_partial("bytes=4-", 4, 9)

    def test_suffix_ranges(self):
        for value, start in (("bytes=-3", 7), ("bytes=-10", 0), ("bytes=-999", 0)):
            with self.subTest(range=value):
                self.assert_partial(value, start, 9)

    def test_unsatisfiable_ranges_return_416_and_total_size(self):
        for value in ("bytes=10-", "bytes=11-12", "bytes=8-3", "bytes=-0"):
            with self.subTest(range=value):
                status, headers, body = self.request(self.endpoint(), {"Range": value})
                self.assertEqual(status, 416)
                self.assertEqual(headers.get("content-range"), "bytes */10")
                self.assertEqual(headers.get("content-length"), "0")
                self.assertEqual(body, b"")

    def test_empty_file_without_range_returns_200_and_zero_length(self):
        response = self.request(self.endpoint(relative="empty.mp4"))
        self.assert_full(response, b"")
        self.assert_private_validator(response[1])

    def test_valid_ranges_on_empty_file_return_416(self):
        for value in ("bytes=0-0", "bytes=0-", "bytes=-1", "bytes=-0"):
            with self.subTest(range=value):
                status, headers, body = self.request(self.endpoint(relative="empty.mp4"), {"Range": value})
                self.assertEqual(status, 416)
                self.assertEqual(headers.get("content-range"), "bytes */0")
                self.assertEqual(headers.get("content-length"), "0")
                self.assertEqual(body, b"")

    def test_malformed_unknown_and_multiple_ranges_are_ignored(self):
        for value in (
            "bytes=-", "bytes=", "bytes=bad", "bytes=+1-3", "bytes=1.5-3",
            "bytes=1--3", "bytes=1-2junk", "items=1-2", "bytes=1-2,4-5", "bytes=1-2,",
        ):
            with self.subTest(range=value):
                self.assert_full(self.request(self.endpoint(), {"Range": value}))

    def test_malformed_range_on_empty_file_is_ignored(self):
        self.assert_full(self.request(self.endpoint(relative="empty.mp4"), {"Range": "bytes=-"}), b"")

    def test_extremely_large_range_numbers_do_not_raise_500(self):
        # 超过 Python 整数字符串转换上限，但仍在 HTTP 请求头长度限制内。
        huge = "9" * 5000
        for value, allowed in (
            (f"bytes={huge}-", (200, 416)),
            (f"bytes=0-{huge}", (200, 206)),
            (f"bytes=-{huge}", (200, 206)),
        ):
            with self.subTest(form=value[:24]):
                status, headers, body = self.request(self.endpoint(), {"Range": value})
                self.assertIn(status, allowed)
                if status == 416:
                    self.assertEqual(headers.get("content-range"), "bytes */10")
                    self.assertEqual(body, b"")
                else:
                    self.assertEqual(body, self.content)
                    self.assertEqual(headers.get("content-length"), "10")
                    if status == 206:
                        self.assertEqual(headers.get("content-range"), "bytes 0-9/10")

    def check_not_modified(self, route):
        url = self.endpoint(route)
        first = self.request(url)
        self.assertEqual(first[0], 200)
        tag = self.assert_private_validator(first[1])
        for validator in (tag, "W/" + tag, "*", '"not-current", ' + tag, '"not-current", W/' + tag):
            with self.subTest(validator=validator):
                status, headers, body = self.request(url, {"If-None-Match": validator})
                self.assertEqual(status, 304)
                self.assertEqual(body, b"")
                self.assertEqual(self.assert_private_validator(headers), tag)

    def test_media_if_none_match_accepts_strong_weak_wildcard_and_lists(self):
        self.check_not_modified("media")

    def test_thumbnail_if_none_match_accepts_strong_weak_wildcard_and_lists(self):
        self.check_not_modified("thumb")

    def test_nonmatching_if_none_match_returns_complete_representation(self):
        for route, content in (("media", self.content), ("thumb", self.thumb_content)):
            with self.subTest(route=route):
                response = self.request(self.endpoint(route), {"If-None-Match": '"stale", W/"also-stale"'})
                self.assert_full(response, content)
                self.assert_private_validator(response[1])

    def test_thumbnail_200_has_private_policy_etag_and_exact_content(self):
        response = self.request(self.endpoint("thumb", w=320))
        self.assert_full(response, self.thumb_content)
        self.assertEqual(response[1].get("content-type"), "image/jpeg")
        self.assert_private_validator(response[1])
        self.thumbnail.assert_called_once_with(self.source.resolve(), 320)

    def test_thumbnail_304_does_not_read_the_content_file(self):
        url = self.endpoint("thumb")
        tag = self.assert_private_validator(self.request(url)[1])
        original_open, original_io_open = builtins.open, io.open
        attempts = []

        def guard(opener):
            def open_without_thumbnail(file, *args, **kwargs):
                if not isinstance(file, int) and Path(file).resolve() == self.cached_thumb:
                    attempts.append(file)
                    raise AssertionError("304 must not open thumbnail content")
                return opener(file, *args, **kwargs)
            return open_without_thumbnail

        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("304 must not read thumbnail bytes")) as read_bytes, mock.patch(
            "builtins.open", side_effect=guard(original_open)
        ), mock.patch("io.open", side_effect=guard(original_io_open)):
            status, headers, body = self.request(url, {"If-None-Match": tag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(self.assert_private_validator(headers), tag)
        read_bytes.assert_not_called()
        self.assertEqual(attempts, [])

    def test_matching_if_range_etag_allows_partial_content(self):
        tag = self.assert_private_validator(self.request(self.endpoint())[1])
        self.assert_partial("bytes=2-5", 2, 5, {"If-Range": tag})

    def test_nonmatching_if_range_ignores_even_unsatisfiable_range(self):
        tag = self.assert_private_validator(self.request(self.endpoint())[1])
        for validator in ('"stale"', "W/" + tag):
            for value in ("bytes=2-5", "bytes=100-"):
                with self.subTest(validator=validator, range=value):
                    response = self.request(self.endpoint(), {"Range": value, "If-Range": validator})
                    self.assert_full(response)
                    self.assertEqual(self.assert_private_validator(response[1]), tag)

    def test_parent_absolute_and_sibling_prefix_escape_return_404(self):
        outside = self.temp_root / "outside.jpg"
        outside.write_bytes(b"outside temporary file")
        sibling = self.temp_root / "downloads-other"
        sibling.mkdir()
        (sibling / "outside.jpg").write_bytes(b"sibling temporary file")
        for relative in ("../outside.jpg", str(outside), "../downloads-other/outside.jpg"):
            for route in ("media", "thumb"):
                with self.subTest(route=route, path=relative):
                    self.assertEqual(self.request(self.endpoint(route, relative))[0], 404)
        self.thumbnail.assert_not_called()

    def test_symlink_escape_returns_404(self):
        outside = self.temp_root / "outside.jpg"
        outside.write_bytes(b"outside temporary file")
        try:
            (self.media_root / "escape.jpg").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable: {exc}")
        for route in ("media", "thumb"):
            with self.subTest(route=route):
                self.assertEqual(self.request(self.endpoint(route, "escape.jpg"))[0], 404)
        self.thumbnail.assert_not_called()

    def test_encoded_nested_media_path_is_accessible(self):
        relative = "album 空间/a #%.jpg"
        self.media_file(relative, self.content)
        self.assert_full(self.request(self.endpoint(relative=relative)))

    def test_gallery_url_construction_does_not_mutate_cached_item_dicts(self):
        self.media_file("album 空间/a #%.jpg", b"image", BASE_MTIME + 10)
        self.media_file("raw.heic", b"raw", BASE_MTIME + 20)
        self.scan(force=True)
        before = copy.deepcopy(app.MEDIA_CACHE["items"])
        for sort in ("new", "old", "name"):
            with self.subTest(sort=sort):
                status, _, body = self.request("/api/gallery?" + urlencode({"sort": sort, "limit": 100}))
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["total"], len(before))
                for item in payload["items"]:
                    relative = quote(item["rel"])
                    self.assertEqual(item["thumb_url"], "/api/thumb?w=720&path=" + relative)
                    self.assertEqual(item["media_url"], "/api/media?path=" + relative)
                    preview = "/api/media?path=" if item["renderable"] else "/api/thumb?w=1800&path="
                    self.assertEqual(item["preview_url"], preview + relative)
                self.assertEqual(app.MEDIA_CACHE["items"], before)


if __name__ == "__main__":
    unittest.main()
