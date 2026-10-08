// 日本語の辞書（kuromoji）を、ページとは別の裏の作業場（Web Worker）で動かす。
// 辞書の展開（約17MB）と単語の切り分けは重く、ページと同じ所で動かすとページが固まるため。
// メールの文はこの作業場とページの間だけでやり取りし、外には送らない（外から読むのは辞書のファイルだけ）。
// ページはこのファイルを読んでその場で組み立てて（Blob）立てる。ファイルの URL から直接立てると、
// 本番の環境で辞書の読み込みが返ってこないことがあったため。その場合の自分の場所は name で受け取る
const BASE = location.protocol === "blob:" ? self.name : location.origin;
importScripts("https://cdn.jsdelivr.net/npm/kuromoji@0.1.2/build/kuromoji.js", BASE + "/static/proper-noun.js");

const ready = new Promise((ok, ng) => {
  kuromoji.builder({ dicPath: "https://cdn.jsdelivr.net/npm/kuromoji@0.1.2/dict/" }).build((e, t) => (e ? ng(e) : ok(t)));
});

onmessage = async (e) => {
  const { id, op, texts, lines, keep } = e.data;
  try {
    const t = await ready;
    if (op === "init") return postMessage({ id, result: true });
    if (op === "vocab") return postMessage({ id, result: [...ProperNoun.shopVocabulary(lines, t)] });
    if (op === "names") return postMessage({ id, result: [...ProperNoun.addresseeNames(lines, t, new Set(keep || []))] });
    if (op === "mask") {
      const k = new Set(keep || []), n = new Set(e.data.names || []);
      return postMessage({ id, result: texts.map((x) => ProperNoun.maskProper(x, t, k, n)) });
    }
    postMessage({ id, error: `知らない操作: ${op}` });
  } catch (err) {
    postMessage({ id, error: String(err && err.message || err) });
  }
};
