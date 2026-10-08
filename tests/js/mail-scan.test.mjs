// 架空のメールで、型からの外れ方の見分けを確かめる（実在のお客様の情報は使わない）
import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const MS = require("../../untangle/static/mail-scan-core.js");

const tok = (id) => Buffer.from(JSON.stringify({ reservationId: id, exp: 1 })).toString("base64url") + ".sig";
const confirm = (name, date, n, extra = [], signer = "予約デスク", cond = false) => [
  `${name}様`, "", "この度は切子体験 90分コースのお申し込みありがとうございます。", `${date} 大人${n}名様で承りました。`, "",
  ...extra,
  "集合場所：浅草店", "基本料金：大人 ¥3,630/名", "支払方法：現金", "",
  ...(cond ? ["こちらの回の終了時刻より前にショップが閉店いたしますので、体験時間中のお会計をお願いしております。"] : []),
  "諸注意：", "・お部屋ではお靴をお脱ぎいただいてお上がりいただいておりますのでご了承ください。", "",
  `ご予約内容の変更・キャンセルはこちら: https://booking.sokichi-workshop.com/manage.html?t=${tok("r-" + name)}`, "",
  "創吉 切子体験教室", signer,
].join("\n");
const SUBJ = "切子体験へのご予約ありがとうございます";
const mails = [
  ...["山田", "田中", "鈴木", "高橋", "伊藤", "渡辺"].map((n, i) => ({ id: "a" + i, subject: SUBJ, body: confirm(n, `2026/10/1${i}(土) 10:00`, i + 1) })),
  ...["中村", "小林", "加藤"].map((n, i) => ({ id: "c" + i, subject: SUBJ, body: confirm(n, `2026/10/2${i}(日) 17:00`, 2, [], "山本", true) })),
  { id: "x1", subject: SUBJ, body: confirm("吉田", "2026/10/30(金) 10:15", 1, ["また、木箱とギフトボックスについては当日お決めいただけます。"], "山本") },
  { id: "x2", subject: SUBJ, body: ["架空旅行株式会社様", "", "創吉 切子体験教室です。", "恐れ入りますが本日は担当が不在のため、2026/11/18(水) 14:40 90分コース 6席のご予約へのご回答ができません。", "明日以降ご連絡させていただきますので今しばらくお待ちください。", "", "創吉 切子体験教室", "川口"].join("\n") },
  { id: "r1", subject: "Re: " + SUBJ, body: ["藤井様", "", "ご予約のお取り消しを承りました。", "", "創吉 切子体験教室", "山本", "", "> 2026/10/02 3:54、x@example.jpのメール:", "> キャンセルさせてください"].join("\n") },
];

test("型からの外れ方を4種類に分ける", () => {
  const r = Object.fromEntries(MS.analyze(mails, { autoSigners: ["予約デスク"] }).map((x) => [x.id, x]));
  assert.equal(r.a0.kind, "ひな形どおり"); assert.equal(r.a0.auto, true);
  assert.equal(r.c0.kind, "条件つきの定型"); assert.equal(r.c0.signer, "山本");
  assert.equal(r.x1.kind, "書き足し");
  assert.deepEqual(r.x1.unique, ["また、木箱とギフトボックスについては当日お決めいただけます。"]);
  assert.equal(r.x2.kind, "別の型");
  assert.equal(r.a0.reservationId, "r-山田");
  assert.equal(r.r1.family, SUBJ);                       // Re: は同じ家族
  assert.ok(!r.r1.unique.some((l) => l.includes("キャンセルさせて")));  // 引用は外す
});

test("骨組みは毎回変わる部分を記号にする", () => {
  assert.equal(MS.skeleton("2026/10/06(火) 13:00 大人2名様で承りました。"), "<DATE> <TIME> 大人<N>名様で承りました。");
  assert.equal(MS.skeleton("加藤大典様"), "<宛名>様");
});

