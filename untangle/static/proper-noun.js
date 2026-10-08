// 固有名詞と辞書に無い語を伏せる（日本語の辞書つき解析器 kuromoji を使い、ブラウザの中で動く）。
// 「名前らしい形を探して伏せる」のではなく、「普通の言葉だと分かるもの以外は伏せる」向き。
// 英字の語は解析器が全部「辞書に無い語」にしてしまうので、ここでは扱わない（mail-scan-core.js の rareNames に任せる）。
(function (root) {
  const ASCII = /^[A-Za-z0-9'’.,\-/&]+$/;
  const LABEL = { "人名": "名前", "組織": "団体", "地域": "地名" };
  const EMPTY = new Set();
  // 国の名前は個人や団体を特定しないので伏せない（「日本の電話番号が無いお客様」の判断が消えないように）。
  // 都道府県・市町村は団体の所在地が分かるので伏せたまま
  const COUNTRIES = new Set(["日本", "中国", "台湾", "香港", "韓国", "アメリカ", "米国", "イギリス", "英国", "フランス", "ドイツ",
    "イタリア", "スペイン", "オーストラリア", "カナダ", "シンガポール", "タイ", "ベトナム", "インドネシア", "マレーシア", "フィリピン", "インド", "海外"]);

  // 伏せる語か。伏せるなら置き換えの見出しを返す
  function labelOf(t, keep, names = EMPTY) {
    const w = t.surface_form;
    if (!w.trim() || ASCII.test(w) || keep.has(w) || COUNTRIES.has(w)) return null;
    // 宛名に出てきた名前（森・林のように普通の言葉と同じ形の名字も）は、本文で「様」が付いていなくても伏せる
    if (names.has(w) && t.pos === "名詞") return "名前";
    if (t.pos === "記号" || t.pos_detail_1 === "数") return null;
    if (t.pos === "名詞" && t.pos_detail_1 === "固有名詞") return LABEL[t.pos_detail_2] || "固有名詞";
    if (t.word_type === "UNKNOWN" && /[一-龥々ァ-ヶぁ-ん]/.test(w)) return "固有名詞";
    return null;
  }

  // 1つの区切り（［］の外側）を伏せる。組織名の後ろに続く名詞（グループ・食品など）もまとめて伏せる
  function maskSegment(seg, tokenizer, keep, names) {
    const toks = tokenizer.tokenize(seg);
    const out = [];
    for (let i = 0; i < toks.length; i++) {
      const label = labelOf(toks[i], keep, names);
      if (!label) { out.push(toks[i].surface_form); continue; }
      let j = i + 1;
      if (label === "団体") {
        while (j < toks.length && toks[j].pos === "名詞" && ["一般", "固有名詞", "サ変接続"].includes(toks[j].pos_detail_1)
               && !ASCII.test(toks[j].surface_form)) j++;
      }
      while (j < toks.length && labelOf(toks[j], keep, names)) j++;   // 続けて伏せる語は1つにまとめる
      out.push(`［${label}］`);
      i = j - 1;
    }
    return out.join("");
  }

  function maskProper(text, tokenizer, keep = new Set(), names = EMPTY) {
    // すでに伏せた［…］の中は触らない
    return String(text).split(/(［[^］]*］)/).map((seg, i) => (i % 2 ? seg : seg && maskSegment(seg, tokenizer, keep, names))).join("");
  }

  // 宛名の行（「森様」「さくら食品 林 花子様」）から、敬称の直前に並ぶ名詞を名前として集める
  function addresseeNames(lines, tokenizer, keep = new Set()) {
    const names = new Set();
    for (const l of lines) {
      const toks = tokenizer.tokenize(l);
      toks.forEach((t, i) => {
        if (!/^(様|さま|さん|殿|御中)$/.test(t.surface_form)) return;
        for (let j = i - 1, n = 0; j >= 0 && n < 3; j--, n++) {
          const x = toks[j];
          if (x.pos === "記号" && x.pos_detail_1 === "空白") { n--; continue; }
          if (x.pos !== "名詞" || x.pos_detail_1 === "接尾" || x.pos_detail_1 === "数") break;
          // 敬称のすぐ前の語は名前。そこから左は固有名詞だけ（「さくら食品 林 花子様」の「食品」は拾わない）
          if (n > 0 && x.pos_detail_1 !== "固有名詞") break;
          const w = x.surface_form;
          if (!ASCII.test(w) && !keep.has(w) && !COUNTRIES.has(w)) names.add(w);
        }
      });
    }
    return names;
  }

  // お店自身が書いた決まり文句に出てくる固有名詞（店名・地名など）。これは伏せない
  function shopVocabulary(lines, tokenizer) {
    const keep = new Set();
    for (const l of lines) for (const t of tokenizer.tokenize(l)) {
      if (t.pos === "名詞" && t.pos_detail_1 === "固有名詞" && !ASCII.test(t.surface_form)) keep.add(t.surface_form);
    }
    return keep;
  }

  const api = { maskProper, shopVocabulary, labelOf, addresseeNames };
  if (typeof module !== "undefined") module.exports = api; else root.ProperNoun = api;
})(typeof window !== "undefined" ? window : globalThis);
