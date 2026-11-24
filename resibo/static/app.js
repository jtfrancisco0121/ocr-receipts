// Resibo web UI. Reads only the Batch JSON and the error envelope (SPEC.md section 7 and 11).
// Every value from the API is inserted with textContent or attribute setters, never as HTML.
(function () {
  "use strict";

  var MESSAGES = {
    network: "Could not reach the server. Check your connection and try again.",
    expired: "This result has expired. Run the extraction again.",
    noDocuments: "No invoices or receipts found in this file.",
    noRows: "No documents with this status.",
  };
  var FALLBACK_CODES = ["RATE_LIMITED", "DEMO_BUDGET_EXHAUSTED"];

  var state = {
    file: null,
    runLive: false,
    running: false,
    batch: null,
    filter: "ALL",
    expanded: {},
    timer: null,
  };

  function $(id) {
    return document.getElementById(id);
  }

  var R = window.Resibo;
  var el = R.el;
  var clear = R.clear;
  var formatMoney = R.formatMoney;
  var dash = R.dash;
  var formatPages = R.formatPages;
  var plural = R.plural;

  // --- state rendering ----------------------------------------------------------------------

  function setBusy(busy) {
    state.running = busy;
    var disabledWhenBusy = ["sample-btn", "run-live"];
    disabledWhenBusy.forEach(function (id) {
      $(id).disabled = busy;
    });
    $("extract-btn").disabled = busy || !state.file;
    $("drop-zone").setAttribute("aria-disabled", busy ? "true" : "false");
    $("file-input").disabled = busy;
  }

  function showProgress(pages) {
    var started = Date.now();
    var label =
      pages && pages > 0
        ? "Reading " + plural(pages, "page", "pages") + " with Claude…"
        : "Reading your PDF with Claude…";
    function tick() {
      var seconds = Math.floor((Date.now() - started) / 1000);
      $("progress-text").textContent = label + " " + seconds + " s";
    }
    tick();
    $("progress").hidden = false;
    state.timer = setInterval(tick, 1000);
  }

  function hideProgress() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
    $("progress").hidden = true;
  }

  function showError(message, offerFallback) {
    $("error-message").textContent = message;
    $("error-fallback").hidden = !offerFallback;
    $("error-banner").hidden = false;
  }

  function hideError() {
    $("error-banner").hidden = true;
  }

  function hideResults() {
    $("results").hidden = true;
    $("sample-missing").hidden = true;
    state.batch = null;
  }

  // --- results ------------------------------------------------------------------------------

  function renderSummary(batch) {
    var s = batch.summary;
    $("card-documents").textContent = String(s.documents);
    var totalNode = $("card-total");
    clear(totalNode);
    var lines = R.totalLines(s, batch.invoices);
    if (!lines.length) {
      totalNode.textContent = "—";
    } else {
      lines.forEach(function (line) {
        var children = [el("span", { text: formatMoney(line.amount, line.currency) })];
        if (line.errors > 0) {
          children.push(
            el("span", {
              className: "small muted total-note",
              text:
                "leaves out " + plural(line.errors, "error", "errors") + ", " +
                formatMoney(line.leftOut, line.currency),
            }),
          );
        }
        totalNode.appendChild(el("span", { className: "total-line" }, children));
      });
    }
    renderCounts(batch);
    var tokens = batch.usage.input_tokens + batch.usage.output_tokens;
    var cost = batch.usage.estimated_cost_usd;
    $("card-cost").textContent =
      tokens.toLocaleString("en-US") + " tokens · " +
      (cost === null || cost === undefined ? "—" : "~$" + cost.toFixed(2));
    $("card-time").textContent = (batch.duration_ms / 1000).toFixed(1) + " s";
  }

  // The counts come from batch.summary, the same numbers the status counts and the filter chips
  // show, so the screen never contradicts itself.
  function statusCounts(batch) {
    var s = batch.summary;
    return [
      ["ALL", "All", s.documents],
      ["OK", "OK", s.ok],
      ["REVIEW", "Review", s.review],
      ["ERROR", "Error", s.error],
    ];
  }

  function setFilter(batch, filter) {
    state.filter = filter;
    renderCounts(batch);
    renderChips(batch);
    renderTable(batch);
  }

  function renderCounts(batch) {
    var buttons = { OK: $("count-ok"), REVIEW: $("count-review"), ERROR: $("count-error") };
    statusCounts(batch).forEach(function (option) {
      var button = buttons[option[0]];
      if (!button) return;
      button.textContent = option[1] + " " + option[2];
      button.setAttribute("aria-pressed", state.filter === option[0] ? "true" : "false");
      button.onclick = function () { setFilter(batch, option[0]); };
    });
  }

  function renderChips(batch) {
    var chips = $("filter-chips");
    clear(chips);
    statusCounts(batch).forEach(function (option) {
      var chip = el("button", {
        className: "chip",
        text: option[1] + " (" + option[2] + ")",
        attrs: { type: "button", "aria-pressed": state.filter === option[0] ? "true" : "false" },
      });
      chip.addEventListener("click", function () { setFilter(batch, option[0]); });
      chips.appendChild(chip);
    });
  }

  function flagMessages(inv) {
    return inv.flags.map(function (flag) { return flag.message; });
  }

  function flagCell(inv) {
    var cell = el("td");
    if (!inv.flags.length) {
      cell.textContent = "—";
      return cell;
    }
    var wrap = el("span", { className: "flag-wrap" });
    var pill = el("span", {
      className: "flag-pill",
      text: plural(inv.flags.length, "flag", "flags"),
      attrs: { tabindex: "0" },
    });
    var tip = el(
      "div",
      { className: "tooltip", attrs: { role: "tooltip" } },
      flagMessages(inv).map(function (message) { return el("p", { text: message }); }),
    );
    pill.addEventListener("keydown", function (event) {
      if (event.key === "Escape") wrap.classList.add("dismissed");
    });
    pill.addEventListener("blur", function () { wrap.classList.remove("dismissed"); });
    wrap.addEventListener("mouseleave", function () { wrap.classList.remove("dismissed"); });
    wrap.appendChild(pill);
    wrap.appendChild(tip);
    cell.appendChild(wrap);
    return cell;
  }

  function detailRow(inv, id) {
    var td = el("td", { attrs: { colspan: "8" } });
    td.appendChild(el("h3", { text: "Line items" }));
    if (!inv.line_items.length) {
      td.appendChild(el("p", { className: "small muted", text: "No line items" }));
    } else {
      var head = el("tr", {}, ["#", "Description", "Qty", "Unit", "Unit price", "Amount"].map(
        function (name, index) {
          return el("th", { text: name, attrs: { scope: "col" }, className: index >= 2 && index !== 3 ? "num" : "" });
        },
      ));
      var rows = inv.line_items.map(function (item, index) {
        return el("tr", {}, [
          el("td", { text: String(index + 1) }),
          el("td", { text: dash(item.description) }),
          el("td", { text: dash(item.quantity), className: "num" }),
          el("td", { text: dash(item.unit) }),
          el("td", { text: formatMoney(item.unit_price, inv.currency), className: "num" }),
          el("td", { text: formatMoney(item.amount, inv.currency), className: "num" }),
        ]);
      });
      td.appendChild(
        el("table", { className: "inner" }, [el("thead", {}, [head]), el("tbody", {}, rows)]),
      );
    }
    if (inv.flags.length) {
      td.appendChild(el("h3", { text: "Flags" }));
      td.appendChild(
        el(
          "ul",
          {},
          inv.flags.map(function (flag) {
            return el("li", {
              text: flag.severity.toUpperCase() + " · " + flag.code + " · " + flag.message,
            });
          }),
        ),
      );
    }
    if (inv.extraction_notes) {
      td.appendChild(el("h3", { text: "Notes" }));
      td.appendChild(el("p", { className: "small", text: inv.extraction_notes }));
    }
    return el("tr", { className: "detail", attrs: { id: id } }, [td]);
  }

  function renderTable(batch) {
    var body = $("results-body");
    clear(body);
    var empty = $("results-empty");
    var tableWrap = $("results-table").parentNode;
    var rows = batch.invoices.filter(function (inv) {
      return state.filter === "ALL" || inv.status === state.filter;
    });
    if (!batch.invoices.length) {
      empty.textContent = MESSAGES.noDocuments;
    } else if (!rows.length) {
      empty.textContent = MESSAGES.noRows;
    } else {
      empty.textContent = "";
    }
    empty.hidden = !empty.textContent;
    tableWrap.hidden = rows.length === 0;

    rows.forEach(function (inv) {
      var detailId = "detail-" + inv.id;
      var open = !!state.expanded[inv.id];
      var tr = el(
        "tr",
        {
          className: "row status-" + inv.status.toLowerCase(),
          attrs: { tabindex: "0", "aria-expanded": open ? "true" : "false", "aria-controls": detailId },
        },
        [
          el("td", {}, [
            el("span", { className: "badge badge-" + inv.status.toLowerCase(), text: inv.status }),
          ]),
          el("td", { text: dash(inv.seller_name) }),
          el("td", { text: dash(inv.invoice_number) }),
          el("td", { text: dash(inv.invoice_date) }),
          el("td", { className: "num", text: formatMoney(inv.vat_amount, inv.currency) }),
          el("td", { className: "num", text: formatMoney(inv.total_amount, inv.currency) }),
          el("td", { className: "mono", text: formatPages(inv.source_pages) }),
          flagCell(inv),
        ],
      );
      function toggle() {
        state.expanded[inv.id] = !state.expanded[inv.id];
        renderTable(batch);
      }
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", function (event) {
        if (event.target !== tr) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      body.appendChild(tr);
      if (open) body.appendChild(detailRow(inv, detailId));
    });
  }

  function renderBatch(batch) {
    state.batch = batch;
    state.filter = "ALL";
    state.expanded = {};
    $("sample-missing").hidden = true;
    var label = $("cached-label");
    if (batch.cached) {
      var day = batch.recorded_at ? " on " + String(batch.recorded_at).slice(0, 10) : "";
      label.textContent =
        "Cached result from a live run" + day + " · turn on Run live to process it now";
      label.hidden = false;
    } else {
      label.textContent = "";
      label.hidden = true;
    }
    renderSummary(batch);
    renderChips(batch);
    renderTable(batch);
    $("results").hidden = false;
    var counts = batch.summary;
    $("announcer").textContent =
      plural(counts.documents, "document", "documents") + " found: " + counts.ok + " OK, " +
      counts.review + " Review, " + counts.error + " Error";
  }

  // --- requests -----------------------------------------------------------------------------

  async function readError(response) {
    try {
      var body = await response.json();
      if (body && body.error) return body.error;
    } catch (err) {
      // not JSON
    }
    return { code: "INTERNAL", message: "Something went wrong. Please try again." };
  }

  async function handleResponse(response, isCachedRequest) {
    if (response.ok) {
      renderBatch(await response.json());
      return;
    }
    var error = await readError(response);
    if (error.code === "SAMPLE_NOT_RECORDED" && isCachedRequest) {
      $("sample-missing").hidden = false;
      return;
    }
    showError(error.message, FALLBACK_CODES.indexOf(error.code) !== -1);
  }

  async function run(pages, task, isCachedRequest) {
    if (state.running) return;
    hideError();
    hideResults();
    setBusy(true);
    showProgress(pages);
    try {
      await handleResponse(await task(), isCachedRequest);
    } catch (err) {
      showError(MESSAGES.network, false);
    } finally {
      hideProgress();
      setBusy(false);
    }
  }

  function postFile(file) {
    var form = new FormData();
    form.append("file", file, file.name);
    return fetch("/api/extract", { method: "POST", body: form });
  }

  function extractSelected() {
    if (!state.file) return;
    var file = state.file;
    run(file.pageCount || 0, function () { return postFile(file); }, false);
  }

  function loadCachedSample() {
    run(0, function () { return fetch("/api/sample/result"); }, true);
  }

  function runSampleLive() {
    run(8, async function () {
      var pdf = await fetch("/api/sample");
      if (!pdf.ok) return pdf;
      var blob = await pdf.blob();
      return postFile(new File([blob], "sample-invoices.pdf", { type: "application/pdf" }));
    }, false);
  }

  function trySample() {
    if (state.runLive) runSampleLive();
    else loadCachedSample();
  }

  // --- downloads ----------------------------------------------------------------------------

  function fileNameFrom(response, fallback) {
    var header = response.headers.get("Content-Disposition") || "";
    var match = /filename="?([^";]+)"?/.exec(header);
    return match ? match[1] : fallback;
  }

  async function download(url, fallbackName) {
    try {
      var response = await fetch(url);
      if (!response.ok) {
        var error = await readError(response);
        showError(error.code === "BATCH_NOT_FOUND" ? MESSAGES.expired : error.message, false);
        return;
      }
      var blob = await response.blob();
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = fileNameFrom(response, fallbackName);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    } catch (err) {
      showError(MESSAGES.network, false);
    }
  }

  // --- file selection -----------------------------------------------------------------------

  // Best-effort page count from the PDF text; compressed object streams hide it, then null.
  async function countPages(file) {
    if (file.size > 25 * 1024 * 1024) return null;
    try {
      var text = new TextDecoder("latin1").decode(await file.arrayBuffer());
      var best = 0;
      var pattern = /\/Type\s*\/Pages[^]{0,400}?\/Count\s+(\d+)|\/Count\s+(\d+)[^]{0,400}?\/Type\s*\/Pages/g;
      var found;
      while ((found = pattern.exec(text)) !== null) {
        best = Math.max(best, parseInt(found[1] || found[2], 10));
      }
      return best > 0 ? best : null;
    } catch (err) {
      return null;
    }
  }

  function formatSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  async function selectFile(file) {
    if (!file || state.running) return;
    state.file = file;
    file.pageCount = null;
    hideError();
    hideResults();
    $("drop-text").textContent = file.name + " · " + formatSize(file.size);
    $("extract-btn").disabled = false;
    var pages = await countPages(file);
    if (state.file === file && pages) {
      file.pageCount = pages;
      $("drop-text").textContent = file.name + " · " + plural(pages, "page", "pages");
    }
  }

  // --- wiring -------------------------------------------------------------------------------

  function init() {
    var zone = $("drop-zone");
    var input = $("file-input");
    zone.addEventListener("click", function () {
      if (!state.running) input.click();
    });
    zone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (!state.running) input.click();
      }
    });
    zone.addEventListener("dragover", function (event) {
      event.preventDefault();
      if (!state.running) zone.classList.add("active");
    });
    zone.addEventListener("dragleave", function () { zone.classList.remove("active"); });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      zone.classList.remove("active");
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) selectFile(files[0]);
    });
    input.addEventListener("change", function () {
      if (input.files && input.files.length) selectFile(input.files[0]);
    });

    $("extract-btn").addEventListener("click", extractSelected);
    $("sample-btn").addEventListener("click", trySample);
    $("run-live").addEventListener("click", function () {
      state.runLive = !state.runLive;
      $("run-live").setAttribute("aria-checked", state.runLive ? "true" : "false");
    });
    $("error-dismiss").addEventListener("click", hideError);
    $("error-fallback").addEventListener("click", function () {
      hideError();
      loadCachedSample();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !$("error-banner").hidden) hideError();
    });

    $("btn-xlsx").addEventListener("click", function () {
      if (state.batch) download(state.batch.downloads.xlsx, "resibo.xlsx");
    });
    $("btn-invoices-csv").addEventListener("click", function () {
      if (state.batch) download(state.batch.downloads.invoices_csv, "invoices.csv");
    });
    $("btn-line-items-csv").addEventListener("click", function () {
      if (state.batch) download(state.batch.downloads.line_items_csv, "line_items.csv");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