test("個別のやり取りと社内のやり取りを分ける", () => {
  const r = MS.analyze([
    { id: "p1", subject: "コップのデザイン", to: "a@example.jp", body: "いつもありがとうございます" },
    { id: "f1", subject: "名入れ依頼　Fwd: フォントについて", to: "x@midori.example", body: "よろしく" },
    { id: "s1", subject: "請求書発行のお願い", to: "shanai@midori.example", body: "お願いします" },
  ], { internal: ["@midori.example"] });
  const k = Object.fromEntries(r.map((x) => [x.id, x.kind]));
  assert.deepEqual(k, { p1: "個別のやり取り", f1: "社内のやり取り", s1: "社内のやり取り" });
});

test("英語の日付・文中の宛名・時刻を置き換える", () => {
  assert.equal(MS.skeleton("We look forward to seeing you on Saturday, October 11, 2026 at 3:00 PM."), "We look forward to seeing you on <DAY> <DATE> at <TIME>.");
  assert.equal(MS.skeleton("山田様のご予約について、10月11日 10時15分"), "<宛名>様のご予約について、<DATE> <TIME>");
  assert.equal(MS.familyOf("[External] Re: 【ご返信ください】10月11日 の切子体験について"), "【ご返信ください】<N>月<N>日 の切子体験について");
});

test("件名をまたいだひな形・区切り線・1行目の宛名・返信を見分ける", () => {
  const conf = (n) => ({ id: "k" + n, subject: "確定", body: ["X様", "", "ご来店をお待ちしております。", "諸注意：", "・お靴をお脱ぎください。", "", "創吉 切子体験教室", "予約デスク"].join("\n") });
  const mails = [conf(1), conf(2), conf(3), conf(4),
    { id: "j1", subject: "ご予約調整のお願い", body: ["城北の会", "", "以下の通りご予約を承りました。", "----------------------------------", "ご来店をお待ちしております。", "諸注意：", "", "創吉 切子体験教室", "山本"].join("\n") },
    { id: "j2", subject: "ご予約調整のお願い", body: ["Y様", "", "空きがございません。", "", "創吉 切子体験教室", "山本"].join("\n") },
    { id: "j3", subject: "ご予約調整のお願い", body: ["Z様", "", "空きがございません。", "", "創吉 切子体験教室", "山本"].join("\n") },
    { id: "r1", subject: "Re: ご予約調整のお願い", body: ["W様", "", "お支払いについて承りました。", "", "創吉 切子体験教室", "山本"].join("\n") },
  ];
  const r = Object.fromEntries(MS.analyze(mails, { autoSigners: ["予約デスク"] }).map((x) => [x.id, x]));
  assert.deepEqual(r.j1.unique, ["以下の通りご予約を承りました。"]);   // 区切り線の下のひな形と1行目の団体名は数えない
  assert.equal(r.r1.kind, "返信での判断");
});

test("宛名・団体名を伏せ、数や役割の「様」は残す（架空の文）", async () => {
  globalThis.window = globalThis.window || {};
  const fs = await import("node:fs");
  new Function("window", fs.readFileSync(new URL("../../untangle/static/local-ai.js", import.meta.url), "utf8"))(globalThis.window);
  const m = async (t) => (await globalThis.window.LocalAI.mask(t, [], false)).masked;
  assert.equal(await m("北山市立青葉中学校様のご予約"), "［団体］様のご予約");
  assert.equal(await m("3. 領収書のお宛名：山川工業株式会社 川口太郎 様（ご変更の場合はお知らせください）"), "3. 領収書のお宛名：［宛名］（ご変更の場合はお知らせください）");
  assert.equal(await m("Dear Mr./Ms. Ms Jane DOE / ABC1234567,"), "Dear Mr./Ms. ［名前］ / ［番号］,");
  assert.equal(await m("陳 小明 様"), "［名前］様");
  assert.equal(await m("ひまわりの会様"), "［団体］様");
  for (const keep of ["大人13名様1部屋", "お子様1名様", "団体様", "皆様", "生徒様", "ご担当者様", "参加者の皆様"]) assert.equal(await m(keep), keep);
});

