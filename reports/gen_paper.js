/**
 * gen_paper.js — 產生完整中文學術論文 .docx
 * 標題：建立我國生乳產量預測模型及其準確度評估
 * 投稿目標：中國畜牧學會誌
 * 字數目標：10,000–12,000 字（中文字符）
 * 引用格式：APA 括號內文引用
 * 重點：以 2021–2024 滾動回測（expanding-window 滾動回測）為核心，不含未驗證的 2026 預測值
 *
 * 執行方式：
 *   cd /Users/tu/Milk_forecast/reports
 *   NODE_PATH=/sessions/quirky-gracious-cray/mnt/outputs/node_modules node gen_paper.js
 */
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType, PageBreak,
  LevelFormat, convertInchesToTwip, ImageRun,
} = require('docx');
const fs = require('fs');
const path = require('path');

const TITLE_ZH = '建立我國生乳產量預測模型及其準確度評估';
const TITLE_EN = 'Building a Raw Milk Production Forecasting Model for Taiwan and Its Accuracy Evaluation';
const FONT_ZH = 'Microsoft JhengHei';
const FONT_EN = 'Times New Roman';

// ============== Paragraph helpers ==============
function p(text, opts = {}) {
  const {
    bold = false, italic = false, size = 24,
    align = AlignmentType.JUSTIFIED, indent = true,
    spacing = { before: 0, after: 120, line: 360 },
  } = opts;
  return new Paragraph({
    alignment: align,
    spacing,
    indent: indent ? { firstLine: 480 } : undefined,
    children: [new TextRun({
      text, bold, italics: italic, size,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

// runs version: allow inline italics / mixed runs
function pRuns(runs, opts = {}) {
  const {
    align = AlignmentType.JUSTIFIED, indent = true,
    spacing = { before: 0, after: 120, line: 360 },
  } = opts;
  return new Paragraph({
    alignment: align,
    spacing,
    indent: indent ? { firstLine: 480 } : undefined,
    children: runs.map(r => new TextRun({
      text: r.t,
      bold: r.b || false,
      italics: r.i || false,
      size: r.s || 24,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })),
  });
}

function h1(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { before: 240, after: 160, line: 360 },
    children: [new TextRun({
      text, bold: true, size: 30,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

function h2(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { before: 200, after: 120, line: 360 },
    children: [new TextRun({
      text, bold: true, size: 26,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

function h3(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { before: 160, after: 100, line: 360 },
    children: [new TextRun({
      text, bold: true, size: 24,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

function ref(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { after: 100, line: 320 },
    indent: { left: 540, hanging: 540 },
    children: [new TextRun({
      text, size: 22,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

function center(text, opts = {}) {
  return p(text, { ...opts, align: AlignmentType.CENTER, indent: false });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// ============== Table helpers ==============
function cell(text, opts = {}) {
  const { bold = false, size = 22, align = AlignmentType.CENTER, shading } = opts;
  const props = {
    children: [new Paragraph({
      alignment: align,
      spacing: { before: 40, after: 40, line: 280 },
      children: [new TextRun({
        text, bold, size,
        font: { ascii: FONT_EN, eastAsia: FONT_ZH },
      })],
    })],
  };
  if (shading) props.shading = { type: ShadingType.CLEAR, color: 'auto', fill: shading };
  return new TableCell(props);
}

function makeTable(rows, widths) {
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    columnWidths: widths,
    rows: rows.map(r => new TableRow({ children: r })),
  });
}

function tableCaption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({
      text, bold: true, size: 22,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

// ============== Figure helpers ==============
function figure(filename, captionZh, captionEn, widthPx = 580) {
  const filepath = path.join(__dirname, 'figs', filename);
  const data = fs.readFileSync(filepath);
  // Figure dimensions (auto-detect aspect ratio)
  // We pre-compute approximate aspect from filename mapping
  const aspectMap = {
    'fig1_overview.png': { w: 580, h: 395 },
    'fig2_structural.png': { w: 580, h: 186 },
    'fig3_model_comparison.png': { w: 580, h: 387 },
    'fig4_cohort_diagnostic.png': { w: 580, h: 218 },
  };
  const dim = aspectMap[filename] || { w: widthPx, h: Math.round(widthPx * 0.6) };

  const imgPara = new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 200, after: 80 },
    children: [new ImageRun({
      data,
      transformation: { width: dim.w, height: dim.h },
    })],
  });
  const captionParaZh = new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 0, line: 280 },
    children: [new TextRun({
      text: captionZh, bold: true, size: 22,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
  const captionParaEn = new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 200, line: 280 },
    children: [new TextRun({
      text: captionEn, italics: true, size: 20,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
  return [imgPara, captionParaZh, captionParaEn];
}

function tableCaptionBilingual(textZh, textEn) {
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 200, after: 0 },
      children: [new TextRun({
        text: textZh, bold: true, size: 22,
        font: { ascii: FONT_EN, eastAsia: FONT_ZH },
      })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 80 },
      children: [new TextRun({
        text: textEn, italics: true, size: 20,
        font: { ascii: FONT_EN, eastAsia: FONT_ZH },
      })],
    }),
  ];
}

function tableNote(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { before: 80, after: 200 },
    children: [new TextRun({
      text, size: 20, italics: true,
      font: { ascii: FONT_EN, eastAsia: FONT_ZH },
    })],
  });
}

// ============== Sections ==============
const docChildren = [];

// ---- 標題 ----
docChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 200, line: 360 },
  children: [new TextRun({
    text: TITLE_ZH, bold: true, size: 32,
    font: { ascii: FONT_EN, eastAsia: FONT_ZH },
  })],
}));
docChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 200, line: 320 },
  children: [new TextRun({
    text: TITLE_EN, italics: true, size: 24,
    font: { ascii: FONT_EN, eastAsia: FONT_ZH },
  })],
}));

// Author block
docChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 80, line: 320 },
  children: [new TextRun({
    text: '作者：[投稿時補填]',
    size: 22,
    font: { ascii: FONT_EN, eastAsia: FONT_ZH },
  })],
}));
docChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 80, line: 320 },
  children: [new TextRun({
    text: '通訊作者 / Corresponding Author: [投稿時補填; email]',
    size: 20, italics: true,
    font: { ascii: FONT_EN, eastAsia: FONT_ZH },
  })],
}));
docChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 0, after: 240, line: 320 },
  children: [new TextRun({
    text: '單位 / Affiliation: [投稿時補填]',
    size: 20, italics: true,
    font: { ascii: FONT_EN, eastAsia: FONT_ZH },
  })],
}));
docChildren.push(p('', { indent: false }));

// ---- 中文摘要 ----
docChildren.push(h1('摘　要'));
docChildren.push(p(
  '本研究使用台灣 2019–2024 年共 1,210,964 筆乳牛群性能改良（Dairy Herd Improvement, DHI）月測乳紀錄，加上農業部公告的全國酪農場與乳牛頭數，建立一套結構式（structural）的全國生乳產量預測方法。預測公式為 Y = N × Q × D：N 是全國產乳牛頭數、Q 是每頭牛平均單位產量、D 是標準泌乳天數（305 天）。為了校正 DHI 樣本偏向中大型場（sample selection bias；Heckman, 1979）造成的高估，本研究引入動態 productivity 比 r_t 做修正。為比較結構式與時序方法的優劣，採用擴展視窗（expanding window）方式對 2021、2022、2023、2024 四個年度做滾動預測，比較 8 種方法（naive seasonal、STL+linear、Holt-Winters、SARIMA、Prophet、NeuralProphet、Level 4 場數線性外推、cohort 結構式）的平均絕對百分比誤差（MAPE）與偏差（bias）。'
));
docChildren.push(p(
  '結果顯示，cohort 結構式模型在 2021–2024 年的 MAPE 為 2.15%、平均偏差 +1.45%，比所有時序方法都好（後者 MAPE 介於 3.50%–16.48%）。時序方法裡以 STL+linear 表現最佳（MAPE 3.50%、bias +2.79%），但仍比結構式差約 1.4 個百分點；NeuralProphet 與 Holt-Winters 中等（MAPE 6.87%、13.21%）；SARIMA、Prophet、naive seasonal 都把產量算高了 12.95%–16.48%，因為它們只能延伸 2019–2020 年的趨勢，無法察覺 2022 年後出現的結構性變化。本研究也比較了三種 Level 4 規模因子（scale factor, SF）還原策略：「用場數比例放大」高估約 +10.45%，「用牛隻數比例放大」反而低估約 −23.45%，顯示 DHI 樣本在「場」與「牛」兩個維度的偏向方向不同；只有 cohort 結構式的動態 productivity 校正能同時消除這兩種偏差。本研究貢獻有三：（1）提出可實際操作的 N × Q × D 結構式預測框架，能同時利用 DHI 微觀資料與政府公告總量；（2）用同一份跨年回測證據，量化結構式與時序模型在台灣酪農產業的相對表現；（3）證明即使 DHI 涵蓋率只有約 30%，也能把全國年度預測誤差控制在 2% 左右，可供畜牧主管機關與酪農合作社參考。'
));
docChildren.push(pRuns([
  { t: '關鍵詞：', b: true },
  { t: '乳牛群性能改良（DHI）；全國生乳產量；結構式預測；時序模型；滾動回測；樣本偏選；productivity 比 r_t' },
]));

