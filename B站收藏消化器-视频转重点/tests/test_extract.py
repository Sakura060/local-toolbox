"""extract 纯函数测试：分句 / 分词 / 关键词 / 重点句 / 章节 / digest。"""

import unittest

from bilidigest import extract


def S(t0, t1, text):
    return {"t0": t0, "t1": t1, "text": text}


TOPIC_A = [
    "今天我们聊聊本地部署大模型的好处",
    "首先是隐私，你的数据完全不出电脑",
    "其次是成本，一次下载永久免费",
    "本地模型现在的效果已经相当能打",
]

TOPIC_B = [
    "接下来讲讲显存怎么选",
    "八G显存可以跑量化之后的七B模型",
    "显存不够就选更小的量化版本",
    "量化会损失一点精度但是可以接受",
]


class TestSentences(unittest.TestCase):
    def test_split_sentences(self):
        out = extract.split_sentences("第一句。第二句！第三句？\n第四句；最后")
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0], "第一句")
        self.assertEqual(out[-1], "最后")

    def test_split_empty(self):
        self.assertEqual(extract.split_sentences(""), [])
        self.assertEqual(extract.split_sentences("。。。"), [])

    def test_normalize_segments_sorts_and_cleans(self):
        segs = [S(5, 8, "后"), {"t0": 1, "t1": 3, "text": "  "}, S(1, 4, "前")]
        out = extract.normalize_segments(segs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "前")
        self.assertLessEqual(out[0]["t0"], out[1]["t0"])

    def test_merge_gap_breaks_sentence(self):
        # 无终结标点但间隔 ≥1.5s → 也算新句
        segs = [S(0, 2, "没有标点的第一句"), S(4, 6, "停顿后的第二句")]
        out = extract.merge_to_sentences(segs)
        self.assertEqual(len(out), 2)

    def test_merge_to_sentences(self):
        segs = [S(0, 2, "我们今天聊聊"), S(2, 4, "本地部署。"), S(4, 6, "第二句开始")]
        out = extract.merge_to_sentences(segs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "我们今天聊聊本地部署。")
        self.assertEqual(out[1]["t0"], 4)


class TestKeywords(unittest.TestCase):
    def test_keywords_count_and_stopwords(self):
        sents = [{"text": t} for t in (TOPIC_A + TOPIC_B)]
        kw = extract.keywords(sents, top=8)
        words = [k["word"] for k in kw]
        self.assertIn("显存", words)
        self.assertNotIn("我们", words)      # 停用词
        self.assertNotIn("就是", words)
        self.assertTrue(all(k["count"] >= 1 for k in kw))

    def test_keywords_empty(self):
        self.assertEqual(extract.keywords([]), [])


class TestKeySentences(unittest.TestCase):
    def test_rank_returns_original_text(self):
        sents = [{"t0": i * 3, "t1": i * 3 + 2, "text": t}
                 for i, t in enumerate(TOPIC_A + TOPIC_B)]
        out = extract.key_sentences(sents, top=3)
        self.assertEqual(len(out), 3)
        texts = {k["text"] for k in out}
        all_texts = {s["text"] for s in sents}
        self.assertTrue(texts.issubset(all_texts))  # 不生成新句子
        # 按时间顺序输出
        self.assertEqual(out, sorted(out, key=lambda k: k["t0"]))

    def test_few_sentences(self):
        sents = [S(0, 1, "只有一句")]
        out = extract.key_sentences(sents, top=5)
        self.assertEqual(len(out), 1)


