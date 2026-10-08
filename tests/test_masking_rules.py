from untangle.masking import Masker


def test_name_before_honorific_is_masked_by_rule():
    out = Masker([]).mask("池田様、お支払いはクレジットで。林さんと添乗員さんとお客様").text
    assert "池田" not in out and "林" not in out
    assert "添乗員さん" in out and "お客様" in out and "クレジット" in out


def test_detected_school_keeps_its_category():
    def det(text):
        i = text.find("あおば幼稚園")
        return [(i, i + 6, "学校名")] if i >= 0 else []
    out = Masker([], detector=det).mask("あおば幼稚園から20名").text
    assert out == "［幼稚園］から20名"