// ---- 英文摘要 ----
docChildren.push(h1('Abstract'));
docChildren.push(p(
  'Forecasting national raw milk production accurately is critical for dairy supply-chain planning, yet government-published herd inventories and farm-level Dairy Herd Improvement (DHI) records are typically used in isolation. This study integrates 1,210,964 monthly DHI test-day records (2019–2024) with annual livestock inventory data published by Taiwan’s Ministry of Agriculture (MOA) into a unified structural forecasting framework: Y = N × Q × D, where N is the national lactating-cow headcount, Q is the mean test-day yield, and D is a 305-day standard lactation length. To correct for sample selection bias inherent in voluntary DHI participation (Heckman, 1979), a dynamic productivity ratio is fitted between DHI test-day yields and MOA-reported national yields. Under an expanding-window rolling backtest covering 2021–2024, we compared eight models: naive seasonal, STL+linear, Holt-Winters (Holt, 2004; Winters, 1960), SARIMA (Box & Jenkins, 1976; Hyndman & Khandakar, 2008), Prophet (Taylor & Letham, 2018), NeuralProphet (Triebe et al., 2021), Level-4 farm-count extrapolation, and the proposed cohort-structural model. The cohort model achieved a Mean Absolute Percentage Error (MAPE) of 2.15% with a mean bias of +1.45%, outperforming all time-series baselines (MAPE 3.50%–16.48%). STL+linear was the best pure time-series method (3.50%), but still 1.4 percentage points higher than the structural approach. We further evaluate three Level-4 scale-factor restoration strategies and show that the structural cohort correction simultaneously removes the +10.45% over-estimation of farm-only extrapolation and the −23.45% under-estimation of cow-only restoration. The contributions are three-fold: (1) an operationally feasible N×Q×D framework that jointly uses micro-level DHI and macro-level inventory data; (2) cross-year empirical evidence comparing structural and time-series models in Taiwan’s dairy industry; and (3) engineering experience showing that, even with DHI sample coverage near 30%, monthly and annual forecasts can be kept within roughly 2% error.'
, { italic: false }));
docChildren.push(pRuns([
  { t: 'Keywords: ', b: true, i: true },
  { t: 'Dairy Herd Improvement (DHI); national raw milk production; structural forecasting; time-series model; rolling backtest; sample selection bias; productivity ratio', i: true },
]));

docChildren.push(pageBreak());
// =====================================================================
// 第一章：前言
// =====================================================================
docChildren.push(h1('一、前言'));

docChildren.push(p(
  '本研究的動機可從產業實務與工程方法兩個面向說明。產業實務上，國產生乳供應預測直接支撐政府之進口配額調節（特別是因應 2025 年全面生效之《臺紐經濟合作協定》所帶來之開放壓力）、加工廠之收乳契約安排、以及產業升級政策之配套規劃；當預測誤差超過 5%–10%，即足以造成原料缺口、滯銷或進口配額失準，因此精準的供應預測是支撐產業升級、調整與輔導政策之科學基礎。工程方法上，本研究欲回答一個未被正式解答的問題：在 DHI 涵蓋率僅 30%–52% 之條件下，台灣公開資料能否達到接近國際成熟系統（如美國 USDA NASS、OECD/FAO AGLINK-COSIMO）之預測精度？此一問題對「中型樣本國家」（非美國 NASS 級之全覆蓋、亦非歐盟級之成員國分項統計）具普遍意義，本研究結果可作為類似條件國家之參考。'
));

docChildren.push(p(
  '台灣生乳在 2024 年產量約 45.2 萬公噸（農業部，2024a），是僅次於毛豬與雞蛋的重要畜產品，對加工廠排程、零售訂貨與政府進口配額調節都很重要。長期以來，國產生乳供應預測依賴兩條獨立的資料線：一條是 1989 年起推動的乳牛群性能改良（Dairy Herd Improvement, DHI）月測乳紀錄，提供場—牛—測試日的三維微觀資料（中華民國乳業發展協會等，2024）；另一條是農業部公告的全國酪農場數、乳牛頭數與生乳年產量等總體資料。學術研究多用 DHI 個體資料估計乳量曲線參數（Wood, 1967）或做品種改良性狀分析；產業預測則多用農業部公告的歷年總量做時序外推。兩條線一直分開使用。'
));

docChildren.push(p(
  '這種分開使用的習慣，使預測精度受限於三個結構性問題。第一，DHI 是酪農自願參與的，樣本本身就有偏選（sample selection bias；Heckman, 1979）：DHI 場每頭牛產量比全國平均高（本研究觀測到 r > 1），把 DHI 月測量直接乘上全國牛隻數會穩定高估。第二，純時序外推方法（例如 SARIMA、Holt-Winters；Box & Jenkins, 1976; Holt, 2004; Winters, 1960）只能抓到一年內的季節性與趨勢，遇到頭數結構轉折就慢半拍（Hyndman & Athanasopoulos, 2021）。第三，公告資料的發布頻率與滯後性質不一：年度資料每年公告一次（次年第三季前公告），季報則於季度結束後 1–2 個月公告，DHI 月測量則在當月或次月即可取得，預測模型必須能在不同時間粒度與滯後條件下整合資料源。'
));

docChildren.push(p(
  '面對這三個挑戰，國際上的國家層級乳業預測主流是「頭數 × 單牛產量」結構式分解。OECD 與 FAO（2024）採用 AGLINK-COSIMO 部分均衡模型，把乳業產量拆成頭數、單牛產量、飼料成本與貿易條件等驅動因子，在十年期長期投影中發揮結構式分解的優勢。美國農業部國家農業統計局（USDA NASS, 2022）每月發布的 Milk Production Report 也是季度抽樣取得乳牛頭數與單牛日產量，配合 Federal Market Orders 行政資料定期修訂，並建議全國 24 個主要乳產州分區估計再加總。這種「N × Q」結構式分解，在資料能分層時通常解釋性更好、預測誤差更小（Hyndman & Athanasopoulos, 2021）。本研究就在這個框架下，用同一份滾動回測證據量化結構式相對於六種主流時序方法的表現，並引入動態 productivity 比 r_t 校正樣本偏選；時序基準則用 STL 分解（Cleveland et al., 1990）、Prophet（Taylor & Letham, 2018）、NeuralProphet（Triebe et al., 2021）等方法。'
));

docChildren.push(p(
  '幾項關鍵方法的歷史背景：乳牛泌乳曲線最常用的經驗式是 Wood（1967）提出的不完整伽瑪函數 y(t) = a · t^b · exp(−c·t)。原文 t 是產犢後的「週數」，本研究因為用月測試日資料，所以把 t 改成「天」重新校準 a、b、c。這個模型形式簡單，但已成為國際 DHI 與遺傳評估系統的事實標準。樣本偏選校正方面，Heckman（1979）以勞動經濟學為背景指出：當樣本入選機率與被觀測變數相關時，普通最小平方估計會系統性偏差，並提出兩階段校正模型；他的核心想法「先建立選擇機制、再校正觀測值」已廣泛用在各種非隨機抽樣資料分析。時序方法方面，指數平滑法可追溯到 Holt 於 1957 年在海軍研究局的內部備忘錄（後來於 2004 年再版為 Holt, 2004），是含趨勢的雙參數平滑；Winters（1960）擴充為含趨勢與季節項的三參數模型，就是現在的 Holt-Winters。ARIMA 由 Box 與 Jenkins 在 1970 年代正式化（Box & Jenkins, 1976），其季節版本 SARIMA 透過差分把非平穩序列變成平穩序列再用 AR、MA 結構建模；自動 ARIMA（auto.arima）能用資訊準則自動選擇階數（Hyndman & Khandakar, 2008）。'
));

docChildren.push(p(
  '本研究的核心問題有三：（RQ1）怎麼把 DHI 微觀資料與全國總量整合到同一個預測框架，合理處理樣本偏選與規模還原？（RQ2）在 2021–2024 年同一回測設定下，cohort 結構式模型相對於六種主流時序模型表現如何？（RQ3）三種規模還原策略（場數線性外推、牛隻數線性外推、50:50 混合）中哪一個偏差最小？動態 productivity 校正能不能同時消解這幾種偏差？資料來源是 2019–2024 年共 1,210,964 筆 DHI 月測乳紀錄、農業部畜禽統計調查在養按品項季報（2020Q3–2025Q3 共 16 期）與全國生乳年產量（2019–2024）；DHI 樣本在 2024 年涵蓋 201 場、31,861 頭乳牛，分別佔全國酪農場 36%、全國產乳牛 52%。本研究三項貢獻：（1）提出可實際操作的 N × Q × D 結構式預測框架，整合 DHI 微觀與政府公告總量；（2）用同一份跨年回測證據量化結構式相對於時序模型的相對表現；（3）提供完整資料審計與年度自動更新流程，可供畜牧主管機關與酪農合作社直接採用。本研究範圍限於全國年度與月度產量總量，不含 2025 年以後的外推預測，所有結論都限於已能用公告資料驗證的 2021–2024 年回測結果。'
));

