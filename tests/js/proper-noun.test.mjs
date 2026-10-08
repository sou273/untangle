// 固有名詞と辞書に無い語を伏せる（架空の名前・会社。実在のお客様の情報は使わない）
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const kuromoji = require("kuromoji");
const PN = require("../../untangle/static/proper-noun.js");
const tokenizer = await new Promise((ok, ng) =>
  kuromoji.builder({ dicPath: new URL("../../node_modules/kuromoji/dict/", import.meta.url).pathname }).build((e, t) => (e ? ng(e) : ok(t))));

test("人名・会社名・地名を伏せ、普通の言葉は残す", () => {
  const m = (t, keep) => PN.maskProper(t, tokenizer, keep);
  assert.equal(m("佐藤花子様のご予約"), "［名前］様のご予約");
  assert.equal(m("トヨタ自動車の皆さんがご見学"), "［団体］の皆さんがご見学");
  assert.equal(m("筋力が足らず二人羽織のように付き添ってください"), "筋力が足らず二人羽織のように付き添ってください");
  assert.equal(m("フルサポートは貸切が必要です"), "フルサポートは貸切が必要です");
  assert.equal(m("［名前］様とThank you"), "［名前］様とThank you");   // ［］の中と英字は触らない
});

test("お店のひな形に出てくる固有名詞は伏せない", () => {
  const keep = PN.shopVocabulary(["創吉 切子体験教室", "浅草の吾妻橋たもとにお集まりください"], tokenizer);
  assert.equal(PN.maskProper("浅草の吾妻橋たもとでお待ちしております", tokenizer, keep), "浅草の吾妻橋たもとでお待ちしております");
  assert.equal(PN.maskProper("横浜からお越しの方", tokenizer, keep), "［地名］からお越しの方");
});

test("国の名前は伏せない（判断の中身が消えないように）", () => {
  assert.equal(PN.maskProper("日本の電話番号をお持ちでないお客様には", tokenizer), "日本の電話番号をお持ちでないお客様には");
});

test("宛名に出てきた名前は、本文で様が付かなくても伏せる（普通の言葉と同じ形の名字も）", () => {
  const names = PN.addresseeNames(["森様", "さくら食品 林 花子様"], tokenizer);
  assert.ok(names.has("森") && names.has("林") && !names.has("食品"));
  assert.equal(PN.maskProper("森からご連絡いただいた件", tokenizer, new Set(), names), "［名前］からご連絡いただいた件");
  assert.equal(PN.maskProper("森林浴のような時間", tokenizer, new Set(), names), "森林浴のような時間");   // 別の言葉の一部は巻き込まない
});
