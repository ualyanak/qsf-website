(function (root) {
  "use strict";

  const A4 = [595.28, 841.89];
  const palette = {
    navy: [0.039, 0.114, 0.192],
    navy2: [0.082, 0.204, 0.310],
    gold: [0.788, 0.635, 0.310],
    goldLight: [0.969, 0.941, 0.855],
    ink: [0.090, 0.141, 0.200],
    muted: [0.380, 0.439, 0.502],
    line: [0.855, 0.886, 0.910],
    canvas: [0.953, 0.965, 0.973],
    green: [0.153, 0.463, 0.353],
    red: [0.643, 0.247, 0.263],
    white: [1, 1, 1]
  };

  function c(PDFLib, value) {
    return PDFLib.rgb(value[0], value[1], value[2]);
  }

  function safe(value, limit) {
    return String(value == null ? "" : value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, limit || 240);
  }

  function money(value, currency) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(number);
  }

  function percent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return (number >= 0 ? "+" : "") + number.toFixed(2) + "%";
  }

  function number(value, digits) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits == null ? 4 : digits }).format(numeric);
  }

  function dateText(value, withTime) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    return new Intl.DateTimeFormat("en-US", withTime
      ? { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }
      : { month: "short", day: "numeric", year: "numeric" }).format(date);
  }

  function textWidth(font, text, size) {
    return font.widthOfTextAtSize(String(text), size);
  }

  function truncate(font, text, size, maxWidth) {
    const value = safe(text, 500);
    if (textWidth(font, value, size) <= maxWidth) return value;
    let result = value;
    while (result.length > 1 && textWidth(font, result + "...", size) > maxWidth) result = result.slice(0, -1);
    return result + "...";
  }

  function wrap(font, text, size, maxWidth, maxLines) {
    const words = safe(text, 2000).split(" ");
    const lines = [];
    let line = "";
    words.forEach(function (word) {
      const candidate = line ? line + " " + word : word;
      if (textWidth(font, candidate, size) <= maxWidth) {
        line = candidate;
      } else {
        if (line) lines.push(line);
        line = word;
      }
    });
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      const kept = lines.slice(0, maxLines);
      kept[maxLines - 1] = truncate(font, kept[maxLines - 1] + " " + lines.slice(maxLines).join(" "), size, maxWidth);
      return kept;
    }
    return lines;
  }

  function drawWrapped(page, font, text, options) {
    const lines = wrap(font, text, options.size, options.maxWidth, options.maxLines || 10);
    lines.forEach(function (line, index) {
      page.drawText(line, {
        x: options.x,
        y: options.y - index * (options.lineHeight || options.size * 1.35),
        size: options.size,
        font: font,
        color: options.color
      });
    });
    return lines.length;
  }

  function drawWatermark(page, PDFLib, fontBold) {
    page.drawText("PUBLIC DEMONSTRATION", {
      x: 68,
      y: 390,
      size: 40,
      font: fontBold,
      color: c(PDFLib, palette.navy),
      rotate: PDFLib.degrees(34),
      opacity: 0.055
    });
    page.drawText("NOT AN ACCOUNT STATEMENT", {
      x: 112,
      y: 350,
      size: 21,
      font: fontBold,
      color: c(PDFLib, palette.gold),
      rotate: PDFLib.degrees(34),
      opacity: 0.075
    });
  }

  function drawFooter(page, PDFLib, fonts, pageNumber, totalPages) {
    const width = page.getWidth();
    page.drawLine({
      start: { x: 38, y: 42 },
      end: { x: width - 38, y: 42 },
      thickness: 0.7,
      color: c(PDFLib, palette.line)
    });
    page.drawText("Quantum Strategy Fund · Public demonstration · Test data only", {
      x: 38,
      y: 25,
      size: 7.2,
      font: fonts.regular,
      color: c(PDFLib, palette.muted)
    });
    const label = "Page " + pageNumber + " of " + totalPages;
    page.drawText(label, {
      x: width - 38 - textWidth(fonts.regular, label, 7.2),
      y: 25,
      size: 7.2,
      font: fonts.regular,
      color: c(PDFLib, palette.muted)
    });
  }

  function drawTopRule(page, PDFLib, fonts, title, subtitle) {
    const width = page.getWidth();
    page.drawRectangle({ x: 0, y: 772, width: width, height: 70, color: c(PDFLib, palette.navy) });
    page.drawRectangle({ x: 0, y: 768, width: width, height: 4, color: c(PDFLib, palette.gold) });
    page.drawText(title, { x: 38, y: 801, size: 15, font: fonts.bold, color: c(PDFLib, palette.white) });
    page.drawText(subtitle, { x: 38, y: 784, size: 8.2, font: fonts.regular, color: c(PDFLib, [0.78, 0.84, 0.88]) });
  }

  function drawMetric(page, PDFLib, fonts, x, y, width, label, value, accent) {
    page.drawRectangle({
      x: x,
      y: y,
      width: width,
      height: 70,
      borderWidth: 0.8,
      borderColor: c(PDFLib, palette.line),
      color: c(PDFLib, palette.white)
    });
    page.drawRectangle({ x: x, y: y + 67, width: width, height: 3, color: c(PDFLib, accent || palette.gold) });
    page.drawText(safe(label, 40).toUpperCase(), {
      x: x + 12,
      y: y + 47,
      size: 7.2,
      font: fonts.bold,
      color: c(PDFLib, palette.muted)
    });
    page.drawText(truncate(fonts.serifBold, value, 16, width - 24), {
      x: x + 12,
      y: y + 19,
      size: 16,
      font: fonts.serifBold,
      color: c(PDFLib, accent || palette.navy)
    });
  }

  function drawAllocation(page, PDFLib, fonts, allocation, x, y, width) {
    page.drawText("ILLUSTRATIVE GROSS EXPOSURE", {
      x: x,
      y: y,
      size: 8,
      font: fonts.bold,
      color: c(PDFLib, palette.muted)
    });
    const items = allocation;
    const barColors = [
      palette.navy,
      palette.gold,
      [0.239, 0.435, 0.549],
      [0.357, 0.506, 0.435],
      [0.588, 0.463, 0.220],
      [0.545, 0.373, 0.388],
      [0.267, 0.329, 0.404]
    ];
    let cursor = x;
    items.forEach(function (item, index) {
      const segmentWidth = Math.max(0, width * Number(item.percent || 0) / 100);
      page.drawRectangle({ x: cursor, y: y - 19, width: segmentWidth, height: 12, color: c(PDFLib, barColors[index % barColors.length]) });
      cursor += segmentWidth;
    });
    let rowY = y - 43;
    items.forEach(function (item, index) {
      page.drawRectangle({ x: x, y: rowY + 1, width: 7, height: 7, color: c(PDFLib, barColors[index % barColors.length]) });
      page.drawText(truncate(fonts.regular, item.name, 8.2, width - 125), {
        x: x + 13,
        y: rowY,
        size: 8.2,
        font: fonts.regular,
        color: c(PDFLib, palette.ink)
      });
      const pct = Number(item.percent || 0).toFixed(1) + "%";
      page.drawText(pct, {
        x: x + width - textWidth(fonts.bold, pct, 8.2),
        y: rowY,
        size: 8.2,
        font: fonts.bold,
        color: c(PDFLib, palette.navy)
      });
      rowY -= 18;
    });
  }

  function parseChartDate(value) {
    const text = String(value == null ? "" : value);
    const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    const date = dateOnly
      ? new Date(Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]), 12))
      : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatChartDate(date, includeYear) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const label = months[date.getUTCMonth()] + " " + date.getUTCDate();
    return includeYear ? label + " '" + String(date.getUTCFullYear()).slice(-2) : label;
  }

  function formatAxisMoney(value, currency) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  }

  function chartTickIndexes(length, maximum) {
    if (length <= 0 || maximum <= 0) return [];
    const count = Math.min(length, maximum);
    if (count === 1) return [0];
    const indexes = [];
    for (let tick = 0; tick < count; tick += 1) {
      const index = Math.round(tick * (length - 1) / (count - 1));
      if (indexes[indexes.length - 1] !== index) indexes.push(index);
    }
    return indexes;
  }

  function niceChartStep(value) {
    if (!(value > 0) || !Number.isFinite(value)) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(value)));
    const scaled = value / power;
    const factors = [1, 2, 2.5, 5, 10];
    const factor = factors.find(function (candidate) { return candidate >= scaled - 1e-10; }) || 10;
    return factor * power;
  }

  function chartAxis(values) {
    const dataMin = Math.min.apply(null, values);
    const dataMax = Math.max.apply(null, values);
    const targetMin = Math.min(9000, dataMin);
    const targetMax = Math.max(9000, dataMax);
    const intervalCount = 5;
    let step = niceChartStep((targetMax - targetMin) / intervalCount || Math.max(1, Math.abs(targetMax) * 0.01));
    let min = dataMin >= 9000 ? 9000 : Math.floor(dataMin / step) * step;
    let max = min + intervalCount * step;
    while (max < targetMax - 1e-8) {
      step = niceChartStep(step * 1.000001);
      min = dataMin >= 9000 ? 9000 : Math.floor(dataMin / step) * step;
      max = min + intervalCount * step;
    }
    return { min: min, max: max, step: step, intervalCount: intervalCount };
  }

  function drawPerformanceChart(page, PDFLib, fonts, view, x, y, width, height) {
    page.drawRectangle({
      x: x,
      y: y,
      width: width,
      height: height,
      borderWidth: 0.7,
      borderColor: c(PDFLib, palette.line),
      color: c(PDFLib, palette.white)
    });
    const pointMap = new Map();
    (view.history || []).forEach(function (item) {
      const date = parseChartDate(item && item.date);
      const value = Number(item && item.value);
      if (date && Number.isFinite(value)) {
        pointMap.set(String(item.date).slice(0, 10), { date: date, value: value });
      }
    });
    const points = Array.from(pointMap.values()).sort(function (left, right) { return left.date - right.date; });
    if (points.length < 2) {
      page.drawText("Nightly account value history is not available yet.", { x: x + 18, y: y + height / 2, size: 9, font: fonts.regular, color: c(PDFLib, palette.muted) });
      return;
    }
    const values = points.map(function (point) { return point.value; });
    const axis = chartAxis(values);
    const pad = { left: 52, right: 18, top: 27, bottom: 32 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const firstTime = points[0].date.getTime();
    const lastTime = points[points.length - 1].date.getTime();
    const timeSpan = Math.max(1, lastTime - firstTime);
    const px = function (point) { return x + pad.left + (point.date.getTime() - firstTime) / timeSpan * plotWidth; };
    const py = function (value) { return y + pad.bottom + (value - axis.min) / (axis.max - axis.min) * plotHeight; };

    for (let tick = 0; tick <= axis.intervalCount; tick += 1) {
      const value = axis.min + tick * axis.step;
      const gy = py(value);
      page.drawLine({
        start: { x: x + pad.left, y: gy },
        end: { x: x + width - pad.right, y: gy },
        thickness: tick === 0 ? 0.7 : 0.35,
        color: c(PDFLib, tick === 0 ? [0.63, 0.68, 0.72] : palette.line)
      });
      const label = formatAxisMoney(value, view.currency);
      page.drawText(label, {
        x: x + pad.left - 7 - textWidth(fonts.regular, label, 6.5),
        y: gy - 2.3,
        size: 6.5,
        font: fonts.regular,
        color: c(PDFLib, palette.muted)
      });
    }
    page.drawLine({
      start: { x: x + pad.left, y: y + pad.bottom },
      end: { x: x + pad.left, y: y + height - pad.top },
      thickness: 0.7,
      color: c(PDFLib, [0.55, 0.61, 0.66])
    });

    const includeYear = points[0].date.getUTCFullYear() !== points[points.length - 1].date.getUTCFullYear();
    const xIndexes = chartTickIndexes(points.length, 7);
    xIndexes.forEach(function (index, tick) {
      const gx = px(points[index]);
      page.drawLine({
        start: { x: gx, y: y + pad.bottom },
        end: { x: gx, y: y + pad.bottom - 4 },
        thickness: 0.55,
        color: c(PDFLib, [0.55, 0.61, 0.66])
      });
      const label = formatChartDate(points[index].date, includeYear);
      const labelWidth = textWidth(fonts.regular, label, 6.4);
      const labelX = tick === 0 ? gx : tick === xIndexes.length - 1 ? gx - labelWidth : gx - labelWidth / 2;
      page.drawText(label, { x: labelX, y: y + 11, size: 6.4, font: fonts.regular, color: c(PDFLib, palette.muted) });
    });

    const axisTitle = "ACCOUNT VALUE (" + (view.currency || "USD") + ")";
    page.drawText(axisTitle, { x: x + pad.left, y: y + height - 15, size: 6.5, font: fonts.bold, color: c(PDFLib, palette.muted) });
    const latest = "LATEST " + money(points[points.length - 1].value, view.currency);
    page.drawText(latest, {
      x: x + width - pad.right - textWidth(fonts.bold, latest, 6.7),
      y: y + height - 15,
      size: 6.7,
      font: fonts.bold,
      color: c(PDFLib, palette.navy)
    });

    for (let index = 1; index < points.length; index += 1) {
      page.drawLine({
        start: { x: px(points[index - 1]), y: py(points[index - 1].value) },
        end: { x: px(points[index]), y: py(points[index].value) },
        thickness: 2.1,
        color: c(PDFLib, palette.navy)
      });
    }
    const finalPoint = points[points.length - 1];
    page.drawCircle({
      x: px(finalPoint),
      y: py(finalPoint.value),
      size: 3.8,
      color: c(PDFLib, palette.gold),
      borderColor: c(PDFLib, palette.white),
      borderWidth: 1.2
    });
  }

  function markName(quality) {
    if (quality === "public_delayed") return "PUBLIC DELAYED";
    if (quality === "model_delayed") return "AUTO MODEL";
    if (quality === "local_manual") return "LOCAL MANUAL";
    if (quality === "manual_demo") return "MANUAL TEST";
    if (quality === "stale_model") return "STALE MODEL";
    return "STALE FALLBACK";
  }

  function isCurrentAutomaticMark(quality) {
    return quality === "public_delayed" || quality === "model_delayed";
  }

  function drawHoldingsTable(page, PDFLib, fonts, view, rows, startY) {
    const x = 38;
    const widths = [180, 48, 72, 88, 130];
    const headers = ["INSTRUMENT", "QTY", "MARK", "VALUE", "MARK STATUS"];
    let cursor = x;
    page.drawRectangle({ x: x, y: startY - 19, width: widths.reduce(function (a, b) { return a + b; }, 0), height: 22, color: c(PDFLib, palette.canvas) });
    headers.forEach(function (header, index) {
      page.drawText(header, { x: cursor + 5, y: startY - 12, size: 6.8, font: fonts.bold, color: c(PDFLib, palette.muted) });
      cursor += widths[index];
    });
    let y = startY - 39;
    rows.forEach(function (holding) {
      cursor = x;
      const values = [
        truncate(fonts.bold, holding.symbol, 7.4, widths[0] - 10),
        number(holding.quantity, 4),
        money(holding.price, view.currency),
        money(holding.marketValue, view.currency),
        markName(holding.markQuality)
      ];
      values.forEach(function (value, index) {
        const font = index === 0 ? fonts.bold : fonts.regular;
        const rendered = truncate(font, value, 7.4, widths[index] - 10);
        const numeric = index >= 1 && index <= 3;
        page.drawText(rendered, {
          x: numeric ? cursor + widths[index] - 5 - font.widthOfTextAtSize(rendered, 7.4) : cursor + 5,
          y: y,
          size: 7.4,
          font: font,
          color: c(PDFLib, index === 4 && !isCurrentAutomaticMark(holding.markQuality) ? palette.red : palette.ink)
        });
        cursor += widths[index];
      });
      page.drawLine({ start: { x: x, y: y - 7 }, end: { x: x + widths.reduce(function (a, b) { return a + b; }, 0), y: y - 7 }, thickness: 0.35, color: c(PDFLib, palette.line) });
      y -= 24;
    });
    return y;
  }

  function drawMarkProvenanceTable(page, PDFLib, fonts, rows, startY) {
    const x = 38;
    const width = page.getWidth() - 76;
    page.drawRectangle({ x: x, y: startY - 20, width: width, height: 23, color: c(PDFLib, palette.canvas) });
    page.drawText("INSTRUMENT / STATUS", { x: x + 6, y: startY - 13, size: 6.8, font: fonts.bold, color: c(PDFLib, palette.muted) });
    page.drawText("SOURCE AND TIMING", { x: x + 178, y: startY - 13, size: 6.8, font: fonts.bold, color: c(PDFLib, palette.muted) });
    let y = startY - 44;
    rows.forEach(function (holding) {
      const status = markName(holding.markQuality);
      page.drawText(truncate(fonts.bold, holding.symbol, 7.6, 165), {
        x: x + 6,
        y: y,
        size: 7.6,
        font: fonts.bold,
        color: c(PDFLib, palette.navy)
      });
      page.drawText(truncate(fonts.bold, status, 6.4, 165), {
        x: x + 6,
        y: y - 13,
        size: 6.4,
        font: fonts.bold,
        color: c(PDFLib, isCurrentAutomaticMark(holding.markQuality) ? palette.green : palette.red)
      });
      page.drawText(truncate(fonts.regular, "Source: " + safe(holding.markSource, 160), 7, width - 192), {
        x: x + 178,
        y: y,
        size: 7,
        font: fonts.regular,
        color: c(PDFLib, palette.ink)
      });
      const valued = "Valued: " + dateText(holding.markAsOf, true);
      const timing = holding.markInputAsOf && holding.markInputAsOf !== holding.markAsOf
        ? valued + "  ·  Market input: " + dateText(holding.markInputAsOf, true)
        : "As of: " + dateText(holding.markAsOf, true);
      page.drawText(truncate(fonts.regular, timing, 6.7, width - 192), {
        x: x + 178,
        y: y - 13,
        size: 6.7,
        font: fonts.regular,
        color: c(PDFLib, palette.muted)
      });
      page.drawLine({ start: { x: x, y: y - 25 }, end: { x: x + width, y: y - 25 }, thickness: 0.35, color: c(PDFLib, palette.line) });
      y -= 42;
    });
    return y;
  }

  async function loadLogoBytes(logo) {
    if (!logo) return null;
    if (logo instanceof Uint8Array || logo instanceof ArrayBuffer) return logo;
    if (typeof logo !== "string" || typeof fetch !== "function") return null;
    try {
      const response = await fetch(logo, { credentials: "omit" });
      if (!response.ok) return null;
      return new Uint8Array(await response.arrayBuffer());
    } catch (_error) {
      return null;
    }
  }

  async function build(view, logo) {
    if (!root.PDFLib) throw new Error("pdf_library_unavailable");
    const PDFLib = root.PDFLib;
    const doc = await PDFLib.PDFDocument.create();
    doc.setTitle("QSF Public Demo Portfolio Report - " + safe(view.accountId, 40));
    doc.setAuthor("Quantum Strategy Fund");
    doc.setSubject("Public demonstration report - not an account statement");
    doc.setKeywords(["public demonstration", "test data", "not an account statement"]);
    const fonts = {
      regular: await doc.embedFont(PDFLib.StandardFonts.Helvetica),
      bold: await doc.embedFont(PDFLib.StandardFonts.HelveticaBold),
      serif: await doc.embedFont(PDFLib.StandardFonts.TimesRoman),
      serifBold: await doc.embedFont(PDFLib.StandardFonts.TimesRomanBold)
    };
    let logoImage = null;
    const logoBytes = await loadLogoBytes(logo);
    if (logoBytes) {
      try { logoImage = await doc.embedPng(logoBytes); } catch (_error) { logoImage = null; }
    }

    const page1 = doc.addPage(A4);
    const w = page1.getWidth();
    const h = page1.getHeight();
    page1.drawRectangle({ x: 0, y: h - 180, width: w, height: 180, color: c(PDFLib, palette.navy) });
    page1.drawRectangle({ x: 0, y: h - 184, width: w, height: 4, color: c(PDFLib, palette.gold) });
    page1.drawText("INVESTOR PORTFOLIO REPORT", { x: 38, y: h - 68, size: 7.8, font: fonts.bold, color: c(PDFLib, palette.gold) });
    page1.drawText("Public Demo Report", { x: 38, y: h - 106, size: 28, font: fonts.serifBold, color: c(PDFLib, palette.white) });
    page1.drawText(safe(view.portfolioName, 70), { x: 38, y: h - 132, size: 12, font: fonts.regular, color: c(PDFLib, [0.79, 0.85, 0.89]) });
    page1.drawText("Generated " + dateText(view.generatedAt, true), { x: 38, y: h - 153, size: 8, font: fonts.regular, color: c(PDFLib, [0.68, 0.76, 0.82]) });
    if (logoImage) {
      const scaled = logoImage.scaleToFit(92, 92);
      page1.drawImage(logoImage, { x: w - 42 - scaled.width, y: h - 137, width: scaled.width, height: scaled.height, opacity: 0.96 });
    }
    page1.drawRectangle({ x: 38, y: h - 209, width: w - 76, height: 19, color: c(PDFLib, palette.goldLight), borderWidth: 0.5, borderColor: c(PDFLib, palette.gold) });
    page1.drawText("PUBLIC DEMONSTRATION - NOT AN ACCOUNT STATEMENT", { x: 50, y: h - 203, size: 8.2, font: fonts.bold, color: c(PDFLib, palette.navy) });

    page1.drawText("Illustrative account summary", { x: 38, y: h - 244, size: 16, font: fonts.serifBold, color: c(PDFLib, palette.navy) });
    const gap = 10;
    const cardWidth = (w - 76 - gap) / 2;
    drawMetric(page1, PDFLib, fonts, 38, h - 330, cardWidth, "Demo account value", money(view.nav, view.currency), palette.navy);
    drawMetric(page1, PDFLib, fonts, 38 + cardWidth + gap, h - 330, cardWidth, view.returnBasisLabel || "Illustrative return", percent(view.returnPct), view.returnPct >= 0 ? palette.green : palette.red);
    drawMetric(page1, PDFLib, fonts, 38, h - 412, cardWidth, "Cash & Cash Equivalents", money(view.cashAndCashEquivalents == null ? view.cash : view.cashAndCashEquivalents, view.currency), palette.navy2);
    drawMetric(page1, PDFLib, fonts, 38 + cardWidth + gap, h - 412, cardWidth, view.baselineLabel || "Published test baseline", money(view.openingNav, view.currency), palette.gold);

    drawAllocation(page1, PDFLib, fonts, view.allocation || [], 38, h - 454, w - 76);

    page1.drawRectangle({ x: 38, y: 75, width: w - 76, height: 104, color: c(PDFLib, palette.canvas), borderWidth: 0.7, borderColor: c(PDFLib, palette.line) });
    page1.drawText("IMPORTANT TEST-DATA NOTICE", { x: 51, y: 158, size: 8, font: fonts.bold, color: c(PDFLib, palette.navy) });
    drawWrapped(page1, fonts.regular,
      "This illustrative report was generated entirely in the browser from intentionally public sample data and local edits. It is not an official statement, valuation, tax document, audited record, proof of ownership, or offer or solicitation. Do not rely on it for investment, accounting, or tax decisions.",
      { x: 51, y: 139, size: 8.1, lineHeight: 12, maxWidth: w - 102, maxLines: 6, color: c(PDFLib, palette.muted) });
    drawWatermark(page1, PDFLib, fonts.bold);

    const holdings = Array.isArray(view.holdings) ? view.holdings : [];
    const chunks = [];
    for (let index = 0; index < holdings.length; index += 16) chunks.push(holdings.slice(index, index + 16));
    if (!chunks.length) chunks.push([]);
    chunks.forEach(function (rows, index) {
      const page = doc.addPage(A4);
      drawTopRule(page, PDFLib, fonts, index === 0 ? "Holdings and nightly account value history" : "Holdings continued", safe(view.portfolioName, 70));
      let tableY;
      if (index === 0) {
        page.drawText("NIGHTLY ACCOUNT VALUE HISTORY", { x: 38, y: 741, size: 8, font: fonts.bold, color: c(PDFLib, palette.muted) });
        page.drawText("Completed end-of-day estimates from public closing prices and model-derived option values.", { x: 38, y: 729, size: 6.8, font: fonts.regular, color: c(PDFLib, palette.muted) });
        drawPerformanceChart(page, PDFLib, fonts, view, 38, 565, page.getWidth() - 76, 160);
        page.drawText("PUBLIC TEST HOLDINGS", { x: 38, y: 541, size: 8, font: fonts.bold, color: c(PDFLib, palette.muted) });
        tableY = 526;
      } else {
        page.drawText("PUBLIC TEST HOLDINGS", { x: 38, y: 741, size: 8, font: fonts.bold, color: c(PDFLib, palette.muted) });
        tableY = 726;
      }
      const endY = drawHoldingsTable(page, PDFLib, fonts, view, rows, tableY);
      if (index === chunks.length - 1 && endY > 105) {
        page.drawRectangle({ x: 38, y: 64, width: page.getWidth() - 76, height: 36, color: c(PDFLib, palette.goldLight), borderWidth: 0.5, borderColor: c(PDFLib, palette.gold) });
        page.drawText("Marks may be delayed, model-estimated, or manually supplied. Every value is illustrative and subject to change.", { x: 50, y: 78, size: 7.6, font: fonts.bold, color: c(PDFLib, palette.navy) });
      }
      drawWatermark(page, PDFLib, fonts.bold);
    });

    const provenanceChunks = [];
    for (let index = 0; index < holdings.length; index += 14) provenanceChunks.push(holdings.slice(index, index + 14));
    if (!provenanceChunks.length) provenanceChunks.push([]);
    provenanceChunks.forEach(function (rows, index) {
      const page = doc.addPage(A4);
      drawTopRule(page, PDFLib, fonts, index === 0 ? "Mark provenance" : "Mark provenance continued", safe(view.portfolioName, 70));
      page.drawText("SOURCE AND FRESHNESS FOR EACH ILLUSTRATIVE MARK", { x: 38, y: 741, size: 8, font: fonts.bold, color: c(PDFLib, palette.muted) });
      const endY = drawMarkProvenanceTable(page, PDFLib, fonts, rows, 726);
      if (index === provenanceChunks.length - 1 && endY > 104) {
        page.drawRectangle({ x: 38, y: 64, width: page.getWidth() - 76, height: 38, color: c(PDFLib, palette.goldLight), borderWidth: 0.5, borderColor: c(PDFLib, palette.gold) });
        page.drawText("AUTO MODEL marks are estimates derived from delayed underlier prices and are not option-market quotes.", { x: 50, y: 79, size: 7.4, font: fonts.bold, color: c(PDFLib, palette.navy) });
      }
      drawWatermark(page, PDFLib, fonts.bold);
    });

    const pages = doc.getPages();
    pages.forEach(function (page, index) { drawFooter(page, PDFLib, fonts, index + 1, pages.length); });
    return await doc.save();
  }

  async function download(view, logoUrl) {
    const bytes = await build(view, logoUrl);
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const date = new Date().toISOString().slice(0, 10);
    const account = safe(view.accountId, 40).replace(/[^A-Za-z0-9_-]+/g, "-") || "demo";
    const filename = "QSF-" + account + "-public-demo-report-" + date + ".pdf";
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    root.setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    return { filename: filename, byteLength: bytes.length };
  }

  root.QSFDemoPdf = { build: build, download: download, version: "1.1.0" };
})(typeof window !== "undefined" ? window : globalThis);