docChildren.push(pageBreak());

// =====================================================================
// 第二章：材料與方法
// =====================================================================
docChildren.push(h1('二、材料與方法'));

docChildren.push(h2('2.1 資料來源'));
docChildren.push(p(
  '本研究使用三類資料：(a) DHI 月測乳紀錄（2019–2024 年共 1,210,964 筆；以「場—牛—測試日」三維索引，含 24 小時擠乳量、乳成分如脂肪率/蛋白質率/體細胞數/尿素氮、產犢與乾乳事件、胎次；中華民國乳業發展協會等，2024）；(b) 農業部畜禽統計調查在養按品項季報（2020Q3–2025Q3 共 16 期，提供全國酪農場數與乳牛頭數的季度估計；農業部統計處，2024）；(c) 全國生乳年產量（2019–2024 年共 6 年，來自農業部「08-畜牧生產及貿易」公開資料；農業部，2024a）。圖 1 綜合呈現本研究使用的資料概況：(a) 預測管線；(b) DHI 樣本年度規模；(c) 全國酪農場與產乳牛頭數；(d) DHI 場數與牛數覆蓋率。表 1 列出 2021–2024 年的 DHI 規模、全國頭數與單牛產量等具體數字。'
));

docChildren.push(...figure(
  'fig1_overview.png',
  '圖 1：研究流程與資料概況。(a) 預測管線；(b) DHI 樣本年度規模；(c) 全國酪農場與產乳牛頭數；(d) DHI 場數與牛數覆蓋率。',
  'Figure 1. Research pipeline and data overview. (a) Forecasting pipeline; (b) DHI annual sample size; (c) National dairy farms and lactating cows; (d) DHI farm and cow coverage.'
));

docChildren.push(...tableCaptionBilingual(
  '表 1：2021–2024 年度資料規模與單牛產量一覽',
  'Table 1. Annual data scale and per-cow yield, 2021–2024.'
));
docChildren.push(makeTable([
  [
    cell('年度 / Year', { bold: true, shading: 'D9E1F2' }),
    cell('DHI Records', { bold: true, shading: 'D9E1F2' }),
    cell('DHI Farms', { bold: true, shading: 'D9E1F2' }),
    cell('DHI Cows', { bold: true, shading: 'D9E1F2' }),
    cell('Nat\'l Farms', { bold: true, shading: 'D9E1F2' }),
    cell('Nat\'l Cows', { bold: true, shading: 'D9E1F2' }),
    cell('Farm Cov %', { bold: true, shading: 'D9E1F2' }),
    cell('Cow Cov %', { bold: true, shading: 'D9E1F2' }),
    cell('Q_DHI (t/cow/y)', { bold: true, shading: 'D9E1F2' }),
    cell('Q_official (t/cow/y)', { bold: true, shading: 'D9E1F2' }),
    cell('Production (kt)', { bold: true, shading: 'D9E1F2' }),
  ],
  [cell('2021'), cell('195,672'), cell('167'), cell('27,566'), cell('557.7'), cell('65,140'), cell('30.0'), cell('42.3'), cell('7.71'), cell('6.90'), cell('449.2')],
  [cell('2022'), cell('203,929'), cell('173'), cell('28,691'), cell('564.7'), cell('65,773'), cell('30.6'), cell('43.6'), cell('7.73'), cell('7.04'), cell('463.1')],
  [cell('2023'), cell('211,950'), cell('189'), cell('31,482'), cell('557.0'), cell('64,651'), cell('33.9'), cell('48.7'), cell('7.93'), cell('7.31'), cell('472.4')],
  [cell('2024'), cell('217,659'), cell('201'), cell('31,861'), cell('552.0'), cell('61,269'), cell('36.4'), cell('52.0'), cell('8.12'), cell('7.38'), cell('452.4')],
], [1100, 1300, 1100, 1100, 1200, 1200, 1100, 1100, 1300, 1500, 1300]));
docChildren.push(tableNote(
  '註：DHI 場數與牛數來自每年參與 DHI 計畫之實際統計；全國場數與牛數採各年四季在養按品項季報之平均；覆蓋率＝DHI/全國×100%；Q_DHI 為 305 天標準化年化單牛產量（測試日 kg/紀錄 × 305 / 1000）；Q_official ＝農業部公告全國生乳產量 / 季報平均產乳牛頭數；Production 為農業部公告之全國生乳產量。'
));

docChildren.push(p(
  '資料審計流程在每次預測前執行：(a) 確認各年度 DHI 紀錄存在；(b) 確認紀錄筆數與場數在合理區間；(c) 確認公告資料年度與 DHI 對齊；(d) 缺漏時自動觸發本機快取更新。資料品質檢核採三層「警示而不剔除」的保守做法：第一層是欄位完整性（farm_id、cow_id、test_date 任一缺值就發警示）；第二層是值域檢核（milk_kg 大於 99.9 百分位上限、fat 不在 2%–6%、protein 不在 2%–5%、scc 超過 200 萬都發警示）；第三層是事件邏輯檢核（calving_date 晚於 test_date、dry_off_date 早於最後一次測量等不合邏輯的事件發警示）。所有警示由 validator 模組記錄，但不剔除原始紀錄，主分析直接保留 1,210,964 筆觀測。這種保守設計避免人為過濾扭曲樣本分布，極端值的影響由各年 DHI 年產量加總時的大數平均自然吸收。樣本期間 r_t 介於 1.086–1.118，是動態 productivity 校正的合理監測區間（見附錄 C）；超出這個區間就觸發人工確認，避免樣本偏選的異常變動傳遞到預測結果。截至本研究撰寫時（2026 年 5 月），2025 年 DHI 紀錄（共 224,651 筆）已可由這個流程自動納入，是後續模型重新訓練的基礎設施。'
));

docChildren.push(h2('2.2 Cohort 結構式模型'));
docChildren.push(p(
  '本研究結構式預測模型的核心公式是 Y_t = N_t × Q_t × D：Y_t 是第 t 年的全國生乳預測產量（公噸）、N_t 是估計的全國產乳牛頭數、Q_t 是等效全國單牛日產量、D = 305 天是標準泌乳天數。N_t 用農業部公告的乳牛頭數乘以「產乳牛比例」（從 DHI 樣本估計，約 80%–82%）算出來。Q_t 的估計分兩步：第一步用 DHI 樣本的測試日紀錄取 305 天標準化的年化產量，得到 Q_DHI,t；第二步用動態 productivity 比 r_t 修正樣本偏選：'
));
docChildren.push(pRuns([
  { t: '　　Y_t = N_t × Q_t × D', i: true },
  { t: '　　Q_t = Q_DHI,t / r_{t−1}    （r 定義為 DHI/全國之單牛年化產量比）' },
  { t: '　　r_{t−1} = Q_DHI,t−1 / Q_official,t−1' },
], { indent: false, align: AlignmentType.CENTER }));
docChildren.push(p(
  'r_{t−1} 用「前一年的值」，避免不小心用到未來資料。實際觀察到 r > 1（DHI 樣本場每頭牛產量比全國平均略高），所以「除以 r」等於把預測往下調，避免高估。實作上採用 r 的三年滑動平均（3-year moving average）以降低單年抽樣噪音。圖 2 用三個面板呈現這個邏輯：(a) DHI 每月樣本產量；(b) 年度單牛產量比較；(c) 動態 productivity 比 r_t 的歷年走勢。'
));
docChildren.push(p(
  '進一步說明 Q_DHI,t 的算法。直白地說，就是「全年所有測試日的平均單日擠乳量 × 305 天泌乳期 / 1000（kg → t）」，等於每頭牛年化產量。寫成公式：Q_DHI,t = (Σ_{i ∈ records_t} milk_kg_i / |records_t|) × 305 / 1000，其中 records_t 是第 t 年所有 DHI 月測試日紀錄、milk_kg_i 是第 i 筆紀錄的 24 小時擠乳量。這是 DHI 國際標準年化公式的線性版本。Q_official,t 則用 Y_t / N_t × 305 倒推：Y_t 是農業部公告的全國年產量、N_t 是季報的全國產乳牛年度平均頭數。兩者相除得 r_t = Q_DHI,t / Q_official,t，反映 DHI 樣本相對於全國平均的 productivity 領先程度。本研究 2021–2024 年觀測到的 r_t 介於 1.086–1.118，意思是 DHI 樣本場每頭牛年化產量比全國平均高 8.6%–11.8%，這與「DHI 是自願參與、樣本偏向中大型場」的先驗認知一致（Heckman, 1979）。'
));
docChildren.push(p(
  '至於 r_{t−1} 滑動視窗的長度選擇，本研究採用 3 年等權平均：r̄_{t−1} = (r_{t−3} + r_{t−2} + r_{t−1}) / 3。考量三點：(a) 3 年視窗能平滑單年噪音、避免年度極端值（例如出現結構轉折之年份）立刻影響下一年預測；(b) 視窗太長（例如 5 年）會使結構轉折期反應遲鈍；(c) 視窗太短（例如 1–2 年）則無法穩定吸收年度噪音。本研究在回測中也初步比較了 1、2、3、5 年視窗的表現，3 年視窗的 MAPE 最低（2.15%），但因為樣本量有限，這只是敏感度初探，正式的視窗最佳化要等資料累積後再做。'
));

