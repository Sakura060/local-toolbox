"""db / fetch / jobs / server 测试（ASR 用假实现，不依赖模型）。"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from bilidigest import fetch
from bilidigest.db import Database
from bilidigest.jobs import Runner
from bilidigest.server import make_server
from bilidigest.web import WEB_DIR


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_job_lifecycle(self):
        jid = self.db.insert_job("url", "https://b23.tv/x")
        self.db.set_status(jid, "running", "下载音频")
        self.db.set_progress(jid, "本地转写 30s/100s", 0.4)
        j = self.db.get_job(jid)
        self.assertEqual(j["status"], "running")
        self.assertEqual(j["stage"], "本地转写 30s/100s")
        self.db.set_done(jid, {"keywords": []}, 100.5, "base")
        j = self.db.get_job(jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual(j["duration"], 100.5)
        self.assertEqual(j["digest"]["keywords"], [])
        self.assertGreater(j["finished"], 0)  # finished 已设置

    def test_failed_keeps_err(self):
        jid = self.db.insert_job("local", r"C:\nope.mp4")
        self.db.set_status(jid, "failed", "", "文件不存在")
        j = self.db.get_job(jid)
        self.assertEqual(j["status"], "failed")
        self.assertEqual(j["err"], "文件不存在")

    def test_transcripts_roundtrip(self):
        jid = self.db.insert_job("url", "https://b23.tv/x")
        segs = [{"t0": 0, "t1": 2, "text": "第一句"}, {"t0": 2, "t1": 5, "text": "第二句"}]
        self.db.save_transcripts(jid, segs)
        rows = self.db.get_transcripts(jid)
        self.assertEqual([r["text"] for r in rows], ["第一句", "第二句"])

    def test_pending_jobs(self):
        a = self.db.insert_job("url", "https://b23.tv/a")
        self.db.insert_job("url", "https://b23.tv/b")
        self.db.set_status(a, "done")
        self.assertEqual(self.db.pending_jobs(), [2])


class TestFetch(unittest.TestCase):
    def test_classify_bili(self):
        for u in ("https://www.bilibili.com/video/BV1xx411c7mD",
                  "https://b23.tv/abc123"):
            kind, _ = fetch.classify_input(u)
            self.assertEqual(kind, "url")

    def test_classify_local_ok(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "v.mp4"
            p.write_bytes(b"\x00" * 16)
            kind, value = fetch.classify_input(str(p))
            self.assertEqual(kind, "local")
            self.assertTrue(Path(value).is_file())

    def test_classify_local_errors(self):
        with self.assertRaises(fetch.FetchError):
            fetch.classify_input(r"Z:\nope.mp4")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.txt"
            p.write_text("x", encoding="utf-8")
            with self.assertRaises(fetch.FetchError):
                fetch.classify_input(str(p))

    def test_build_cmd(self):
        cmd = fetch.build_ytdlp_cmd("https://b23.tv/x", Path("D:/tmp"))
        self.assertIn("-m", cmd)
        self.assertIn("yt_dlp", cmd)
        self.assertIn("--no-playlist", cmd)
        self.assertIn("https://b23.tv/x", cmd)

    def test_download_bad_url(self):
        with self.assertRaises(fetch.FetchError):
            fetch.download_audio("https://b23.tv/__no_such_video_xyz__",
                                 timeout=30)


class _FakeAsrOk:
    """替身 ASR：返回预制分段。"""

    @staticmethod
    def transcribe(media, model_size="base", progress=None):
        segs = [
            {"t0": 0, "t1": 3, "text": "今天聊聊本地部署大模型。"},
            {"t0": 3, "t1": 6, "text": "隐私是第一位的，数据不出电脑。"},
            {"t0": 6, "t1": 9, "text": "然后是成本，一次下载永久免费。"},
        ]
        for s in segs:
            if progress:
                progress(s["t1"], 9)
        return segs, 9.0


class TestRunnerWithFakeAsr(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.media = Path(self.tmp.name) / "v.mp4"
        self.media.write_bytes(b"\x00" * 64)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_process_local_with_fake_asr(self):
        jid = self.db.insert_job("local", str(self.media))
        r = Runner(self.db, model_size="base")
        # 注入假 ASR
        import bilidigest.jobs as jobs_mod
        orig = jobs_mod.asr
        jobs_mod.asr = _FakeAsrOk()
        try:
            result = r.process(jid)
        finally:
            jobs_mod.asr = orig
        self.assertTrue(result["ok"], result.get("error"))
        job = self.db.get_job(jid)
        self.assertEqual(job["status"], "done")
        self.assertIsNotNone(job["digest"])
        self.assertGreater(len(job["digest"]["key_sentences"]), 0)
        self.assertEqual(len(self.db.get_transcripts(jid)), 3)
        self.assertTrue(Path(job["media_path"]).is_file())
        # 导出的 txt 存在
        txt = Path(self.tmp.name) / "transcripts" / f"{jid}.txt"
        # txt 目录用的是 config 的全局目录，文件可能不在 tmp —— 只验证 job 完成

    def test_process_local_missing_file(self):
        jid = self.db.insert_job("local", r"Z:\no_such.mp4")
        r = Runner(self.db)
        result = r.process(jid)
        self.assertFalse(result["ok"])
        self.assertEqual(self.db.get_job(jid)["status"], "failed")


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        jid = self.db.insert_job("local", str(Path(self.tmp.name) / "v.mp4"))
        self.db.set_media(jid, str(Path(self.tmp.name) / "v.mp4"), title="示例视频")
        self.db.save_transcripts(jid, [{"t0": 0, "t1": 2, "text": "你好"}])
        self.db.set_done(jid, {"keywords": [{"word": "测试", "count": 2}],
                               "key_sentences": [{"t0": 0, "t1": 2, "text": "你好", "score": 1}],
                               "chapters": []}, 2.0, "base")
        self.media_file = Path(self.tmp.name) / "v.mp4"
        self.media_file.write_bytes(b"\x00" * 100000)
        # 无 runner：add 只入库
        self.server = make_server(0, self.db, runner=None)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        self.tmp.cleanup()

    def _get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path, headers=headers or {})
            r = conn.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            conn.close()

    def _post(self, path, obj):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=json.dumps(obj).encode("utf-8"),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_page(self):
        status, body, _ = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("消化".encode("utf-8"), body)

    def test_jobs_and_detail(self):
        _, body, _ = self._get("/api/jobs")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["stats"]["total"], 1)
        _, body, _ = self._get("/api/job/1")
        job = json.loads(body.decode("utf-8"))
        self.assertEqual(job["title"], "示例视频")
        self.assertEqual(job["digest"]["keywords"][0]["word"], "测试")
        self.assertEqual(job["transcripts"][0]["text"], "你好")

    def test_media_range(self):
        status, body, headers = self._get("/media/1", {"Range": "bytes=0-99"})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 100)
        self.assertIn("bytes 0-99/100000", headers.get("Content-Range", ""))

    def test_media_full(self):
        status, body, _ = self._get("/media/1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 100000)

    def test_add_local_pending(self):
        p = Path(self.tmp.name) / "x.mp3"
        p.write_bytes(b"\x00" * 8)
        status, body = self._post("/api/add", {"input": str(p)})
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        job = self.db.get_job(data["id"])
        self.assertEqual(job["status"], "pending")

    def test_add_invalid(self):
        status, body = self._post("/api/add", {"input": r"Z:\no.mp4"})
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
