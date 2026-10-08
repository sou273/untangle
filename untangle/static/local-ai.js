// 端末の中で動く部分（Chrome内蔵の Gemini Nano と、決まったプログラム）。
// 生の文はこのファイルの中だけで扱い、サーバーには送らない。
// サーバーに送るのは、①選択肢から選ばれた値（自動の道） ②決裁者が確かめた伏せた後の文（人が確かめる道）だけ。
window.LocalAI = (() => {
  const LM = window.LanguageModel || (window.ai && window.ai.languageModel);
  const OPTS = { expectedInputs: [{ type: "text", languages: ["ja"] }], expectedOutputs: [{ type: "text", languages: ["ja"] }] };
  const UNKNOWN = "不明";
  const UNDECIDED = "決まっていない";

  async function availability() {
    if (!LM) return "unavailable";
    try { return await (LM.availability ? LM.availability(OPTS) : LM.capabilities().then((c) => c.available)); }
    catch (_) { return "unavailable"; }
  }

  async function prompt(system, text, schema) {
    const s = await LM.create({ ...OPTS, initialPrompts: [{ role: "system", content: system }] });
    try { return JSON.parse(await s.prompt(text, { responseConstraint: schema })); }
    finally { s.destroy && s.destroy(); }
  }

  // ---- 自動の道: 項目の値と判断を選択肢から選ぶ ----
  async function extract(text, schema) {
    const qTitles = schema.questions.map((q) => q.title);
    const answers = [...new Set(schema.questions.flatMap((q) => q.answers)), UNDECIDED];
    const attrProps = Object.fromEntries(Object.entries(schema.attributes).map(([k, v]) => [k, { type: "string", enum: [...v, UNKNOWN] }]));
    const json = {
      type: "object",
      properties: { scenes: { type: "array", maxItems: 10, items: {
        type: "object",
        properties: { "問い": { type: "string", enum: qTitles }, ...attrProps,
          "決めた人": { type: "string", enum: ["決裁者", "スタッフ", "まだ決まっていない"] },
          "判断": { type: "string", enum: answers } },
        required: ["問い", ...Object.keys(attrProps), "決めた人", "判断"] } } },
      required: ["scenes"],
    };
    const system = `あなたは店のやり取りを読み、決まった判断を1件ずつ取り出す係です。テーマ: ${schema.name}
問い: ${schema.questions.map((q) => `「${q.title}」（答え: ${q.answers.join("・")}）`).join("、")}
項目: ${Object.entries(schema.attributes).map(([k, v]) => `${k}（${v.join("・")}）`).join("、")}
- 1つのやり取りに対象が複数あれば（例: 先生は数えない・生徒は子供）、対象ごとに分ける
- 書かれていない項目は「不明」。推測しない
- 決めた人: 決裁者（店主・社長など）の返事やメモで決まったなら「決裁者」。スタッフが自分で決めて報告しているなら「スタッフ」。
  スタッフが質問しているだけで決裁者がまだ返事をしていなければ「まだ決まっていない」
- 判断: 決めた人が「まだ決まっていない」なら必ず「${UNDECIDED}」。何も決まっていないやり取りなら scenes は空にする`;
    const out = await prompt(system, text, json);
    return (out.scenes || []).map((sc) => check(sc, schema));
  }

  // 決まったプログラムの歯止め。引っかかったものは自動では送らない
  function check(sc, schema) {
    const q = schema.questions.find((x) => x.title === sc["問い"]);
    const attrs = {};
    for (const k of Object.keys(schema.attributes)) if (sc[k] && sc[k] !== UNKNOWN) attrs[k] = sc[k];
    const problems = [];
    if (!q) problems.push("問いが分からない");
    if (sc["決めた人"] === "まだ決まっていない" || sc["判断"] === UNDECIDED) problems.push("まだ決まっていない");
    else if (q && !q.answers.includes(sc["判断"])) problems.push("その問いの答えに無い判断");
    if (!Object.keys(attrs).length) problems.push("項目がひとつも読めない");
    // 選択肢に無い値は（Nano の制約が外れても）ここで落とす
    for (const [k, v] of Object.entries(attrs)) if (!(schema.attributes[k] || []).includes(v)) problems.push(`項目「${k}」が選択肢に無い`);
    return {
      ok: problems.length === 0, problems,
      send: q ? { question_id: q.id, attributes: attrs, decision: sc["判断"], decider: sc["決めた人"] === "決裁者" ? "owner" : "staff" } : null,
      view: { question: sc["問い"], attributes: attrs, decision: sc["判断"], decider: sc["決めた人"] },
    };
  }

  // ---- 人が確かめる道: 端末の中で伏せる（辞書 → 決まった規則 → Gemini Nano で名前を探す） ----
  const SECRET = [/(パスワード|password|passwd|暗証番号|PIN)\s*[:：は]/i, /\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{8,}/, /\bxox[abpr]-[A-Za-z0-9-]{10,}/, /-----BEGIN [A-Z ]*PRIVATE KEY-----/];
  const NUMBERS = [
    ["メール", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g],
    ["URL", /https?:\/\/[^\s　）)」]+/g],
    ["電話番号", /(?<!\d)0\d{1,4}[-‐－ー(（]?\d{1,4}[-‐－ー)）]?\d{3,4}(?!\d)/g],
    ["郵便番号", /〒?\s?\d{3}[-‐－]\d{4}/g],
    ["番号", /(?<!\d)\d{9,}(?!\d)/g],
  ];
  const NOT_NAMES = new Set(["客", "お客", "皆", "皆々", "奥", "各", "名", "子", "お子", "団体", "先方", "先生", "生徒", "添乗員", "保育士", "担当", "担任", "店長", "部長", "社長", "店主", "運転手", "ガイド", "スタッフ", "オーナー", "一家", "夫妻", "家族", "子供", "親御", "担当者", "参加者", "保護者", "見学者", "代表者", "利用者", "宛名", "団体", "年長", "年中", "年少", "皆さ", "みなさ", "お客さ"]);
  const WEEK = "日月火水木金土";
  // 会社・団体名の語尾（2文字以上だけ。「反省」の「省」のような1文字は普通の語を巻き込むので入れない）
  const ORG = "株式会社|有限会社|合同会社|一般社団法人|社団法人|財団法人|法人|高等学校|中学校|小学校|大学校|大学|幼稚園|保育園|学園|学校|支店|支社|本社|支部|協会|組合|振興会|連合会|キャンパス|"
    + "食品|商事|商店|商会|工業|産業|製作所|製薬|化学|電機|電気|電工|建設|工務店|不動産|銀行|信金|証券|保険|生命|損保|旅行社|旅行会社|ツーリスト|トラベル|ツアーズ|"
    + "ホテル|旅館|新聞社|放送|出版|印刷|病院|医院|クリニック|事務所|研究所|役所|役場|市役所|区役所|県庁|都庁|グループ|ホールディングス|コーポレーション|カンパニー|ジャパン";

  // 宛名・団体名（決まった規則）。名前の候補を広めに伏せる——伏せすぎは決裁者が直せるが、漏れは戻せない
  function maskAddressees(text) {
    return text
      // 領収書の宛名の行は、値をまるごと伏せる
      .replace(/((?:領収書名|領収書の?お?宛名|お宛名|宛名)\s*[:：]\s*)[^（(\n]+/g, "$1［宛名］")
      // 英語の宛名（Mr./Ms. Taro EXAMPLE / Dear Ms Jane）と予約番号のような英数字
      .replace(/\b(Mr|Ms|Mrs|Miss|Mx)\.?(\/(Mr|Ms|Mrs|Miss|Mx)\.?)?(\s+(Mr|Ms|Mrs|Miss|Mx)\.?)?\s+[A-Z][A-Za-z'-]*(\s+[A-Z][A-Za-z'-]*){0,3}/g, (m, t) => m.slice(0, m.search(/\s/)) + " ［名前］")
      .replace(/\b[A-Z]{2,5}\d{5,}\b/g, "［番号］")
      // （株）○○ ／ ○○株式会社・○○中学校・○○支店 など
      .replace(/[（(]株[）)][^\s　様、。（(]{1,20}/g, "［団体］")
      .replace(new RegExp(`(?:[一-龥々ァ-ヶーA-Za-z0-9０-９・]|(?![のをはがにでとへもやかよ])[ぁ-ん]){1,24}(?:${ORG})(?:(?!様|殿)[一-龥々ァ-ヶー]){0,10}`, "g"), "［団体］")
      // かなを含む会の名前（○○の会様）
      .replace(/[ぁ-んァ-ヶー一-龥々]{2,15}会(?=[ 　]?(様|さま|御中))/g, "［団体］")
      // 名字と名前のあいだに空白がある宛名（陳 小明 様）
      .replace(/(?<![一-龥々ァ-ヶー])[一-龥々]{1,4}[ 　][一-龥々]{1,4}[ 　]?(?=様|さま|さん|氏)/g, "［名前］")
      .replace(/［団体］[ 　]+([一-龥々]{1,4})(?=様|さま|さん)/g, "［団体］［名前］");
  }

  async function mask(text, dictionary, useNano) {
    if (SECRET.some((p) => p.test(text))) return { masked: null, reason: "パスワードなどを含むため、取り込みません" };
    let out = text;
    const entries = [...dictionary].filter((d) => d.surface).sort((a, b) => b.surface.length - a.surface.length);
    for (const d of entries) out = out.split(d.surface).join(`［${d.replacement}］`);
    for (const [kind, re] of NUMBERS) out = out.replace(re, `［${kind}］`);
    out = out.replace(/(?:(\d{4})[/年-])?(\d{1,2})[/月](\d{1,2})日?/g, (m, y, mo, d) => {
      const dt = new Date(Number(y) || new Date().getFullYear(), Number(mo) - 1, Number(d));
      if (isNaN(dt)) return m;
      const w = WEEK[dt.getDay()];
      const season = { 12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春", 6: "夏", 7: "夏", 8: "夏" }[Number(mo)] || "秋";
      return `［${"土日".includes(w) ? "土日" : "平日"}・${season}］`;
    });
    out = maskAddressees(out);
    // 数字のすぐ後（2名様）は名前ではない
    out = out.replace(/(?<![一-龥々ァ-ヶー\d０-９])([一-龥々]{1,4}|[ァ-ヶー]{2,8})(?=さん|様|さま|くん|ちゃん|氏)/g, (m, w) => (NOT_NAMES.has(w) || /^(数|何|若干)?名$/.test(w) ? w : "［名前］"));
    let usedNano = false;
    if (useNano) {
      try {
        const r = await prompt(
          "文の中の固有名詞（人の名字・名前、会社名、団体名、学校名、店名、地名）を、文に書かれたとおり抜き出してください。［］で囲まれた部分、役割（店主・スタッフなど）、人数・日時は抜き出しません。無ければ空。",
          out, { type: "object", properties: { names: { type: "array", items: { type: "string" } } }, required: ["names"] });
        for (const n of (r.names || []).sort((a, b) => b.length - a.length)) {
          if (!n || n.length < 2 || /\d/.test(n) || NOT_NAMES.has(n) || out.indexOf(n) < 0) continue;
          out = out.split(n).join("［名前］");
        }
        usedNano = true;
      } catch (_) { /* Nano が使えなくても、辞書と決まった規則の結果を見せて決裁者が確かめる */ }
    }
    return { masked: out, usedNano };
  }

  return { availability, extract, mask, maskAddressees };
})();