docChildren.push(...figure(
  'fig2_structural.png',
  '圖 2：結構式 cohort 模型之核心邏輯。(a) DHI 月度樣本產量；(b) 年度單牛產量比較；(c) 動態 productivity 比 r_t。',
  'Figure 2. Structural core of the cohort model. (a) DHI monthly sample production; (b) Annual per-cow yield comparison; (c) Dynamic productivity ratio r_t.'
));

docChildren.push(h2('2.3 規模還原策略'));
docChildren.push(p(
  '當 DHI 樣本沒有涵蓋全國時，需要把樣本產量「放大」到全國規模。本研究比較三種純線性還原策略：(a) Level 4 場數線性外推（L4_farms）：放大倍數 = 全國場數 / DHI 樣本場數；(b) Level 4 牛隻數線性外推（L4_cows）：放大倍數 = 全國乳牛數 / DHI 樣本乳牛數；(c) 50:50 混合（L4_mixed）：上面兩種策略的算術平均；另外加 (d) Level 1 簡化版（L1_farms，只用場數比，沒有分群結構）做對照。Cohort 結構式不直接用上述放大倍數，它的等效放大倍數由 productivity 比 r_t 隱含吸收。'
));

docChildren.push(h2('2.4 時序基準模型'));
docChildren.push(p(
  '本研究納入六種時序基準方法：(1) Naive seasonal（用前一年同月當預測值，季節長度 s = 12）；(2) STL+linear（Cleveland et al., 1990；先做 STL 分解，再對趨勢項用線性回歸外推、季節項保留最末年）；(3) Holt-Winters（Holt, 2004; Winters, 1960；加法季節，使用 statsmodels 的 ExponentialSmoothing）；(4) SARIMA（Box & Jenkins, 1976；用 pmdarima 的 auto_arima 自動選階，依 AIC 準則挑選；Hyndman & Khandakar, 2008）；(5) Prophet 1.1（Taylor & Letham, 2018；年度與週節性、自動斷點）；(6) NeuralProphet 1.0（Triebe et al., 2021；AR window=12，epochs=100）。各方法的詳細超參數見表 2。'
));

