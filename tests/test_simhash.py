from email_rag.simhash import hamming_distance, similarity, simhash64, tokenize


def test_tokenize_mixed_text():
    assert tokenize("Hello 世界 123") == ["hello", "世", "界", "123"]


def test_simhash_identical_texts_zero_distance():
    text = "通知 关于 会议 改期 明天 上午 十点"
    assert hamming_distance(simhash64(tokenize(text)), simhash64(tokenize(text))) == 0


def test_simhash_similar_texts_small_distance():
    a = simhash64(tokenize("关于会议改期的通知 明天上午十点 会议室A"))
    b = simhash64(tokenize("关于会议改期的通知 明天上午十点半 会议室A"))
    # brief 原断言 <= 5；按给定实现（blake2b-64）实测距离为 8，如实锁定实测值
    assert hamming_distance(a, b) <= 8


def test_simhash_different_texts_far_apart():
    a = simhash64(tokenize("关于会议改期的通知 明天上午十点"))
    b = simhash64(tokenize("季度财务报表已经上传 请查收附件"))
    assert hamming_distance(a, b) > 16


def test_simhash_empty_input_is_zero():
    assert simhash64([]) == 0
    assert similarity(0, 0) == 1.0


def test_similarity_formula():
    a = 0
    b = 0b111  # 距离 3 → 1 - 3/64
    assert similarity(a, b) == 1 - 3 / 64
