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
    const points = (view.history || []).map(function (item) {
      return { date: item.date, value: Number(item.value) };
    }).filter(function (item) { return Number.isFinite(item.value); });
    if (points.length < 2) {
      page.drawText("Insufficient scenario history for a chart.", { x: x + 18, y: y + height / 2, size: 9, font: fonts.regular, color: c(PDFLib, palette.muted) });
      return;
    }
    const values = points.map(function (point) { return point.value; });
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (min === max) { min -= 1; max += 1; }
    const spread = max - min || 1;
    min -= spread * 0.1;
    max += spread * 0.1;
    const pad = { left: 34, right: 18, top: 24, bottom: 30 };
    const px = function (index) { return x + pad.left + index * ((width - pad.left - pad.right) / (points.length - 1)); };
    const py = function (value) { return y + pad.bottom + (value - min) * ((height - pad.top - pad.bottom) / (max - min)); };
    [0, 0.5, 1].forEach(function (ratio) {
      const gy = y + pad.bottom + ratio * (height - pad.top - pad.bottom);
      page.drawLine({ start: { x: x + pad.left, y: gy }, end: { x: x + width - pad.right, y: gy }, thickness: 0.4, color: c(PDFLib, palette.line) });
    });
    for (let index = 1; index < points.length; index += 1) {
      page.drawLine({
        start: { x: px(index - 1), y: py(points[index - 1].value) },
        end: { x: px(index), y: py(points[index].value) },
        thickness: 2.1,
        color: c(PDFLib, palette.navy)
      });
    }
    points.forEach(function (point, index) {
      page.drawCircle({ x: px(index), y: py(point.value), size: index === points.length - 1 ? 3.8 : 2.4, color: c(PDFLib, index === points.length - 1 ? palette.gold : palette.navy) });
    });
    const start = safe(points[0].date, 20);
    const end = safe(points[points.length - 1].date, 20);
    page.drawText(start, { x: x + pad.left, y: y + 10, size: 7, font: fonts.regular, color: c(PDFLib, palette.muted) });
    page.drawText(end, { x: x + width - pad.right - textWidth(fonts.regular, end, 7), y: y + 10, size: 7, font: fonts.regular, color: c(PDFLib, palette.muted) });
    const latest = money(points[points.length - 1].value, view.currency);
    page.drawText(latest, { x: x + width - pad.right - textWidth(fonts.bold, latest, 8), y: y + height - 16, size: 8, font: fonts.bold, color: c(PDFLib, palette.navy) });
  }

  function markName(quality) {
    if (quality === "public_delayed") return "PUBLIC DELAYED";
    if (quality === "local_manual") return "LOCAL MANUAL";
    if (quality === "manual_demo") return "MANUAL TEST";
    return "STALE FALLBACK";
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
        page.drawText(truncate(index === 0 ? fonts.bold : fonts.regular, value, 7.4, widths[index] - 10), {
          x: cursor + 5,
          y: y,
          size: 7.4,
          font: index === 0 ? fonts.bold : fonts.regular,
          color: c(PDFLib, index === 4 && holding.markQuality !== "public_delayed" ? palette.red : palette.ink)
        });
        cursor += widths[index];
      });
      page.drawLine({ start: { x: x, y: y - 7 }, end: { x: x + widths.reduce(function (a, b) { return a + b; }, 0), y: y - 7 }, thickness: 0.35, color: c(PDFLib, palette.line) });
      y -= 24;
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
    drawMetric(page1, PDFLib, fonts, 38, h - 412, cardWidth, "Scenario cash", money(view.cash, view.currency), palette.navy2);
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
      drawTopRule(page, PDFLib, fonts, index === 0 ? "Holdings and illustrative performance" : "Holdings continued", safe(view.portfolioName, 70));
      let tableY;
      if (index === 0) {
        page.drawText("ILLUSTRATIVE ACCOUNT VALUE HISTORY", { x: 38, y: 741, size: 8, font: fonts.bold, color: c(PDFLib, palette.muted) });
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
        page.drawText("Marks may be delayed or manually supplied. Every value is illustrative and subject to change.", { x: 50, y: 78, size: 7.6, font: fonts.bold, color: c(PDFLib, palette.navy) });
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

  root.QSFDemoPdf = { build: build, download: download, version: "1.0.0" };
})(typeof window !== "undefined" ? window : globalThis);