test("自動送信は判断に数えず、引用からお客様の文を取り出す", () => {
  const auto = (n, extra) => ({ id: "a" + n, subject: "確定", body: ["X様", "ご来店をお待ちしております。", ...(extra ? [extra] : []), "", "創吉 切子体験教室", "予約デスク"].join("\n") });
  const r = MS.analyze([auto(1), auto(2), auto(3), auto(4, "見学は1名までです。")], { autoSigners: ["予約デスク"] });
  assert.equal(r.find((x) => x.id === "a4").kind, "条件つきの定型");
  const s = MS.split(["承知いたしました。", "", "2026年9月1日(火) 12:00 山田 <taro@example.com>:", "> 子供2名に変更できますか", "> よろしくお願いします"].join("\n"));
  assert.deepEqual(s.lines, ["承知いたしました。"]);
  assert.deepEqual(s.quoted, ["子供2名に変更できますか", "よろしくお願いします"]);
});

test("めったに出ないカタカナ・英字の語は伏せ、決まり文句は残す（架空の文）", () => {
  const tmpl = ["グラスをお選びください。", "Thank you for your reservation.", "We look forward to seeing you."];
  const docs = [...Array(5)].map(() => tmpl);
  docs.push(["Mr./Ms. Patricia EXAMPLE", "グラスはフルボトルではありません。"]);
  docs.push(["ホセ様のご予約です。", "Unfortunately we are full. unfortunately"]);
  const rare = MS.rareNames(docs);
  assert.ok(rare.has("Patricia") && rare.has("EXAMPLE") && rare.has("ホセ"));
  assert.ok(!rare.has("グラス") && !rare.has("Thank") && !rare.has("Mr") && !rare.has("Unfortunately"));
  assert.equal(MS.maskRare("Mr./Ms. Patricia EXAMPLE", rare), "Mr./Ms. ［固有名詞］ ［固有名詞］");
});

test("伏せきれていない行を見つける", () => {
  assert.equal(MS.suspicious("［名前］様のご予約を承りました。"), null);
  assert.equal(MS.suspicious("大人13名様・お子様・皆様・ご担当者様"), null);
  assert.ok(MS.suspicious("山田様のご予約"));
  assert.ok(MS.suspicious("連絡先 abc@example.com"));
  assert.ok(MS.suspicious("予約番号 1234567"));
  assert.equal(MS.suspicious("小計29,040円・¥21,075"), null);
});

test("「子」「名」で終わる名字は通さず、人数とお子様は通す", async () => {
  assert.ok(MS.suspicious("椎名様のご予約"));
  assert.ok(MS.suspicious("花子様のご予約"));
  for (const ok of ["4歳、5歳のお子様は付き添いが必要です", "日本の電話番号をお持ちでないお客様には", "2名様・数名様・何名様"]) assert.equal(MS.suspicious(ok), null, ok);
  const fs = await import("node:fs");
  globalThis.window = globalThis.window || {};
  new Function("window", fs.readFileSync(new URL("../../untangle/static/local-ai.js", import.meta.url), "utf8"))(globalThis.window);
  const m = async (t) => (await globalThis.window.LocalAI.mask(t, [], false)).masked;
  assert.equal(await m("椎名様のご予約"), "［名前］様のご予約");
  assert.equal(await m("花子様と2名様"), "［名前］様と2名様");
});

test("「様」「さん」の前は名前でないと分かる語以外を伏せる（架空の名前）", () => {
  const m = (t) => MS.maskHonorifics(t);
  assert.equal(m("林さくら様"), "［名前］様");
  assert.equal(m("つばささんにお伝えください"), "［名前］さんにお伝えください");
  assert.equal(m("椎名様と花子様"), "［名前］様と［名前］様");
  for (const keep of ["たくさんのご来店", "お疲れさまです", "同様に", "ご様子", "皆さん", "お子様", "2名様", "数名様", "お客様", "ご担当者様", "おかげさまで", "参加者の皆様", "5歳のお子様"])
    assert.equal(m(keep), keep, keep);
});

test("署名のスタッフ名を集める（自動の署名は除く）", () => {
  const r = [{ signer: "山本" }, { signer: "予約デスク", auto: true }, { signer: "山本" }, { signer: "" }, { signer: "川口" }];
  assert.deepEqual(MS.staffNames(r), ["山本", "川口"]);
});