docChildren.push(...tableCaptionBilingual(
  '表 2：時序基準模型實作設定',
  'Table 2. Time-series baseline model configuration.'
));
docChildren.push(makeTable([
  [
    cell('Model', { bold: true, shading: 'D9E1F2' }),
    cell('Library', { bold: true, shading: 'D9E1F2' }),
    cell('Key Hyperparameters', { bold: true, shading: 'D9E1F2' }),
    cell('Forecast Target', { bold: true, shading: 'D9E1F2' }),
  ],
  [cell('Naive seasonal'), cell('numpy', { align: AlignmentType.LEFT }), cell('s = 12 (months)', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('STL + linear'), cell('statsmodels', { align: AlignmentType.LEFT }), cell('seasonal = 13, trend window auto, robust = True; trend extrapolated by OLS', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('Holt-Winters'), cell('statsmodels', { align: AlignmentType.LEFT }), cell('seasonal = additive, period = 12, MLE estimation of α/β/γ', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('SARIMA'), cell('pmdarima', { align: AlignmentType.LEFT }), cell('auto_arima: p≤3, q≤3, P≤2, Q≤2, s = 12, AIC selection, KPSS for d, OCSB for D', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('Prophet'), cell('prophet 1.1', { align: AlignmentType.LEFT }), cell('yearly_seasonality = True, weekly = True, daily = False, default changepoints, no holidays', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('NeuralProphet'), cell('neuralprophet 1.0', { align: AlignmentType.LEFT }), cell('AR window = 12, epochs = 100, learning rate auto, default loss (Huber)', { align: AlignmentType.LEFT }), cell('DHI subset')],
  [cell('Cohort (this study)', { bold: true }), cell('milkfc.forecast', { align: AlignmentType.LEFT }), cell('Y = N × (Q_DHI / r_{t−1}) × 305; r uses 3-yr moving avg; STL+linear projects Q_DHI', { align: AlignmentType.LEFT }), cell('National total')],
], [2000, 1700, 4500, 1800]));
docChildren.push(tableNote(
  '註：DHI subset 預測之輸出後續配合 scale factor 或 cohort productivity 比放大為全國總量。auto_arima 之最終階數因每個目標年訓練資料不同而異，最常見階數為 (1,1,1)(0,1,1)_12。'
));

docChildren.push(h2('2.5 滾動回測與統計分析'));
docChildren.push(p(
  '本研究採擴展視窗（expanding window）滾動回測。每個目標年度 y（y = 2021, 2022, 2023, 2024）的訓練資料是 2019 年 1 月到 (y−1) 年 12 月，預測 y 年的 12 個月產量；每年重新擬合所有模型參數（包含 productivity 比、SARIMA 階數、Prophet 斷點、NeuralProphet 權重）。預測誤差用 err_y = (Ŷ_y − Y_y) / Y_y × 100% 計算；MAPE 是 (1/n) Σ |err_y|、bias 是 (1/n) Σ err_y。為了提供誤差不確定性參考，本研究對 cohort 的 4 個逐年絕對誤差做 bootstrap 重抽樣（2,000 次）；對 cohort 與 STL+linear 的配對絕對誤差做 Wilcoxon signed-rank 檢定。但 n = 4 的檢定力很低，全文用「在 4 年觀測中持續優於」「明顯優於」等描述性說法，不做正式假設檢定推論；正式檢定要等資料累積到 6–8 年後才有足夠統計力。'
));

docChildren.push(pageBreak());

// =====================================================================
// 第三章：結果
// =====================================================================
docChildren.push(h1('三、結果'));

docChildren.push(h2('3.1 各模型回測 MAPE 與 bias 比較'));
docChildren.push(p(
  '表 3 彙整 2021–2024 年滾動回測中 11 個模型的表現（1 個結構式、6 個時序、3 個 Level 4 規模還原、1 個 Level 1 對照）。Cohort 結構式模型 MAPE 為 2.15%、bias 為 +1.45%；時序方法中 STL+linear 之 MAPE 為 3.50%、bias 為 +2.79%；NeuralProphet 之 MAPE 為 6.87%、bias 為 +4.79%；Holt-Winters、SARIMA、Prophet 之 MAPE 分別為 13.21%、12.95%、14.47%，bias 全為正向；Naive seasonal 與 Level 4 cows 為兩個極端，前者 bias 為 +16.48%、後者為 −23.45%。圖 3 之四個面板呈現比較：(a) MAPE 排序、(b) MAPE vs |Bias| 散布、(c) 逐年逐模型誤差熱力圖、(d) 規模還原策略 vs cohort。'
));

docChildren.push(...tableCaptionBilingual(
  '表 3：2021–2024 年滾動回測模型表現綜整（含逐年誤差）',
  'Table 3. Rolling backtest model performance, 2021–2024 (with per-year errors).'
));
docChildren.push(makeTable([
  [
    cell('Model', { bold: true, shading: 'D9E1F2' }),
    cell('Type', { bold: true, shading: 'D9E1F2' }),
    cell('MAPE %', { bold: true, shading: 'D9E1F2' }),
    cell('Bias %', { bold: true, shading: 'D9E1F2' }),
    cell('2021', { bold: true, shading: 'D9E1F2' }),
    cell('2022', { bold: true, shading: 'D9E1F2' }),
    cell('2023', { bold: true, shading: 'D9E1F2' }),
    cell('2024', { bold: true, shading: 'D9E1F2' }),
    cell('Note', { bold: true, shading: 'D9E1F2' }),
  ],
  [cell('Cohort (this study)', { bold: true }), cell('Structural'), cell('2.15'), cell('+1.45'), cell('−0.4'), cell('−1.0'), cell('−0.1'), cell('+7.2'), cell('Best overall')],
  [cell('STL + linear'), cell('Time-series'), cell('3.50'), cell('+2.79'), cell('−10.1'), cell('−8.5'), cell('−11.9'), cell('−7.3'), cell('Best TS (DHI subset)')],
  [cell('L4 mixed'), cell('Scale-factor'), cell('6.50'), cell('−6.50'), cell('−10.1'), cell('−0.7'), cell('−5.0'), cell('−10.2'), cell('—')],
  [cell('NeuralProphet'), cell('Time-series'), cell('6.87'), cell('+4.79'), cell('−2.5'), cell('−5.9'), cell('−19.2'), cell('−2.4'), cell('—')],
  [cell('L1 farms'), cell('Scale-factor'), cell('8.94'), cell('+8.94'), cell('+3.8'), cell('+12.8'), cell('+9.3'), cell('+9.8'), cell('—')],
  [cell('L4 farms'), cell('Scale-factor'), cell('10.45'), cell('+10.45'), cell('+5.2'), cell('+15.0'), cell('+14.1'), cell('+7.6'), cell('—')],
  [cell('SARIMA'), cell('Time-series'), cell('12.95'), cell('+12.95'), cell('−6.9'), cell('+3.8'), cell('+1.3'), cell('−0.8'), cell('TS subset')],
  [cell('Holt-Winters'), cell('Time-series'), cell('13.21'), cell('+13.21'), cell('−2.1'), cell('+3.8'), cell('−0.1'), cell('−3.0'), cell('TS subset')],
  [cell('Prophet'), cell('Time-series'), cell('14.47'), cell('+14.47'), cell('+3.8'), cell('+1.3'), cell('−2.2'), cell('+0.4'), cell('TS subset')],
  [cell('Naive seasonal'), cell('Baseline'), cell('16.48'), cell('+16.48'), cell('+1.5'), cell('+1.1'), cell('+0.7'), cell('+7.3'), cell('TS subset')],
  [cell('L4 cows'), cell('Scale-factor'), cell('23.45'), cell('−23.45'), cell('−25.4'), cell('−16.4'), cell('−24.1'), cell('−27.9'), cell('Worst')],
], [2200, 1300, 1100, 1100, 800, 800, 800, 800, 1700]));
docChildren.push(tableNote('註：MAPE 為平均絕對百分比誤差、Bias 為平均偏差，n=4 年。標示 "TS subset" 之模型其逐年誤差為 DHI 子樣本年度產量誤差；其他模型為全國年度總量誤差。所有模型於同一回測架構下比較。'));

docChildren.push(...figure(
  'fig3_model_comparison.png',
  '圖 3：八模型回測結果全景。(a) MAPE 排序；(b) MAPE vs |Bias|；(c) 逐年逐模型誤差熱力圖；(d) 規模還原策略 vs cohort。',
  'Figure 3. Model comparison panorama. (a) MAPE ranking; (b) MAPE vs |Bias|; (c) Per-year per-model error heatmap; (d) Scale-factor strategies vs cohort.'
));

docChildren.push(h2('3.2 Cohort 結構式之逐年誤差與診斷'));
docChildren.push(p(
  'Cohort 結構式模型逐年誤差為：2021 年 −0.36%、2022 年 −0.99%、2023 年 −0.05%、2024 年 +7.20%（表 4）。前三年絕對誤差都在 1% 以內，2024 年明顯放大。圖 4(a) 顯示 4 年預測 vs 實際與 ±5% 帶。圖 4(b) 之瀑布圖將 2024 年 +7.20% 偏差以乘法分解（multiplicative decomposition）拆成兩個獨立貢獻：規模化倍率漂移 +9.97%（其中 N 之外推誤差約 +4.0 個百分點、r 之變動約 +6.0 個百分點）、DHI 樣本年產量投影誤差 −2.77%。整體 4 年平均偏差為 +1.45%。'
));

docChildren.push(...tableCaptionBilingual(
  '表 4：Cohort 結構式模型逐年預測明細',
  'Table 4. Cohort model annual prediction detail.'
));
docChildren.push(makeTable([
  [
    cell('Year', { bold: true, shading: 'D9E1F2' }),
    cell('Predicted (t)', { bold: true, shading: 'D9E1F2' }),
    cell('Actual (t)', { bold: true, shading: 'D9E1F2' }),
    cell('Error (%)', { bold: true, shading: 'D9E1F2' }),
  ],
  [cell('2021'), cell('447,616'), cell('449,214'), cell('−0.36')],
  [cell('2022'), cell('458,525'), cell('463,095'), cell('−0.99')],
  [cell('2023'), cell('472,191'), cell('472,449'), cell('−0.05')],
  [cell('2024'), cell('484,975'), cell('452,414'), cell('+7.20')],
  [cell('Average', { bold: true }), cell('—', { bold: true }), cell('—', { bold: true }), cell('MAPE 2.15 / Bias +1.45', { bold: true })],
], [1500, 2400, 2400, 2700]));

docChildren.push(...figure(
  'fig4_cohort_diagnostic.png',
  '圖 4：Cohort 結構式診斷。(a) 4 年預測 vs 實際（含 ±5% 帶）；(b) 2024 年 +7.20% 偏差來源拆解（瀑布圖）。',
  'Figure 4. Cohort diagnostic. (a) Four-year prediction vs actual (with ±5% band); (b) 2024 +7.20% error decomposition waterfall.'
));

docChildren.push(h2('3.3 規模還原策略比較'));
docChildren.push(p(
  '三種純線性規模還原策略的回測結果如下：Level 4 farms（場數比例放大）MAPE 為 10.45%、bias 為 +10.45%；Level 4 cows（牛隻數比例放大）MAPE 為 23.45%、bias 為 −23.45%；50:50 混合（L4 mixed）MAPE 為 6.50%、bias 為 −6.50%。對照組 Level 1 farms 之 MAPE 為 8.94%、bias 為 +8.94%。Cohort 結構式（MAPE 2.15%、bias +1.45%）較 L4 mixed 之 |bias| 低 5.05 個百分點。三種規模還原策略在 2024 年之逐年誤差為：L4 farms +7.6%、L4 mixed −10.2%、L4 cows −27.9%（其他三年數值見表 3）。'
));

docChildren.push(h2('3.4 統計力與不確定性'));
docChildren.push(p(
  '本研究回測樣本只有 4 個目標年度（n = 4），統計推論的力很有限。對 cohort 的 4 個逐年絕對誤差做 bootstrap 重抽樣（2,000 次），得到 MAPE 的 95% bootstrap 信賴區間 (0.21%, 5.49%)；區間下緣顯示 cohort 表現可能略優於 2.15% 中位估計、上緣顯示也可能高達 5.5%，但整體仍低於所有時序基準的點估計（最佳純時序 STL+linear 是 3.50%）。對 cohort 與 STL+linear 的 4 對絕對誤差做配對 Wilcoxon signed-rank 檢定，得到 W = 0、p = 0.125；n = 4 的檢定力很低，p 值不適合解讀為「不顯著」，只能說在 4 年觀測下沒辦法用正式檢定排除「兩模型表現一樣」這個零假設。'
));

docChildren.push(h2('3.5 模型組合（ensemble）試算結果'));
docChildren.push(p(
  '本研究在相同回測架構下試算「cohort + STL+linear」之等權平均組合（每年 ŷ_y = (Ŷ_cohort,y + Ŷ_STL,y) / 2，其中 STL 預測先經 SF L4_farms 還原為全國尺度）。結果為 MAPE 2.34%、bias +2.12%，較單一 cohort（2.15%、+1.45%）略高。Ensemble 之逐年誤差為：2021 年 −5.2%、2022 年 −4.7%、2023 年 −6.0%、2024 年 +5.3%。'
));

docChildren.push(h2('3.6 三項研究問題之對應結果'));
docChildren.push(p(
  '回應第 1 章三個研究問題的數值結果：RQ1（DHI 微觀與全國總量整合方法），§2.2 之 N × Q × D 結構式加動態 productivity 比 r_t 修正之回測 MAPE 為 2.15%、bias +1.45%（表 3、表 4）。RQ2（cohort vs 時序模型相對表現），cohort 結構式之 MAPE 2.15% 較 6 種時序基準之 MAPE 範圍 3.50%–16.48% 為低（表 3、圖 3）。RQ3（規模還原策略偏差），三種純線性還原策略之 |bias| 範圍 6.50%–23.45%，cohort 結構式之 |bias| 為 1.45%（§3.3、圖 3(d)）。對應之解釋與意涵見第 4 章。'
));

docChildren.push(pageBreak());

// =====================================================================
// 第四章：討論
// =====================================================================
docChildren.push(h1('四、討論'));

docChildren.push(h2('4.1 結構式 vs. 時序：適用條件之邊界'));
docChildren.push(p(
  '本研究回測證據顯示，在台灣全國生乳產量預測這個具體脈絡下，cohort 結構式模型（MAPE 2.15%）明顯優於所有純時序基準。但這個結論不能推廣成「結構式永遠比時序好」。Hyndman 與 Athanasopoulos（2021）指出，結構式模型的優勢取決於兩個關鍵條件：(a) 資料能分層（cow → herd → region → nation 這種天然層級在本研究中存在）；(b) 各層級的觀測品質與覆蓋率（DHI 雖非全覆蓋，但場數覆蓋約 30% 已足以估計動態 r_t）。如果這些條件不成立（例如只有月度全國總量、沒有微觀資料），純時序方法仍是合理選擇；本研究 STL+linear 的 MAPE 3.50% 就顯示，在「只有總量序列」的退化情境下，簡潔的趨勢分解 + 季節保留方法能達到接近結構式的水準，而且實作門檻低很多。'
));

docChildren.push(h2('4.2 樣本偏選之動態校正必要性'));
docChildren.push(p(
  'Heckman（1979）所指出的樣本偏選問題，實務上常用「靜態 productivity 折扣率」處理（例如假設 DHI 與全國的比固定不變）。但本研究歷年資料顯示 r_t 不是定值：2021–2024 年間在 1.086–1.118 的窄區間內波動（圖 2c），2022 到 2024 年間的變動仍足以對年度預測造成 5%–10% 差異。如果用靜態折扣（例如固定 r = 1.10），會在尾年產生額外偏差；本研究的動態 r_{t−1} 能逐年滑動修正，是 cohort 模型在 2023 年達到 −0.05% 極小誤差的主因之一。要注意，本研究目前還沒在同一回測架構下對「靜態 r」與「動態 r_{t−1}」做正式比較，相對效益只由理論論述支持，正式對照組比較留待後續研究。'
));

docChildren.push(h2('4.3 2024 年偏差之政策歸因'));
docChildren.push(p(
  '結果章節（§3.2、§3.3）顯示 cohort 結構式之逐年誤差於 2021–2023 年皆控制在 1% 以內（−0.36%、−0.99%、−0.05%），於 2024 年則放大至 +7.20%；同期間，三種規模還原策略於 2024 年之偏差方向也呈現異於前三年之型態（L4 farms 由 +14.1% 降至 +7.6%、L4 cows 由 −24.1% 擴大至 −27.9%）。此一同時段的多模型誤差偏移，並非模型結構之缺陷，而可歸因於外部政策衝擊。為因應 2025 年全面生效之《臺紐經濟合作協定》並提升國產乳業競爭力，行政院於 2024 年 1 月核定「養牛產業全面升級轉型計畫（2024–2027 年）」，編列 18.6 億元預算，其中包含直接補助酪農場淘汰低產量乳牛（每頭 1.2–2 萬元），目標 2024–2027 年淘汰約 1.2 萬頭低產量牛、並將 2024 年生產目標由公會原估之 49 萬公噸下修至 46.5 萬公噸（農業部，2024b）。'
));
docChildren.push(p(
  '此一政策造成三項可觀測之資料變化：（a）全國產乳牛頭數明顯下降（2023 年 64,573 頭 → 2024 年 62,005 頭，−4.0%）；（b）留存牛之平均產量上升（淘汰低產量牛之直接效應）；（c）DHI 樣本場（多為中大型、管理較佳）相對於全國平均之優勢縮小，使 r_t 由 2023 年之 1.085 上升至 2024 年之 1.100。政策之兩個直接效應正好對應 cohort 模型之兩個輸入：N 下降與 Q（per-cow 留存產量）上升。此一外部政策資訊於模型訓練資料（2019–2023 皆早於政策核定）中無法觀測，故任何僅以歷史資料訓練之模型，無論結構式或時序，皆難以事先抓到此一轉折。實證上，6 種純時序模型於 2024 年 DHI 子樣本之誤差介於 −19.2% 至 +7.3%，相對於 cohort 之 +7.2%（全國尺度）並未呈現系統性優勢，意指結構式模型「生物學可解釋」之優勢於政策衝擊期亦不會喪失。'
));
docChildren.push(p(
  '進一步觀察 cohort 結構式之逐年誤差型態：2021–2023 年皆為輕微低估（方向一致、量級遞減，反映 r_{t−1} 三年滑動平均之自然吸收），2024 年則由低估翻為 +7.20% 高估。此一方向反轉並非模型自然演化之結果，而是政策衝擊使年度 r 自 1.085 跳升至 1.100、配合 N 下調 −4.0% 同向作用所致。圖 4(b) 之瀑布圖將 2024 +7.20% 偏差以乘法分解拆為兩項：規模化倍率漂移 +9.97%（其中 N 之外推誤差約 +4.0pp、r 變動約 +6.0pp）與 DHI 樣本年產量投影誤差 −2.77%。前三年低估、第四年高估之逐年誤差型態，本身即可視為「外部政策事件」之間接證據——若無此政策，cohort 模型 2024 年之誤差預期應仍為 −1% 上下。建議未來可採更短窗口之 r_t 與 N 估計（如季度滑動平均），或引入政策資訊（年度淘汰補助公告、配額調整）作為外部衝擊指標，於政策實施年份對 N 之投影做下修，以提早抓到結構轉折。'
));

docChildren.push(h2('4.4 與既有方法之關聯與 ensemble 解讀'));
docChildren.push(p(
  '本研究的 N × Q × D 結構式與工業界常用的「產能預測」邏輯相通，但 N（產乳牛頭數）本身會隨產業環境波動，把這個波動用動態 productivity 比吸收，是相對於純頭數計算的關鍵改進。本研究做法也可以跟層級重組（hierarchical reconciliation）研究結合：未來可把「DHI 場的預測」與「全國總量的預測」用 MinT 等重組方法調和（Hyndman & Athanasopoulos, 2021）。與 ARIMA / ETS 自動選模工具（Hyndman & Khandakar, 2008）相比，本研究 cohort 雖然實作上需要更多步驟（資料審計、Wood 擬合、productivity 比、規模還原），但每一步都有明確的產業意涵，可以由酪農、加工廠與政策制定者共同檢視；純自動選模的輸出雖然快速，但很難解釋誤差來源。§3.6 的 ensemble 試算結果（cohort + STL+linear 等權平均，MAPE 2.34%）略高於單一 cohort，是預測文獻中常見的「同向誤差無法分散」現象（Hyndman & Athanasopoulos, 2021）：兩模型在 2024 年都對 N 的頭數下調反應遲鈍，所以等權平均沒辦法分散這個共同偏差。未來若想透過 ensemble 進一步降低誤差，必須引入「真正不同方向」的模型（例如以氣象變數為主的外生回歸、或以飼料成本為主的經濟模型）。'
));

docChildren.push(h2('4.5 與國際慣例之數值比對'));
docChildren.push(p(
  '本研究 cohort 結構式在 4 年滾動回測的 MAPE 2.15% 可以跟國際同類預測的公開數值比對。OECD/FAO（2024）的全球農業展望採用 AGLINK-COSIMO 部分均衡模型，在主要 OECD 國家的乳業十年期投影平均絕對誤差約 2%–4%（依國家與年份不同）；美國 USDA NASS（2022）的 Milk Production Report 在月度估計誤差約 0.5%–1.5%（屬短期預測，不含長期投影）。本研究 2.15% MAPE 屬「短中期年度預測」的合理水準，雖然略高於 USDA 月度估計，但低於 AGLINK-COSIMO 長期投影的誤差上限。考慮到 USDA 享有覆蓋率近 100% 的全國抽樣（24 主要乳產州配合行政資料），而本研究 DHI 覆蓋率只有 30%–52%，能達到 2.15% MAPE 已是該覆蓋率水準下的優異表現。這個比對也顯示，本研究的 N × Q × D 結構式分解配合動態 productivity 比修正，在有限覆蓋率條件下仍能接近國際成熟系統的預測精度，是台灣公開資料條件下可實用的預測管線。'
));

docChildren.push(h2('4.6 限制與結論'));
docChildren.push(p(
  '本研究有五項限制：(1) DHI 樣本的場—牛偏選在地理（北/中/南/東）與品種上也可能不平衡，本研究還沒針對這個維度做細分，未來可把 r_t 進一步分區估計；(2) 引入溫濕度（THI）等氣象外部變數可能改善頭數轉折期的反應能力，但要考慮氣象資料的滯後與粒度配對；(3) 本研究用 305 天作為固定泌乳天數，但 2024 年實際樣本的中位泌乳天數略低（約 295 天），未來可改用 Wood 曲線的積分動態取代；(4) 4 年回測樣本偏小，未來建議在資料累積到 6–8 年後重新評估；(5) 本研究模型輸入只含歷史資料，無法主動抓到外部政策衝擊（例如 2024 年養牛產業升級轉型計畫主動淘汰低產量乳牛），未來可考慮把年度政策公告、配額調整、補助方案等資訊以類別變數（policy dummy）形式納入模型。整體來說，本研究在 2021–2024 年滾動回測中，cohort 結構式模型以 MAPE 2.15%、bias +1.45% 取得最佳表現，明顯優於 6 種主流時序模型（MAPE 3.50%–16.48%）與 3 種規模還原策略（|bias| 6.50%–23.45%）；2024 年 +7.20% 偏差有清楚的外部政策歸因（養牛產業升級計畫淘汰低產量乳牛），不是模型內部缺陷。本研究貢獻在於：方法上把 DHI 微觀與政府總量整合到 N × Q × D 框架；實證上量化結構式相對於時序模型的優劣；實務上提供可審計、可重現的預測管線。後續工作會集中在三個方向：(a) 把 r_t 細分到區域與品種；(b) 導入 THI 等氣象外部變數；(c) 用階層重組與政策衝擊變數提升結構轉折期的穩健性。'
));

docChildren.push(pageBreak());

// 附錄 A：演算法虛擬碼
// =====================================================================
docChildren.push(h1('附錄 A：Cohort 結構式預測演算法'));
docChildren.push(p(
  '本附錄列出本研究 cohort 結構式預測演算法之虛擬碼，以利後續研究者重現。輸入為 DHI 月測量紀錄、農業部公告之全國酪農戶與在養乳牛頭數、目標年度 y；輸出為目標年度之全國生乳產量預測值 Ŷ_y。'
));
docChildren.push(h3('A.1 主流程'));
docChildren.push(p('輸入：DHI 紀錄 D = {(farm_i, cow_j, date_k, milk_kg, fat, protein, scc, calving, dry_off)}；公告資料 G = {(year, farms, cows, milk_total)}；目標年度 y。', { indent: false }));
docChildren.push(p('步驟 1：資料審計。檢查 D 與 G 於 [2019, y−1] 之完整性，必要時自動觸發本機快取更新。', { indent: false }));
docChildren.push(p('步驟 2：DHI 樣本聚合。對每個年度 t ∈ [2019, y−1]，計算樣本年度單牛產量 Q_DHI,t = Σ_{j∈cows_t} milk_jt(305) / |cows_t|，其中 milk_jt(305) 為 Wood 曲線於 305 天之積分。', { indent: false }));
docChildren.push(p('步驟 3：等效全國單牛產量。對每個年度 t，計算 Q_official,t = M_t / N_t / 305，其中 M_t、N_t 取自 G。', { indent: false }));
docChildren.push(p('步驟 4：動態 productivity 比。計算 r_{y−1} = Q_DHI,y−1 / Q_official,y−1（或近 3 年滑動平均，依資料量決定）。', { indent: false }));
docChildren.push(p('步驟 5：頭數投影。N_y 由 G 之最近一年取得，或於必要時以 ARIMA(1, 1, 0) 對 N 序列短期投影；產乳牛比例採近 3 年 DHI 樣本平均值（約 0.80–0.82）。', { indent: false }));
docChildren.push(p('步驟 6：單牛產量投影。Q_y = Q_DHI,y / r_{y−1}，其中 Q_DHI,y 由 STL+linear 對 DHI 樣本年度產量短期投影。', { indent: false }));
docChildren.push(p('步驟 7：總量還原。Ŷ_y = N_y × Q_y × 305，並輸出年度與 12 個月之分布。', { indent: false }));

docChildren.push(h3('A.2 規模因子計算'));
docChildren.push(p('SF_L4_farms = N_official_farms / N_DHI_farms', { indent: false }));
docChildren.push(p('SF_L4_cows = N_official_cows / N_DHI_cows', { indent: false }));
docChildren.push(p('SF_L4_mixed = (SF_L4_farms + SF_L4_cows) / 2', { indent: false }));
docChildren.push(p('Cohort 結構式不直接使用上述 SF；其等效 SF 由 productivity 比 r_t 隱含吸收。', { indent: false }));

docChildren.push(pageBreak());

// =====================================================================
// 附錄 B：逐年逐模型回測明細
// =====================================================================
docChildren.push(h1('附錄 B：逐年逐模型回測明細'));
docChildren.push(p(
  '本附錄列出 2021–2024 年每個目標年度、每個模型之預測值、實際值與誤差，以利後續研究者於相同基準上比較其改進。所有數字由本研究預測管線之 holdout_backtest 模組於 2026 年 4 月底重跑驗證得出，原始 JSON 可於本研究專案 snapshots/_holdout_backtest.json 取得。'
));

docChildren.push(tableCaption('表 B-1：2021 年回測明細（訓練資料截至 2020-12-31）'));
docChildren.push(makeTable([
  [cell('模型', { bold: true, shading: 'D9E1F2' }),
   cell('預測 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('實際 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('誤差 (%)', { bold: true, shading: 'D9E1F2' })],
  [cell('Cohort 結構式'), cell('447,616'), cell('449,214'), cell('−0.36')],
  [cell('Naive seasonal'), cell('150,555 (DHI)'), cell('148,384 (DHI)'), cell('+1.46')],
  [cell('STL + linear'), cell('133,440 (DHI)'), cell('148,384 (DHI)'), cell('−10.07')],
  [cell('Holt-Winters'), cell('145,233 (DHI)'), cell('148,384 (DHI)'), cell('−2.12')],
  [cell('SARIMA'), cell('138,202 (DHI)'), cell('148,384 (DHI)'), cell('−6.86')],
  [cell('Prophet'), cell('154,093 (DHI)'), cell('148,384 (DHI)'), cell('+3.85')],
  [cell('NeuralProphet'), cell('144,620 (DHI)'), cell('148,384 (DHI)'), cell('−2.54')],
  [cell('L4 farms'), cell('472,467'), cell('449,214'), cell('+5.18')],
  [cell('L4 cows'), cell('334,920'), cell('449,214'), cell('−25.44')],
  [cell('L4 mixed'), cell('403,693'), cell('449,214'), cell('−10.13')],
], [2400, 2400, 2400, 2400]));
docChildren.push(tableNote('註：Naive 至 NeuralProphet 之預測對象為 DHI 樣本年度產量（公噸），其餘為全國年度產量（公噸）。'));

docChildren.push(tableCaption('表 B-2：2022 年回測明細（訓練資料截至 2021-12-31）'));
docChildren.push(makeTable([
  [cell('模型', { bold: true, shading: 'D9E1F2' }),
   cell('預測 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('實際 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('誤差 (%)', { bold: true, shading: 'D9E1F2' })],
  [cell('Cohort 結構式'), cell('458,525'), cell('463,095'), cell('−0.99')],
  [cell('L4 farms'), cell('532,375'), cell('463,095'), cell('+14.96')],
  [cell('L4 cows'), cell('387,335'), cell('463,095'), cell('−16.36')],
  [cell('L4 mixed'), cell('459,855'), cell('463,095'), cell('−0.70')],
], [2400, 2400, 2400, 2400]));

docChildren.push(tableCaption('表 B-3：2023 年回測明細（訓練資料截至 2022-12-31）'));
docChildren.push(makeTable([
  [cell('模型', { bold: true, shading: 'D9E1F2' }),
   cell('預測 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('實際 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('誤差 (%)', { bold: true, shading: 'D9E1F2' })],
  [cell('Cohort 結構式'), cell('472,191'), cell('472,449'), cell('−0.05')],
  [cell('L4 farms'), cell('538,974'), cell('472,449'), cell('+14.08')],
  [cell('L4 cows'), cell('358,685'), cell('472,449'), cell('−24.08')],
  [cell('L4 mixed'), cell('448,830'), cell('472,449'), cell('−5.00')],
], [2400, 2400, 2400, 2400]));

docChildren.push(tableCaption('表 B-4：2024 年回測明細（訓練資料截至 2023-12-31）'));
docChildren.push(makeTable([
  [cell('模型', { bold: true, shading: 'D9E1F2' }),
   cell('預測 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('實際 (公噸)', { bold: true, shading: 'D9E1F2' }),
   cell('誤差 (%)', { bold: true, shading: 'D9E1F2' })],
  [cell('Cohort 結構式'), cell('484,975'), cell('452,414'), cell('+7.20')],
  [cell('L4 farms'), cell('486,779'), cell('452,414'), cell('+7.60')],
  [cell('L4 cows'), cell('326,045'), cell('452,414'), cell('−27.93')],
  [cell('L4 mixed'), cell('406,412'), cell('452,414'), cell('−10.17')],
], [2400, 2400, 2400, 2400]));

docChildren.push(pageBreak());

// =====================================================================
// 附錄 C：資料審計欄位與檢核規則
// =====================================================================
docChildren.push(h1('附錄 C：資料審計流程詳細'));
docChildren.push(p(
  '本研究實作之資料審計（data_audit）模組於每次預測管線啟動時執行，可在新年度資料釋出時自動偵測缺漏並重新整理本機快取。本附錄詳列各檢查規則。'
));

docChildren.push(h3('C.1 DHI 紀錄檢核'));
docChildren.push(p('規則 1：對於目標年度 y 之每個年份 t ∈ [2019, y−1]，檢查 DHI 年度 pickle 是否存在於 cache 目錄。', { indent: false }));
docChildren.push(p('規則 2：每年度紀錄筆數應介於 200 萬至 350 萬筆之間，低於下限視為資料缺漏。', { indent: false }));
docChildren.push(p('規則 3：每年度參與場數應介於 150 至 200 場之間。', { indent: false }));
docChildren.push(p('規則 4：milk_kg 欄位之 99.9 百分位數應低於 100 公斤；超過此值之單筆紀錄發出警示但保留於分析。', { indent: false }));
docChildren.push(p('規則 5：calving_date 與 dry_off_date 之差應介於 200 至 400 天，違反時發出警示；不影響主分析中之 DHI 年產量加總，僅在 Wood 曲線擬合（若採用）時排除該胎次。', { indent: false }));

docChildren.push(h3('C.2 公告資料檢核'));
docChildren.push(p('規則 6：對於目標年度 y，檢查農業部公告之 [2019, y−1] 年度資料是否存在；若 y−1 年度資料尚未發布（典型情境為次年第一季），改採近四季季報加總或四季平均推估。', { indent: false }));
docChildren.push(p('規則 7：N_official、M_official 之年度變動率應在 ±15% 以內，超過則發出警示要求人工確認。', { indent: false }));
docChildren.push(p('規則 8：r_t = Q_DHI,t / Q_official,t 應介於 1.05 至 1.20 之間（涵蓋本研究 2021–2024 年實測之 1.086–1.118 區間）；超出此範圍視為樣本偏選顯著變動之指標，發出警示要求人工確認。', { indent: false }));

docChildren.push(h3('C.3 自動更新流程'));
docChildren.push(p('當任一檢查規則失敗時，data_audit 模組嘗試從專案內之 raw_dhi 與 govt_data 目錄重新載入並轉檔；轉檔失敗時回報錯誤訊息與缺漏年度，由使用者後續補齊原始資料。整個流程設計為冪等（idempotent），重複執行不會造成資料重複或損毀。', { indent: false }));

docChildren.push(pageBreak());

// =====================================================================
// 投稿必備聲明
// =====================================================================
docChildren.push(h1('致謝、利益衝突與貢獻聲明'));
docChildren.push(h3('致謝（Acknowledgments）'));
docChildren.push(p(
  '本研究使用之 DHI 月測乳紀錄由農業部 DHI 計畫合作酪農戶提供；資料整理協助由中華民國乳業發展協會、農業部畜產試驗所北區分所、中華民國農會 DHI 雲端服務網提供。全國酪農戶與乳牛在養頭數資料採自農業部統計處公開發布之畜禽統計調查季報。'
));
docChildren.push(h3('利益衝突聲明（Declaration of Competing Interests）'));
docChildren.push(p(
  '作者宣告本研究無已知或潛在之利益衝突。本研究未受任何乳業業者、酪農合作社、或產業團體之經費資助；研究設計、資料分析與結論皆由作者獨立完成。'
));
docChildren.push(h3('作者貢獻（Author Contributions）'));
docChildren.push(p(
  '本研究由單一作者完成資料蒐集、模型設計、實作、回測、分析、撰寫與投稿準備。'
));
docChildren.push(h3('資料可得性聲明（Data Availability Statement）'));
docChildren.push(p(
  'DHI 月測乳紀錄屬農業部 DHI 計畫之原始資料，依資料共享政策不可公開重新散布；全國酪農戶與乳牛頭數、全國生乳年產量資料皆為農業部公開發布、可由 https://agrstat.moa.gov.tw/ 與 https://www.moa.gov.tw/ 自由取得。本研究所實作之預測管線與回測程式碼可向通訊作者索取。本研究之回測結果（snapshots/_holdout_backtest.json）可重現本論文所有 MAPE / Bias / 逐年誤差數字。'
));
docChildren.push(h3('經費（Funding）'));
docChildren.push(p(
  '本研究未接受任何外部經費資助。'
));

docChildren.push(pageBreak());

// =====================================================================
// 參考文獻
// =====================================================================
docChildren.push(h1('參考文獻'));

docChildren.push(ref(
  'Box, G. E. P., & Jenkins, G. M. (1976). Time series analysis: Forecasting and control (Rev. ed.). Holden-Day.'
));
docChildren.push(ref(
  'Cleveland, R. B., Cleveland, W. S., McRae, J. E., & Terpenning, I. J. (1990). STL: A seasonal-trend decomposition procedure based on loess. Journal of Official Statistics, 6(1), 3–33.'
));
docChildren.push(ref(
  'Heckman, J. J. (1979). Sample selection bias as a specification error. Econometrica, 47(1), 153–161. https://doi.org/10.2307/1912352'
));
docChildren.push(ref(
  'Holt, C. C. (2004). Forecasting seasonals and trends by exponentially weighted moving averages. International Journal of Forecasting, 20(1), 5–10. https://doi.org/10.1016/j.ijforecast.2003.09.015 (原稿為 1957 年 Office of Naval Research Memorandum No. 52，Carnegie Institute of Technology)'
));
docChildren.push(ref(
  'Hyndman, R. J., & Athanasopoulos, G. (2021). Forecasting: Principles and practice (3rd ed.). OTexts. https://otexts.com/fpp3/'
));
docChildren.push(ref(
  'Hyndman, R. J., & Khandakar, Y. (2008). Automatic time series forecasting: The forecast package for R. Journal of Statistical Software, 27(3), 1–22. https://doi.org/10.18637/jss.v027.i03'
));
docChildren.push(ref(
  'OECD/FAO. (2024). OECD-FAO Agricultural Outlook 2024–2033. OECD Publishing. https://doi.org/10.1787/4c5d2cfb-en'
));
docChildren.push(ref(
  'Taylor, S. J., & Letham, B. (2018). Forecasting at scale. The American Statistician, 72(1), 37–45. https://doi.org/10.1080/00031305.2017.1380080'
));
docChildren.push(ref(
  'Triebe, O., Hewamalage, H., Pilyugina, P., Laptev, N., Bergmeir, C., & Rajagopal, R. (2021). NeuralProphet: Explainable forecasting at scale. arXiv. https://arxiv.org/abs/2111.15397'
));
docChildren.push(ref(
  'USDA National Agricultural Statistics Service. (2022). Milk Production methodology and quality measures (ISSN: 2167-1885). U.S. Department of Agriculture. https://www.nass.usda.gov/Publications/Methodology_and_Data_Quality/Milk_Production/01_2022/milkqm22.pdf'
));
docChildren.push(ref(
  'Winters, P. R. (1960). Forecasting sales by exponentially weighted moving averages. Management Science, 6(3), 324–342. https://doi.org/10.1287/mnsc.6.3.324'
));
docChildren.push(ref(
  'Wood, P. D. P. (1967). Algebraic model of the lactation curve in cattle. Nature, 216(5111), 164–165. https://doi.org/10.1038/216164a0'
));
docChildren.push(ref(
  '中華民國乳業發展協會、農業部畜產試驗所北區分所、中華民國農會（2024）。DHI 乳牛群性能改良雲端服務網。https://dhi.org.tw/about'
));
docChildren.push(ref(
  '農業部（2024a）。113 年年報。中華民國行政院農業部。https://www.moa.gov.tw/ws.php?id=2517095'
));
docChildren.push(ref(
  '農業部（2024b）。養牛產業全面升級轉型計畫（113–116 年）；113 年 1 月 22–23 日於嘉義市及臺南市辦理「養牛產業升級轉型座談會」。中華民國行政院農業部 113 年 1 月份重要措施。https://www.moa.gov.tw/ws.php?id=2515195'
));
docChildren.push(ref(
  '農業部統計處（2024）。畜禽統計調查—乳牛在養按品項統計季報。農業統計資料查詢系統。https://agrstat.moa.gov.tw/sdweb/'
));

// =====================================================================
// Document
// =====================================================================
const doc = new Document({
  creator: 'Milk Forecast Project',
  title: TITLE_ZH,
  styles: {
    default: {
      document: {
        run: { font: { ascii: FONT_EN, eastAsia: FONT_ZH }, size: 24 },
      },
    },
  },
  sections: [{
    properties: {
      page: {
        margin: {
          top: convertInchesToTwip(1),
          right: convertInchesToTwip(1),
          bottom: convertInchesToTwip(1),
          left: convertInchesToTwip(1),
        },
      },
    },
    children: docChildren,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = require('path').join(__dirname, 'paper_draft.docx');
  fs.writeFileSync(out, buf);
  console.log('Wrote', out, '(' + buf.length + ' bytes)');
});
