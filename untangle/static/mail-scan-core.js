// 送ったメールが「型（ひな形）」からどう外れているかを見つける（決まったプログラムだけ。AIは使わない）。
// ブラウザでもNodeでも動く。メールの中身はこの関数の中だけで扱い、どこにも送らない。
(function (root) {
  // 店ごとに違う値（署名の店名の行・自動送信の署名・社内のドメイン）は、analyze の opts で受け取るか、メールから見つける

  // 1行を「骨組み」にする: 宛名・日時・金額・数・URLなど毎回変わる部分を記号に置き換える
  const MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec";
  const DAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Tues|Wed|Thu|Thur|Fri|Sat|Sun";
  function skeleton(line) {
    return line
      .replace(/https?:\/\/\S+/g, "<URL>")
      // 英語の日付・曜日・時刻（October 11, 2026 / Saturday / 3:00 PM / 11th）
      .replace(new RegExp(`\\b(${DAYS})\\b,?`, "g"), "<DAY>")
      .replace(new RegExp(`\\b(${MONTHS})\\.?\\s+\\d{1,2}(st|nd|rd|th)?(,?\\s*\\d{4})?`, "g"), "<DATE>")
      .replace(new RegExp(`\\b\\d{1,2}(st|nd|rd|th)?\\s+(of\\s+)?(${MONTHS})\\b(,?\\s*\\d{4})?`, "g"), "<DATE>")
      .replace(/\b\d{1,2}(:\d{2})?\s*[AaPp]\.?[Mm](\.(?!\s|$))?/g, "<TIME>")
      // 中国語の日付（10月11日（六）など）は下の和文と同じ形で置き換わる。曜日の漢字だけ寄せる
      .replace(/[（(](星期)?[一二三四五六日天月火水木金土][)）]/g, "(<DAY>)")
      .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "<MAIL>")
      .replace(/\d{4}[/年-]\d{1,2}[/月-]\d{1,2}日?(\s*[(（][^)）]*[)）])?/g, "<DATE>")
      .replace(/\d{1,2}[/月]\d{1,2}日?/g, "<DATE>")
      .replace(/\d{1,2}[:：]\d{2}/g, "<TIME>")
      .replace(/[¥￥]\s?[\d,]+/g, "<YEN>")
      .replace(/\d{1,2}時(\d{1,2}分|半)?/g, "<TIME>")
      .replace(/\d[\d,，.]*/g, "<N>")
      .replace(/^.{1,40}(様|御中|さま)$/, "<宛名>$1")
      // 文の途中の宛名（○○様・○○さん・Mr./Ms. ○○）
      .replace(/(?<![\d>])([一-龥々ぁ-んァ-ヶーA-Za-z]{1,12})\s?(様|さま|さん|御中)/g,
        (m, w, h) => (/^(お客|客|皆|各|名|皆々|みな|みなさ|お子)$/.test(w) || /名$/.test(w) ? m : `<宛名>${h}`))
      .replace(/\b(Mr|Ms|Mrs|Miss|Mx)\.?\s+[A-Z][\w'-]*(\s+[A-Z][\w'-]*)?/g, "$1 <宛名>")
      .replace(/^(Dear|Hi|Hello)\s+(?!<|(Mr|Ms|Mrs|Miss|Mx)\b)[A-Z][\w'-]*(\s+[A-Z][\w'-]*)?/, "$1 <宛名>")
      .replace(/\s+/g, " ")
      .trim();
  }

  // 本文から引用（> の行・「〜のメール:」以降）を外し、署名を取り出す
  const escapeRe = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s*");
  function split(body, shopLine = "") {
    const lines = String(body || "").replace(/\r/g, "").split("\n");
    const own = [];
    let quoteAt = lines.length;
    for (const [i, l] of lines.entries()) {
      // 引用の見出し（「2026年9月1日(火) 12:00 名前 <mail>:」「… wrote:」）と > の行から先はお客様側の文
      if (/^\s*>/.test(l) || (/(のメール|wrote|より)[:：]\s*$/.test(l) && /\d/.test(l)) || /^-{3,}\s*(Original|引用元|元のメッセージ)/.test(l)
          || (/@[A-Za-z0-9.-]+\.[A-Za-z]{2,}>?\s*[:：]\s*$/.test(l) && /\d/.test(l))) { quoteAt = i; break; }
      own.push(l);
    }
    // お客様の文: 引用の中身（> を外し、さらに古い引用の手前まで）
    const quoted = [];
    for (const l of lines.slice(quoteAt + 1)) {
      const t = l.replace(/^(\s*>)+\s?/, "").trim();
      if (/(のメール|wrote)[:：]\s*$/.test(t) || /@[A-Za-z0-9.-]+\.[A-Za-z]{2,}>?\s*[:：]\s*$/.test(t)) break;
      if (t) quoted.push(t);
      if (quoted.length >= 15) break;
    }
    // 署名: 店名の行の次の行（担当者名か、自動送信の署名）
    let signer = "";
    const shopRe = shopLine ? new RegExp(`${escapeRe(shopLine.trim())}\\s*$`) : null;
    for (let i = own.length - 1; shopRe && i >= 0; i--) {
      if (shopRe.test(own[i].trim()) && own[i + 1] && own[i + 1].trim()) { signer = own[i + 1].trim(); break; }
    }
    return { lines: own.map((l) => l.trim()).filter(Boolean), signer, quoted };
  }

  // 件名をそろえる: 頭の [External] や Re:/Fwd: を外し、数を記号にする
  function familyOf(subject) {
    let s = String(subject || "");
    for (let i = 0; i < 4; i++) s = s.replace(/^\s*(\[(external|EXTERNAL|External)\]|［[^］]*］|(re|fw|fwd)\s*[:：])\s*/i, "");
    return s.replace(/[\d#]+/g, "<N>").replace(/\s+/g, " ").trim();
  }
  const isForward = (subject) => /(^|\s)(fw|fwd)\s*[:：]/i.test(String(subject || ""));

  /**
   * mails: [{id, subject, date, body}]
   * 返り値: 件名ごとの家族（型）と、家族の中で外れている行・別の型を選んだメール
   */
  // 署名の店名の行を見つける: 多くのメールの末尾近くに繰り返し出る、短い・文でない行
  function guessShopLine(mails, autoSigners = []) {
    const count = new Map();
    for (const m of mails) {
      const own = split(m.body).lines;
      for (const l of new Set(own.slice(-8))) {
        if (l.length < 2 || l.length > 30 || /[。．！？!?、,@:：]|https?|\d{4,}/.test(l)) continue;
        count.set(l, (count.get(l) || 0) + 1);
      }
    }
    const need = Math.max(3, Math.floor(mails.length * 0.2));
    // 店名の行の次には署名（担当者名）が来るので、次の行が何通りもあるものを選ぶ
    let best = "", bestN = 0;
    for (const [l, n] of count) {
      if (n < need) continue;
      const nexts = new Set(mails.map((m) => { const o = split(m.body).lines; const i = o.lastIndexOf(l); return i >= 0 ? o[i + 1] : undefined; }).filter(Boolean));
      if ((nexts.size >= 2 || [...nexts].some((x) => autoSigners.includes(x))) && n > bestN) { best = l; bestN = n; }
    }
    return best;
  }
  // 社内のドメイン: 送ったメールの差出人のドメイン（いちばん多いもの）
  function guessInternal(mails) {
    const count = new Map();
    for (const m of mails) {
      const d = (/@([A-Za-z0-9.-]+)/.exec(m.from || "") || [])[1];
      if (d) count.set(d.toLowerCase(), (count.get(d.toLowerCase()) || 0) + 1);
    }
    const top = [...count].sort((a, b) => b[1] - a[1])[0];
    return top ? ["@" + top[0]] : [];
  }

  function analyze(mails, opts = {}) {
    const autoSigners = (opts.autoSigners || []).filter(Boolean);
    const shopLine = opts.shopLine || guessShopLine(mails, autoSigners);
    opts = { ...opts, internal: [...new Set([...(opts.internal || []), ...guessInternal(mails)])] };
    const minShared = opts.minShared ?? 3;      // この通数以上に同じ形で出る行は「型」
    const parsed = mails.map((m) => {
      const { lines, signer, quoted } = split(m.body, shopLine);
      return { ...m, lines, signer, quoted, auto: autoSigners.includes(signer), skels: [...new Set(lines.map(skeleton))], family: familyOf(m.subject) };
    });
    const families = {};
    for (const p of parsed) (families[p.family] ||= []).push(p);
    // 型の判定は件名をまたいで行う（スタッフは別の件名のメールにひな形を区切り線の下へ貼ることがある）
    const globalFreq = new Map();
    for (const p of parsed) for (const s of p.skels) globalFreq.set(s, (globalFreq.get(s) || 0) + 1);
    const out = [];
    for (const [family, list] of Object.entries(families)) {
      const freq = new Map();
      for (const p of list) for (const s of p.skels) freq.set(s, (freq.get(s) || 0) + 1);
      // 家族の芯: 半分以上のメールに出る行（ひな形の本体）
      const core = [...freq.entries()].filter(([, n]) => n >= Math.max(2, list.length * 0.5)).map(([s]) => s);
      for (const p of list) {
        const isType = (l) => (globalFreq.get(skeleton(l)) || 0) >= minShared;
        const shared = p.lines.filter((l) => (freq.get(skeleton(l)) || 0) >= minShared);
        const block = addresseeBlock(p.lines);
        const unique = p.lines.filter((l, i) => i >= block && !isType(l) && !isSignature(l, p.signer, shopLine) && !isSeparator(l));
        const coreHit = core.length ? core.filter((c) => p.skels.includes(c)).length / core.length : 1;
        let kind;
        const isReply = /^\s*(\[[^\]]*\]\s*)?(re)\s*[:：]/i.test(p.subject || "");
        if (isForward(p.subject) || p.lines.slice(0, 3).some((l) => /^お疲れ(様|さま)です/.test(l)) || (opts.internal.length && p.to && p.to.split(/[,\s]+/).every((a) => !a || opts.internal.some((d) => a.toLowerCase().replace(/[<>]/g, "").endsWith(d))))) kind = "社内のやり取り";
        else if (isReply && unique.length && unique.length >= bodyTypeLines(p, isType, shopLine)) kind = "返信での判断";   // お客様の返信に応えた返事（全文がその場の判断）
        else if (p.auto && unique.length) kind = "条件つきの定型";                                  // 自動送信にスタッフの判断は入らない（違いは仕組みの規則）
        else if (list.length < minShared) kind = "個別のやり取り";                 // 型が無い（その件名が2通以下）
        else if (coreHit < 0.5) kind = "別の型";            // ④ ひな形の本体が半分も無い
        else if (unique.length) kind = "書き足し";                                  // ③ 1通にしか出ない文がある
        else if (shared.some((l) => (freq.get(skeleton(l)) || 0) < list.length * 0.5)) kind = "条件つきの定型";  // ② 一部のメールにだけ同じ形で出る段落
        else kind = "ひな形どおり";                                                   // ①
        out.push({ id: p.id, date: p.date, subject: p.subject, family, familySize: list.length, signer: p.signer, auto: p.auto,
                   kind, unique, uniqueSkels: unique.map(skeleton), lines: p.lines, quoted: p.quoted, reservationId: reservationIdOf(p.body) });
      }
    }
    out.shopLine = shopLine;          // 見つけた（または設定された）署名の店名の行
    out.internal = opts.internal;     // 社内とみなしたドメイン
    return out;
  }

  // 書き足しのうち、何通かに同じ形で出る言い回し（型には届かないが、くり返し使われている）
  function repeatedAdditions(results, min = 2) {
    const m = new Map();
    for (const r of results) if (r.kind === "書き足し" || r.kind === "返信での判断") for (const [i, k] of r.uniqueSkels.entries()) {
      const e = m.get(k) || { skeleton: k, example: r.unique[i], count: 0, families: new Set() };
      e.count++; e.families.add(r.family); m.set(k, e);
    }
    return [...m.values()].filter((e) => e.count >= min).sort((a, b) => b.count - a.count).map((e) => ({ ...e, families: [...e.families] }));
  }

  // 宛名・署名・区切り線を除いた、ひな形の行の数
  const bodyTypeLines = (p, isType, shopLine) => { const b = addresseeBlock(p.lines); return p.lines.filter((l, i) => i >= b && isType(l) && !isSignature(l, p.signer, shopLine) && !isSeparator(l)).length; };
  // 冒頭の宛名の欄（会社名・部署名・名前）の行数。「○○様」「御中」の行までで、それより上に文（。）が無いこと
  function addresseeBlock(lines) {
    for (let i = 0; i < Math.min(lines.length, 5); i++) {
      if (/[。．！？]/.test(lines[i]) && !/(様|さま|御中|殿|各位)\s*[,，、]?$/.test(lines[i])) break;
      if (/(様|さま|御中|殿|各位)\s*[,，、]?$/.test(lines[i]) || /^(Dear|Hi|Hello)\b/i.test(lines[i])) return i + 1;
    }
    return lines[0] && isSalutation(lines[0]) ? 1 : 0;
  }
  const isSeparator = (line) => /^[-=＝ー─━_*・\s]{5,}$/.test(line);
  // 本文の1行目の宛名（○○様／○○御中／団体名だけの行／Dear ○○,）
  const isSalutation = (line) => line.length <= 40 && (/(様|御中|さま|各位)\s*[,，、]?$/.test(line) || /^(Dear|Hi|Hello)\b/i.test(line) || !/[。．.!！?？、]/.test(line));

  function isSignature(line, signer, shopLine = "") {
    return !!signer && (line === signer || (!!shopLine && line.replace(/\s+/g, "") === shopLine.replace(/\s+/g, "")));
  }

  // 「変更・キャンセル」リンクの t= から予約IDを取り出す（中身は {"reservationId":..., "exp":...}）
  function reservationIdOf(body) {
    const m = /manage\.html\?t=([A-Za-z0-9_-]+)\./.exec(String(body || ""));
    if (!m) return null;
    try {
      const b64 = m[1].replace(/-/g, "+").replace(/_/g, "/");
      const json = typeof atob === "function" ? decodeURIComponent(escape(atob(b64))) : Buffer.from(b64, "base64").toString("utf8");
      return JSON.parse(json).reservationId || null;
    } catch (_) { return null; }
  }

  // ---- 確認なしで伏せるための歯止め（迷ったら伏せる・伏せきれなければ送らない） ----
  // 名前の候補: カタカナの語・英字の大文字で始まる語。店の決まり文句は何通にも出るが、名前は1〜2通にしか出ない
  const CANDIDATE = /[ァ-ヶ][ァ-ヶー]+|[A-Za-z][A-Za-z'’-]*/g;
  const ALWAYS_OK = new Set(["AM", "PM", "JST", "OK", "Mr", "Ms", "Mrs", "Miss", "Mx", "Dear", "Hi", "Hello", "Re", "Fw", "Fwd",
    ...MONTHS.split("|"), ...DAYS.split("|")]);
  function rareNames(lineSets, maxDocs = 2) {
    const df = new Map(), lower = new Set();
    for (const lines of lineSets) {
      const seen = new Set();
      for (const l of lines) for (const t of l.match(CANDIDATE) || []) {
        if (/^[a-z]/.test(t)) lower.add(t);
        seen.add(t);
      }
      for (const t of seen) df.set(t, (df.get(t) || 0) + 1);
    }
    const rare = new Set();
    for (const [t, n] of df) {
      if (n > maxDocs || ALWAYS_OK.has(t)) continue;
      if (/^[A-Za-z]/.test(t)) {
        if (!/^[A-Z]/.test(t) || lower.has(t.toLowerCase())) continue;   // 小文字でも使われる語はふつうの単語
      }
      rare.add(t);
    }
    return rare;
  }
  const maskRare = (text, rare) => text.replace(CANDIDATE, (t) => (rare.has(t) ? "［固有名詞］" : t));

  // 伏せた後にまだ怪しい所が残っているか（残っていればその行は送らない）
  // 「様」「さん」の前に来てよい語（名前ではないもの）。これ以外はすべて名前とみなして伏せる（迷ったら伏せる）。
  // 「子」「名」で終わるだけの語は通さない（花子様・椎名様は名前）。人数の「名様」は数字・数・何の直後だけ
  const HONORIFIC_OK = /^(お?客|皆|皆々|みな|みなさ|各|お?子|名|団体|先方|先生|生徒|担当者?|参加者|保護者|見学者|代表者|利用者|添乗員|お連れ|ご?家族|ご?一行|数名|何名|若干名|同|仕|模|多|異|一|文|今|たく|疲れ|世話|馳走|苦労|互い|おかげ|ご?一緒|奥|神|王)$/;
  const BEFORE_HONORIFIC = /([一-龥々ァ-ヶーA-Za-zＡ-Ｚａ-ｚ]+[ぁ-ん]{0,4}|[ぁ-ん]{2,6})(?=(様|さま|さん|御中)(?![子々式相化]))/g;
  // ［名前］のすぐ隣にある英字の大文字の語（Kevin ［名前］様／［名前］ Smith）は名前の一部として一緒に伏せる。
  // 同じお客様と何度もやり取りすると英字の名前は何通にも出て「めったに出ない語」にならないため
  const LATIN_NEXT_TO_NAME = /((?:[A-Z][A-Za-z'’-]*[ 　]+)+)(?=［名前］)|(?<=［名前］)((?:[ 　]+[A-Z][A-Za-z'’-]*)+)/g;
  const maskLatinNextToName = (text) => text.replace(LATIN_NEXT_TO_NAME, (m) => {
    const words = m.trim().split(/[ 　]+/);
    const keep = words.filter((w) => ALWAYS_OK.has(w.replace(/[.,]/g, "")));
    return keep.length === words.length ? m : (m.startsWith(" ") || m.startsWith("　") ? " " : "") + (keep.length ? keep.join(" ") + " " : "") + (m.endsWith(" ") || m.endsWith("　") ? "" : "");
  }).replace(/［名前］(?:[ 　]*［名前］)+/g, "［名前］");
  const maskHonorifics = (text) => maskLatinNextToName(text.replace(BEFORE_HONORIFIC, (w) => (HONORIFIC_OK.test(w) ? w : "［名前］")));

  // 住所の番地（雷門2-1-1・2丁目1番1号）は［住所］にする
  const maskAddress = (text) => text
    .replace(/\d{1,4}丁目(\d{1,4}番地?)?(\d{1,4}号?)?/g, "［住所］")
    .replace(/(?<![\d,.])\d{1,4}[-‐－−ー]\d{1,4}[-‐－−ー]\d{1,4}(?![\d])/g, "［住所］")
    .replace(/(?<=[一-龥々ァ-ヶ])\d{1,4}[-‐－−]\d{1,4}(?![\d名人席部時:：])/g, "［住所］")
    .replace(/(?:［住所］[ 　]*)+/g, "［住所］");

  // 宛名の欄（Dear ○○／Mr. ○○／○○様）に出てきた英字の名前。常連は何通にも出るので、出た回数ではなく宛名で覚える
  function addresseeLatinNames(results) {
    const names = new Set();
    for (const r of results) for (const l of (r.lines || []).slice(0, addresseeBlock(r.lines || []))) {
      for (const w of l.match(/[A-Z][A-Za-z'’-]+/g) || []) if (!ALWAYS_OK.has(w)) names.add(w);
    }
    return names;
  }
  const maskLatinNames = (text, names) => (names.size ? text.replace(/[A-Za-z][A-Za-z'’-]+/g, (w) => (names.has(w) ? "［名前］" : w)).replace(/［名前］(?:[ 　]*［名前］)+/g, "［名前］") : text);

  // 伏せた後にまだ怪しい所が残っているか（残っていればその行は送らない）
  function suspicious(text) {
    if (/@|https?:/.test(text)) return "メールアドレスかURL";
    if (/\d{1,4}[-‐－−]\d{1,4}[-‐－−]\d{1,4}|\d+丁目|\d+番地/.test(text)) return "住所の番地";
    if (/\d{6,}/.test(text.replace(/［[^］]*］/g, ""))) return "長い番号";
    if (maskHonorifics(text) !== text) return "「様」の前に伏せていない語";
    return null;
  }

  // 署名に出てくるスタッフの名前（予約デスク等の自動の署名は除く）。本文中でも伏せる
  const staffNames = (results) => [...new Set(results.filter((r) => !r.auto).map((r) => r.signer).filter((s) => s && s.length <= 12))];

  const api = { skeleton, split, analyze, reservationIdOf, familyOf, repeatedAdditions, rareNames, maskRare, maskHonorifics, suspicious, staffNames, addresseeBlock, maskAddress, addresseeLatinNames, maskLatinNames, guessShopLine, guessInternal };
  if (typeof module !== "undefined") module.exports = api; else root.MailScan = api;
})(typeof window !== "undefined" ? window : globalThis);
