from datetime import date

from untangle.masking import DictEntry, Masker

ENTRIES = [
    DictEntry("佐藤", "ショップ担当A", "スタッフ"),
    DictEntry("店主", "決裁者", "スタッフ"),
    DictEntry("青葉台小学校", "学校（小）", "学校"),
    DictEntry("山本先生", "教員", "お客様"),
]


def masker(detector=None):
    return Masker(ENTRIES, detector=detector, today=lambda: date(2026, 10, 1))


def test_dictionary_numbers_and_dates_are_replaced():
    r = masker().mask("佐藤：10/21の青葉台小学校さん、山本先生の携帯は090-1234-5678、mail は a.b@example.jp です")
    assert r.text is not None
    for raw in ("佐藤", "青葉台", "山本", "090", "example.jp", "10/21"):
        assert raw not in r.text
    assert "［ショップ担当A］" in r.text and "［平日・秋］" in r.text
    assert not r.held


def test_counts_needed_for_judgment_survive():
    r = masker().mask("生徒38名・先生3名・添乗員1名")
    assert r.text == "生徒38名・先生3名・添乗員1名"


def test_message_with_credentials_is_not_ingested():
    r = masker().mask("管理画面のパスワード: hunter2 です")
    assert r.text is None and r.rejected_reason


def test_detector_catches_unknown_names_and_residual_holds():
    def detector(text):
        i = text.find("鈴木")
        return [(i, i + 2, "人名")] if i >= 0 else []
    r = masker(detector).mask("鈴木さんから電話")
    assert "鈴木" not in r.text and not r.held

    # 検出器が拾わない場合でも、辞書にある名前が残れば保留になる
    m = masker()
    assert m.residual_check("佐藤さん") == ["スタッフ"]
