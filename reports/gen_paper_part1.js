// 生成論文第一部分：摘要 + 緒論 + 文獻回顧 + 參考文獻
const { Document, Packer, Paragraph, TextRun, AlignmentType, HeadingLevel,
        LevelFormat, ShadingType, BorderStyle, PageOrientation,
        TabStopType, TabStopPosition, PageNumber, Header, Footer,
        Table, TableRow, TableCell, WidthType, VerticalAlign } = require('docx');
const fs = require('fs');

const TITLE_ZH = "以測乳紀錄與在養量資料整合預測全國牛乳產量：結構式與時序模型之比較研究";
const TITLE_EN = "Integrating DHI Records and Livestock Inventory Data for National Milk Production Forecasting: A Comparative Study of Structural and Time-Series Models";

const FONT_ZH = "Microsoft JhengHei";
const FONT_EN = "Times New Roman";

function p(text, opts = {}) {
  const {bold = false, italic = false, size = 24, align = AlignmentType.JUSTIFIED,
         indent = true, spacing = {before: 0, after: 120, line: 360}} = opts;
  return new Paragraph({
    alignment: align,
    spacing,
    indent: indent ? {firstLine: 480} : undefined,
    children: [new TextRun({text, bold, italics: italic, size,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: {before: 360, after: 180},
    alignment: AlignmentType.LEFT,
    children: [new TextRun({text, bold: true, size: 32,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: {before: 240, after: 120},
    alignment: AlignmentType.LEFT,
    children: [new TextRun({text, bold: true, size: 28,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  });
}

function center(text, opts = {}) {
  const {bold = false, size = 28} = opts;
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: {after: 120, line: 360},
    children: [new TextRun({text, bold, size,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  });
}

// 參考文獻段落（hanging indent）
function ref(text) {
  return new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: {after: 100, line: 320},
    indent: {left: 540, hanging: 540},
    children: [new TextRun({text, size: 22,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  });
}

const children = [
  // ========== 標題 ==========
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: {before: 240, after: 240, line: 420},
    children: [new TextRun({text: TITLE_ZH, bold: true, size: 36,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: {after: 240, line: 320},
    children: [new TextRun({text: TITLE_EN, bold: true, size: 24, italics: true,
                              font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
  }),

  center("（作者姓名待填）  （服務單位待填）", {size: 22}),
  center("(Author Name TBD)  (Affiliation TBD)", {size: 20}),
  new Paragraph({spacing: {after: 240}, children: [new TextRun("")]}),

  // ========== 中文摘要 ==========
  h1("摘要"),
  p("本研究以 2000-2024 年台灣酪牛性能改良 (Dairy Herd Improvement, DHI) 月度測乳紀錄共 5,168,864 筆與農業部公告之全國在養量年報、季報為基礎，建構整合多源異質資料之全國牛乳產量預測架構，並系統性比較結構式與時序式預測模型於滾動回測 (rolling holdout backtest) 下之預測精度。本研究方法學貢獻有二：(1) 提出「Cohort 結構式 + 動態 productivity 比率校正」之預測流程，將 DHI 樣本之單頭日產量校正至全國平均水準，藉以解決 DHI 樣本場生產力高於全國平均之系統性偏差；(2) 以 4 年 (2021-2024) 滾動 holdout 設計，量化驗證 7 種預測模型之精度與系統性偏差。實證結果顯示：本研究提出之動態校正後 Cohort 模型之平均絕對誤差百分比 (MAPE) 為 2.15%、系統性偏差為 +1.45%，顯著優於 5 種純時序模型 (MAPE 介於 3.50%-16.48%) 與多模型集成 (MAPE 10.50%)，且校正方法將 Cohort 原始 MAPE 由 9.85% 降至 2.15% (改善 7.70 個百分點)。本研究貢獻在於提出可解釋、可驗證、可複製之結構式預測方法，並以實證資料說明結構式模型在牛乳產量預測上勝過純時序模型之三層原因：物理可解釋性、變數穩定性、誤差抵消機制。"),

  p("關鍵字：牛乳產量預測、測乳紀錄 (DHI)、結構式模型、時間序列、滾動回測、productivity 比率校正", {indent: false}),

  // ========== 英文摘要 ==========
  h1("Abstract"),
  p("This study constructs a national milk production forecasting framework integrating heterogeneous multi-source data: 5,168,864 monthly Dairy Herd Improvement (DHI) records (2000-2024) and Council of Agriculture (Taiwan) annual production statistics and quarterly inventory reports. We systematically compare structural and time-series forecasting models under rolling holdout backtesting. The methodological contributions are twofold: (1) we propose a Cohort-based structural model with dynamic productivity ratio correction, which calibrates DHI sample per-cow yield to national average to address the systematic positive bias arising from higher productivity in DHI sample farms; and (2) we empirically validate 7 forecasting models on 4 years (2021-2024) of holdout data. Results show that the proposed dynamically-corrected Cohort model achieves a Mean Absolute Percentage Error (MAPE) of 2.15% and systematic bias of +1.45%, significantly outperforming 5 pure time-series models (MAPE ranging 3.50%-16.48%) and a weighted ensemble (MAPE 10.50%). The proposed correction reduces the raw Cohort MAPE from 9.85% to 2.15%, a 7.70 percentage point improvement. The study contributes an interpretable, verifiable, and reproducible structural forecasting methodology, and empirically demonstrates three reasons why structural models outperform pure time-series models in milk production forecasting: physical interpretability, variable stability, and error-cancellation mechanisms.", {indent: false}),

  p("Keywords: milk production forecasting, Dairy Herd Improvement (DHI), structural model, time-series, rolling backtest, productivity ratio correction", {indent: false}),

  new Paragraph({spacing: {after: 360}, children: [new TextRun("")]}),

  // ========== 一、緒論 ==========
  h1("一、緒論"),

  h2("1.1 研究背景與動機"),
  p("台灣酪農業為農業重要產業之一，依據農業部公告，全國產乳牛口數約 59,259 頭、乳牛場 545 場、年產乳量 452,414 公噸（行政院農業部，2024）。然而，酪農業近年面臨多重結構性挑戰：進口乳製品市場份額持續擴大、酪農年齡結構老化、飼料原物料成本波動加劇、極端氣候對乳牛生產力之衝擊頻繁，以致國產乳量於 2023 年公告值 472,449 公噸達到歷史相對高峰後，2024 年遽降至 452,414 公噸（年減 4.2%），引發產業界對未來供需平衡之高度關注。"),

  p("為支援主管機關進行產業政策規劃、進口配額調整、補貼方案評估，建立可信賴之全國牛乳產量預測系統具有迫切之政策意義。然而，預測台灣全國牛乳產量面臨以下方法論挑戰：第一，DHI 月度測乳紀錄係以樣本場為基礎之高頻資料（International Committee for Animal Recording [ICAR], 2017），具備細粒度乳量資訊，但僅涵蓋全國約 30-40% 之乳牛場，存在涵蓋率還原（scale factor restoration）之需求；第二，農業部公告之年度與季度在養量資料具有母體代表性，但時間粒度較粗，無法直接用於月度預測；第三，DHI 樣本場之單頭乳牛產量歷年觀察均高於全國平均 6%-12%，反映樣本場具有較高生產力之選擇性偏差（selection bias；Heckman, 1979），若直接以樣本資料外推全國產量，會產生系統性高估。"),

  p("既有國內研究多以單一資料源（如 DHI 樣本或官方公告值）進行單變量時間序列預測，未能充分利用多源異質資料之互補性，亦未針對 DHI 樣本之選擇性偏差設計動態校正機制。本研究旨在補足此一方法論缺口，提出整合 DHI 月度資料、官方在養量資料、與動態 productivity 校正之預測架構，並以滾動 holdout backtest 驗證其精度（Hyndman & Athanasopoulos, 2021）。"),

  h2("1.2 預測方法的兩大途徑"),
  p("乳量預測之既有方法可大致分為兩類。第一類為「純時間序列途徑」（time-series approach），以歷史月度乳量序列為唯一輸入，運用統計或機器學習方法外推未來。常見方法包括 ARIMA（Box & Jenkins, 1976）、季節性自迴歸整合移動平均（SARIMA；Hyndman & Khandakar, 2008）、指數平滑（Holt, 1957；Winters, 1960）、季節趨勢分解（Cleveland et al., 1990）、以及近年廣被應用之 Facebook Prophet（Taylor & Letham, 2018）與 NeuralProphet（Triebe et al., 2021）等。此類方法之優點在於模型純粹、不依賴外生變數；缺點在於模型本質為趨勢外推，對結構性轉折之預警能力有限，且無法解釋「為何產量是此一水準」之因果機制（Pesaran & Timmermann, 2007）。"),

  p("第二類為「結構式途徑」（structural approach），依產業物理關係將產量分解為若干可觀測組成變數之乘積，最常見之公式為 Y = N × Q × D，其中 Y 為年產量、N 為產乳牛口數、Q 為單頭日產乳量、D 為標準泌乳期長度（305 天；Wood, 1967）。結構式模型之優點在於每一組成變數均具明確物理意義，外推時可分別追蹤每一變數之歷史軌跡（Murphy et al., 2014）；缺點則在於：若單頭產量資訊來自具有選擇性偏差之 DHI 樣本，將直接傳遞偏差至全國產量估計（Heckman, 1979）。"),

  h2("1.3 研究問題與貢獻"),
  p("本研究欲回答之核心研究問題為：「在預測全國牛乳產量時，整合 DHI 紀錄與在養量資料之結構式模型（含動態 productivity 比率校正）是否顯著優於純時序模型？」基於此問題，本研究之主要貢獻包括："),

  p("第一，方法學貢獻：提出「Cohort 結構式 + 動態 productivity 比率校正」之預測流程。此校正方法以歷年「DHI 樣本單頭日產量 / 全國平均單頭日產量」之比率為依據，線性外推至目標年並反向校正 Cohort 預測值，藉此將 DHI 樣本之選擇性偏差由系統性扣除。", {indent: true}),

  p("第二，實證貢獻：以 2021-2024 年 4 年滾動 holdout 設計，系統性比較 7 種預測模型（5 種純時序模型、Cohort 結構式模型、加權集成模型）之預測精度。實證結果顯示動態校正後之 Cohort 模型在所有評估指標上均優於其他模型。", {indent: true}),

  p("第三，方法論討論貢獻：本文以「物理可解釋性、變數穩定性、誤差抵消機制」三層次論證結構式模型在乳量預測上勝過時序模型之原因，並指出結構式方法之主要限制為對結構性轉折之敏感度不足。", {indent: true}),

  h2("1.4 論文架構"),
  p("本論文後續章節安排如下：第二節回顧結構式與時序式預測之相關文獻；第三節說明本研究使用之 DHI 紀錄、農業部公告值、在養量季報三大資料來源；第四節詳述 7 種預測模型之數學形式、Level 4 涵蓋率還原方法、以及本文核心方法貢獻——動態 productivity 比率校正；第五節報告 4 年滾動回測之精度比較結果；第六節討論結構式模型勝出之原因與本研究之限制；第七節為結論與政策意涵。"),

  new Paragraph({spacing: {after: 240}, pageBreakBefore: true, children: [new TextRun("")]}),

  // ========== 二、文獻回顧 ==========
  h1("二、文獻回顧"),

  h2("2.1 時間序列預測模型"),
  p("Box and Jenkins（1976）提出之自迴歸整合移動平均模型（ARIMA）為時間序列預測之經典方法，其延伸 SARIMA 加入季節項，至今仍廣泛用於月度與季度資料預測。Hyndman and Khandakar（2008）提出 auto.arima 演算法自動選定 ARIMA 階數，使非統計專業使用者亦能應用。指數平滑類方法以 Holt（1957）與 Winters（1960）之 Holt-Winters 三重指數平滑為代表，具備趨勢與季節成分之分離追蹤能力。Cleveland et al.（1990）提出 STL（Seasonal-Trend decomposition using Loess）分解，將序列拆解為趨勢、季節、殘差三部分，便於外推。"),

  p("近年機器學習導向之時序模型亦廣為流行。Taylor and Letham（2018）提出 Facebook Prophet 模型，整合分段線性趨勢、傅立葉級數季節項、與假日效應，於商業預測場景具高度可用性。Triebe et al.（2021）進一步提出 NeuralProphet，以 PyTorch 實作 Prophet 之神經網路版本，加入 AR-Net 自迴歸模組，於部分場景優於原始 Prophet。Hyndman and Athanasopoulos（2021）之《Forecasting: Principles and Practice》系統性整理各類時序模型之原理與適用情境，為本研究模型選擇之主要參考。"),

  h2("2.2 牛乳產量結構式模型"),
  p("結構式產量模型之根源可追溯至 Wood（1967）提出之乳期曲線函式，以三參數方程式描述單頭乳牛日產量隨泌乳天數之變化。後續研究將此曲線整合至全場與全國尺度之產量預測。Murphy et al.（2014）以愛爾蘭酪農場為例，建構含牛口、產量、季節之多層結構模型，預測全場月度乳固形物產量；其方法核心在於分別追蹤牛口動態與單頭產量趨勢，再以乘法整合。本研究 Cohort 模型之公式架構（Y = N × Q × D）與此一脈相承。"),

  h2("2.3 樣本偏差與校正"),
  p("DHI 樣本場相對於全國乳牛場具有選擇性偏差，原因包括：自願參與 DHI 計畫之農場通常規模較大、管理較先進、生產力較高（International Committee for Animal Recording [ICAR], 2017）。此一現象為樣本選擇偏差（sample selection bias）之典型案例，Heckman（1979）將其形式化為樣本選擇模型，並提出兩階段修正法。本研究採用較簡化之動態 productivity 比率校正——即以歷年「DHI 樣本單頭產量 / 全國平均單頭產量」之比值反向校正樣本估計，雖未採用 Heckman 兩階段方法，但在資料受限（缺乏個別農場決策變數）之實務情境下，提供可行之偏差扣除途徑。"),

  h2("2.4 預測模型驗證方法"),
  p("時間序列預測之驗證需考慮資料時序性，不可隨機切分訓練 / 測試集。Hyndman and Athanasopoulos（2021）建議使用「rolling-origin cross-validation」（滾動原點交叉驗證）或「expanding window backtest」（擴展窗回測），即以訓練資料截至某時點，預測下一時段，再依序滾動。本研究採用 leave-one-year-out 之變體：對 2021-2024 每年進行 holdout，模型僅看到 ≤ Y-1 之資料，預測年 Y 並對照農業部公告真值，以此計算各模型在 4 年上之 MAPE 與系統性偏差（bias）。Pesaran and Timmermann（2007）指出，於存在結構性轉折之資料上，rolling-origin 設計優於固定切分，因其能評估模型對「最近資料」之適應能力。"),

  h2("2.5 既有方法之限制與本研究定位"),
  p("綜合上述文獻，既有方法存在以下限制：(1) 純時序模型雖可在無外生變數下運作，但無法區分「結構性下跌」與「短期波動」，於 2024 年此類突發轉折下之預測穩定性不足（Pesaran & Timmermann, 2007）；(2) 既有結構式模型多以單一資料源為基礎，未充分整合 DHI 樣本與官方公告之互補資訊；(3) 樣本偏差校正多採事後 bias correction（即由歷史誤差平均扣除），未針對偏差比率之歷史趨勢進行動態建模。本研究提出之動態 productivity 比率校正，補足上述第三項之限制，並以實證資料驗證其在乳量預測上之效益。"),

  new Paragraph({spacing: {after: 240}, pageBreakBefore: true, children: [new TextRun("")]}),

  // ========== 參考文獻（暫列） ==========
  h1("參考文獻"),

  p("中文文獻（依姓名筆劃排序）", {indent: false, bold: true, size: 26, spacing: {before: 180, after: 120}}),
  ref("行政院農業部 (2024). 113 年農業統計年報. 台北：行政院農業部."),
  ref("行政院農業部 (2024). 牛乳產量資料 (1967-2024). [畜牧生產及貿易資料]. 取自農業統計資料查詢系統."),
  ref("行政院農業部 (2025). 113 年第 1-3 季在養量比較. 台北：行政院農業部畜牧統計調查."),

  p("英文文獻（依字母順序排序）", {indent: false, bold: true, size: 26, spacing: {before: 240, after: 120}}),
  ref("Box, G. E. P., & Jenkins, G. M. (1976). Time Series Analysis: Forecasting and Control (Revised ed.). San Francisco: Holden-Day."),
  ref("Cleveland, R. B., Cleveland, W. S., McRae, J. E., & Terpenning, I. (1990). STL: A seasonal-trend decomposition procedure based on Loess. Journal of Official Statistics, 6(1), 3-73."),
  ref("Heckman, J. J. (1979). Sample selection bias as a specification error. Econometrica, 47(1), 153-161."),
  ref("Holt, C. C. (1957). Forecasting seasonals and trends by exponentially weighted moving averages. ONR Research Memorandum 52, Carnegie Institute of Technology."),
  ref("Hyndman, R. J., & Athanasopoulos, G. (2021). Forecasting: Principles and Practice (3rd ed.). Melbourne: OTexts. https://otexts.com/fpp3/"),
  ref("Hyndman, R. J., & Khandakar, Y. (2008). Automatic time series forecasting: The forecast package for R. Journal of Statistical Software, 27(3), 1-22."),
  ref("International Committee for Animal Recording [ICAR]. (2017). ICAR recording guidelines for milk performance recording in dairy cattle. Rome: ICAR."),
  ref("Murphy, M. D., O'Mahony, M. J., Shalloo, L., French, P., & Upton, J. (2014). Comparison of modelling techniques for milk-production forecasting. Journal of Dairy Science, 97(6), 3352-3363."),
  ref("Pesaran, M. H., & Timmermann, A. (2007). Selection of estimation window in the presence of breaks. Journal of Econometrics, 137(1), 134-161."),
  ref("Taylor, S. J., & Letham, B. (2018). Forecasting at scale. The American Statistician, 72(1), 37-45."),
  ref("Triebe, O., Hewamalage, H., Pilyugina, P., Laptev, N., Bergmeir, C., & Rajagopal, R. (2021). NeuralProphet: Explainable forecasting at scale. arXiv preprint arXiv:2111.15397."),
  ref("Winters, P. R. (1960). Forecasting sales by exponentially weighted moving averages. Management Science, 6(3), 324-342."),
  ref("Wood, P. D. P. (1967). Algebraic model of the lactation curve in cattle. Nature, 216(5111), 164-165."),

  p("（後續章節「資料」、「方法」、「結果」、「討論」、「結論」之文獻將於對應段落引述後補入。）", {indent: false, italic: true, size: 22}),
];

const doc = new Document({
  styles: {
    default: { document: { run: { font: { ascii: FONT_EN, eastAsia: FONT_ZH }, size: 24 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: { ascii: FONT_EN, eastAsia: FONT_ZH } },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: { ascii: FONT_EN, eastAsia: FONT_ZH } },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [new TextRun({text: "中國畜牧學會誌（投稿草稿）", size: 18, italics: true,
                                  font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
      })]}),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({text: "第 ", size: 18, font: { ascii: FONT_EN, eastAsia: FONT_ZH }}),
                    new TextRun({children: [PageNumber.CURRENT], size: 18}),
                    new TextRun({text: " 頁 / 共 ", size: 18, font: { ascii: FONT_EN, eastAsia: FONT_ZH }}),
                    new TextRun({children: [PageNumber.TOTAL_PAGES], size: 18}),
                    new TextRun({text: " 頁", size: 18, font: { ascii: FONT_EN, eastAsia: FONT_ZH }})],
      })]}),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/sessions/quirky-gracious-cray/mnt/Milk_forecast/reports/paper_part1_draft.docx', buf);
  console.log('OK: written paper_part1_draft.docx (' + buf.length + ' bytes)');
});