class TestChapters(unittest.TestCase):
    def test_split_on_gap(self):
        sents = [S(i * 3, i * 3 + 2, t) for i, t in enumerate(TOPIC_A)]
        sents += [S(20 + i * 3, 22 + i * 3, t) for i, t in enumerate(TOPIC_B)]
        chs = extract.chapters(sents, gap=3.5, min_sents=4)
        self.assertEqual(len(chs), 2)
        self.assertLess(chs[0]["t1"], chs[1]["t0"])
        self.assertIn("显存", chs[1]["title"])  # 章节标题含该章关键词

    def test_no_split_without_gap(self):
        # 真正无缝（t1 == 下一句 t0）→ 无停顿边界，话题突变也要求 gap≥1s → 不切
        sents = [S(i * 3, i * 3 + 3, t) for i, t in enumerate(TOPIC_A + TOPIC_B)]
        chs = extract.chapters(sents, gap=3.5, min_sents=4)
        self.assertEqual(len(chs), 1)

    def test_empty(self):
        self.assertEqual(extract.chapters([]), [])


class TestDigest(unittest.TestCase):
    def test_digest_shape(self):
        segs = [S(i * 4, i * 4 + 2.5, t) for i, t in enumerate(TOPIC_A)]
        segs += [S(20 + i * 4, 22 + i * 4, t) for i, t in enumerate(TOPIC_B)]
        dg = extract.digest(segs)
        self.assertIn("keywords", dg)
        self.assertIn("key_sentences", dg)
        self.assertIn("chapters", dg)
        self.assertEqual(dg["sentence_count"], 8)
        self.assertGreater(dg["duration"], 0)

    def test_transcript_text_stamp(self):
        sents = [S(61, 63, "一分钟后的句子")]
        out = extract.transcript_text(sents)
        self.assertIn("[01:01]", out)
        self.assertIn("一分钟后的句子", out)


EN_TOPIC_1 = [
    "A good soup needs patience.",
    "Simmer the broth slowly for two hours.",
    "Fresh herbs make a big difference.",
]
EN_TOPIC_2 = [
    "The striker scored two goals in the second half.",
    "The coach changed the formation at half time.",
    "The final score was three to one.",
]


class TestMergeEnglish(unittest.TestCase):
    def test_english_period_ends_sentence(self):
        # 真实数据回归：whisper 英文段以 "." 结尾，曾因缺 "." 被黏连成一整句
        segs = [S(0, 2, "A good soup needs patience."),
                S(2, 4, "Simmer the broth slowly."),
                S(4, 6, "Fresh herbs matter.")]
        out = extract.merge_to_sentences(segs)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1]["text"], "Simmer the broth slowly.")

    def test_english_merge_inserts_space(self):
        segs = [S(0, 2, "Never rush a good"), S(2, 4, "stew when cooking")]
        out = extract.merge_to_sentences(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "Never rush a good stew when cooking")

    def test_chinese_merge_no_space(self):
        segs = [S(0, 2, "我们今天聊聊"), S(2, 4, "本地部署的坑")]
        out = extract.merge_to_sentences(segs)
        self.assertEqual(out[0]["text"], "我们今天聊聊本地部署的坑")


class TestChaptersShift(unittest.TestCase):
    def test_shift_boundary_without_long_gap(self):
        # 话题突变（词集几乎零重叠）+ 1.2s 小停顿 → 切章；停顿远小于 gap=3.5
        sents = [S(i * 3, i * 3 + 2, t) for i, t in enumerate(EN_TOPIC_1)]
        sents += [S(11.2 + i * 3, 13.2 + i * 3, t) for i, t in enumerate(EN_TOPIC_2)]
        chs = extract.chapters(sents, gap=3.5, min_sents=3)
        self.assertEqual(len(chs), 2)
        # 两章时间不重叠
        self.assertLess(chs[0]["t1"], chs[1]["t0"])
        # 标题词来自各章正文（词频并列时按字母序取，不断言具体词）
        words1 = set(" ".join(EN_TOPIC_1).lower().split())
        words2 = set(" ".join(EN_TOPIC_2).lower().split())
        t1_words = set(chs[0]["title"].lower().replace("第1段", "").split("·"))
        t2_words = set(chs[1]["title"].lower().replace("第2段", "").split("·"))
        self.assertTrue(t1_words & words1, t1_words)
        self.assertTrue(t2_words & words2, t2_words)


if __name__ == "__main__":
    unittest.main()