test("冒頭の宛名の欄（会社名・部署名・名前）をまるごと外す（架空の会社）", () => {
  assert.equal(MS.addresseeBlock(["さくら食品株式会社", "総務部", "山田様", "ご連絡ありがとうございます。"]), 3);
  assert.equal(MS.addresseeBlock(["みどり食品", "名前様", "ご見学は可能です。"]), 2);
  assert.equal(MS.addresseeBlock(["いつもお世話になっております。", "ご見学は可能です。"]), 0);
  const tmpl = (first) => ({ subject: "見学", body: [first, "ご連絡ありがとうございます。", "ご見学は可能です。", "", "創吉 切子体験教室", "スタッフ"].join("\n") });
  const r = MS.analyze([tmpl("A様"), tmpl("B様"), tmpl("C様"), { subject: "見学", body: ["みどり食品", "D様", "ご連絡ありがとうございます。", "ご見学は可能です。", "", "創吉 切子体験教室", "スタッフ"].join("\n") }]);
  assert.ok(r.every((x) => !x.unique.some((l) => l.includes("みどり"))));
});

test("会社名の語尾で伏せ、普通の語は巻き込まない（架空の会社）", async () => {
  const fs = await import("node:fs");
  globalThis.window = globalThis.window || {};
  new Function("window", fs.readFileSync(new URL("../../untangle/static/local-ai.js", import.meta.url), "utf8"))(globalThis.window);
  const m = async (t) => (await globalThis.window.LocalAI.mask(t, [], false)).masked;
  assert.equal(await m("さくら食品のご担当者様より"), "［団体］のご担当者様より");
  assert.equal(await m("みどりグループの皆様"), "［団体］の皆様");
  for (const keep of ["反省しております", "社員旅行でのご利用", "近くのホテルまで"]) assert.equal(await m(keep), keep);
});

test("［名前］の隣の英字の名前も伏せ、Dear・Mr・Thank you は残す（架空の名前）", () => {
  const m = (t) => MS.maskHonorifics(t);
  assert.equal(m("Kevin ［名前］様"), "［名前］様");
  assert.equal(m("［名前］ Smith様"), "［名前］様");
  assert.equal(m("Dear Kevin ［名前］様"), "Dear ［名前］様");
  for (const keep of ["Dear ［名前］", "Mr. ［名前］", "Thank you ［名前］様"]) assert.equal(m(keep), keep);
});

test("住所の番地を伏せ、人数・時刻・金額は残す", () => {
  assert.equal(MS.maskAddress("雷門2-1-1"), "雷門［住所］");
  assert.equal(MS.maskAddress("2丁目1番1号まで"), "［住所］まで");
  for (const keep of ["12〜24名", "10:15-11:45", "1-2名様", "合計29,040円"]) assert.equal(MS.maskAddress(keep), keep);
  assert.ok(MS.suspicious("台東区1-2-3"));
});

test("宛名の英字の名前は、何通に出ても本文で伏せる（架空の名前）", () => {
  const n = MS.addresseeLatinNames([{ lines: ["Dear Jane Example,", "Thank you."] }]);
  assert.equal(MS.maskLatinNames("Jane Example asked about the course. Thank you", n), "［名前］ asked about the course. Thank you");
});


test("署名の店名の行と社内のドメインを、メールから見つける（架空の店）", () => {
  const m = (i, signer) => ({ id: "s" + i, subject: "ご予約", from: "みどり工房 <info@midori.example>", body: [`客${i}様`, "ご予約ありがとうございます。", "", "みどり工房", signer].join("\n") });
  const mails = [m(1, "山本"), m(2, "川口"), m(3, "自動受付"), m(4, "山本")];
  const r = MS.analyze(mails, { autoSigners: ["自動受付"] });
  assert.equal(r.shopLine, "みどり工房");
  assert.deepEqual(r.internal, ["@midori.example"]);
  assert.deepEqual(r.map((x) => [x.signer, x.auto]), [["山本", false], ["川口", false], ["自動受付", true], ["山本", false]]);
});
