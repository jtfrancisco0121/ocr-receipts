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
    var disabledWhenBusy = ["sample-btn", "run-live-btn", "sample-missing-live-btn"];
    disabledWhenBusy.forEach(function (id) {
      $(id).disabled = busy;
    });
    updateActions();
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

  // With no file chosen the sample button is the first action and Extract is hidden; with a file
  // chosen Extract is primary and the sample button steps back.
  function updateActions() {
    var hasFile = !!state.file;
    $("extract-btn").hidden = !hasFile;
    $("extract-btn").disabled = state.running || !hasFile;
    $("sample-btn").className = "btn " + (hasFile ? "btn-secondary" : "btn-primary");
  }

  function hideResults() {
    $("results").hidden = true;
    $("sample-missing").hidden = true;
    state.batch = null;
  }

  // --- results ------------------------------------------------------------------------------

  function renderSummary(batch) {
    var s = batch.summary;
    $("total-documents").textContent = String(s.documents);
    var amountNode = $("total-amount");
    var noteNode = $("total-errors-note");
    clear(amountNode);
    clear(noteNode);
    var lines = R.totalLines(s, batch.invoices);
    if (!lines.length) amountNode.textContent = "—";
    lines.forEach(function (line) {
      amountNode.appendChild(
        el("span", { className: "total-line", text: formatMoney(line.amount, line.currency) }),
      );
      if (line.errors > 0) {
        var prefix = lines.length > 1 ? line.currency + ": " : "";
        noteNode.appendChild(
          el("span", {
            className: "total-line total-note",
            text:
              prefix + "leaves out " + plural(line.errors, "error", "errors") + ", " +
              formatMoney(line.leftOut, line.currency),
          }),
        );
      }
    });
    renderCounts(batch);
    var cost = batch.usage.estimated_cost_usd;
    var tokens = batch.usage.input_tokens + batch.usage.output_tokens;
    $("total-cost").textContent = cost === null || cost === undefined ? "—" : "~$" + cost.toFixed(2);
    $("total-tokens").textContent = tokens.toLocaleString("en-US") + " tokens";
    $("total-time").textContent = (batch.duration_ms / 1000).toFixed(1) + " s";
    $("total-model").textContent = dash(batch.model);
  }

  // The counts come from batch.summary, the same numbers the status counts and the filter tabs
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
    renderTabs(batch);
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

  function renderTabs(batch) {
    var tabs = $("filter-tabs");
    clear(tabs);
    statusCounts(batch).forEach(function (option) {
      var tab = el("button", {
        className: "tab",
        text: option[1] + " (" + option[2] + ")",
        attrs: { type: "button", "aria-pressed": state.filter === option[0] ? "true" : "false" },
      });
      tab.addEventListener("click", function () { setFilter(batch, option[0]); });
      tabs.appendChild(tab);
    });
  }

  // The five fields that have a column; a flag on one of them marks that cell.
  var MARKED_FIELDS = ["total_amount", "invoice_number", "invoice_date", "seller_name", "vat_amount"];

  function flaggedFields(inv) {
    var marked = {};
    inv.flags.forEach(function (flag) {
      if (MARKED_FIELDS.indexOf(flag.field) !== -1) marked[flag.field] = true;
    });
    return marked;
  }

  function cellClass(base, marked, field) {
    var names = [base, marked[field] ? "flagged-field" : ""].filter(Boolean);
    return names.join(" ");
  }

  // The first flag message shows under the seller at every width; more flags show as "+N more".
  function flagNote(inv) {
    if (!inv.flags.length) return null;
    var more = inv.flags.length - 1;
    return el("p", {
      className: "flag-note",
      text: inv.flags[0].message + (more > 0 ? " +" + more + " more" : ""),
    });
  }

  function flagCell(inv) {
    return el("td", { className: "num", text: inv.flags.length ? String(inv.flags.length) : "—" });
  }

  function flagItem(flag) {
    var sentence = R.FLAG_SENTENCES[flag.code];
    return el("li", { className: "flag-item" }, [
      sentence ? el("p", { className: "flag-sentence", text: sentence }) : null,
      el("p", { className: "flag-message", text: flag.message }),
      el("p", { className: "flag-code mono", text: flag.code }),
    ]);
  }

  function lineItemsTable(inv) {
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
        el("td", { text: formatMoney(item.unit_price, inv.currency), className: "num mono" }),
        el("td", { text: formatMoney(item.amount, inv.currency), className: "num mono" }),
      ]);
    });
    return el("table", { className: "inner" }, [el("thead", {}, [head]), el("tbody", {}, rows)]);
  }

  // Flags first (why the row was marked), then the line items, then the notes.
  function detailRow(inv, id) {
    var td = el("td", { attrs: { colspan: "9" } });
    if (inv.flags.length) {
      td.appendChild(el("h3", { text: "Flags" }));
      td.appendChild(el("ul", { className: "flag-list" }, inv.flags.map(flagItem)));
    }
    td.appendChild(el("h3", { text: "Line items" }));
    if (!inv.line_items.length) {
      td.appendChild(el("p", { className: "small muted", text: "No line items" }));
    } else {
      td.appendChild(lineItemsTable(inv));
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
      var marked = flaggedFields(inv);
      var tr = el(
        "tr",
        {
          className: "row status-" + inv.status.toLowerCase(),
          attrs: { tabindex: "0", "data-id": inv.id, "aria-expanded": open ? "true" : "false", "aria-controls": detailId },
        },
        [
          el("td", { className: "chevron-cell" }, [
            el("span", { className: "chevron", attrs: { "aria-hidden": "true" } }),
          ]),
          el("td", {}, [
            el("span", { className: "badge badge-" + inv.status.toLowerCase(), text: inv.status }),
          ]),
          el("td", { className: cellClass("", marked, "seller_name") }, [
            el("span", { className: "seller-name", text: dash(inv.seller_name) }),
            flagNote(inv),
          ]),
          el("td", { className: cellClass("mono", marked, "invoice_number"), text: dash(inv.invoice_number) }),
          el("td", { className: cellClass("mono", marked, "invoice_date"), text: dash(inv.invoice_date) }),
          el("td", {
            className: cellClass("num mono", marked, "vat_amount"),
            text: formatMoney(inv.vat_amount, inv.currency),
          }),
          el("td", {
            className: cellClass("num mono", marked, "total_amount"),
            text: formatMoney(inv.total_amount, inv.currency),
          }),
          el("td", { className: "mono", text: formatPages(inv.source_pages) }),
          flagCell(inv),
        ],
      );
      function toggle() {
        state.expanded[inv.id] = !state.expanded[inv.id];
        renderTable(batch);
        var again = body.querySelector('tr.row[data-id="' + String(inv.id).replace(/["\\]/g, "") + '"]');
        if (again) again.focus();
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
    var recorded = $("recorded-run");
    if (batch.cached) {
      label.textContent =
        "Cached result from a live run · use Run it live now to process it again";
      label.hidden = false;
      $("recorded-run-text").textContent = batch.recorded_at
        ? "Recorded run · " + R.formatManilaDate(batch.recorded_at)
        : "Recorded run";
      recorded.hidden = false;
    } else {
      label.textContent = "";
      label.hidden = true;
      recorded.hidden = true;
    }
    renderSummary(batch);
    renderTabs(batch);
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
    loadCachedSample();
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
    updateActions();
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
    $("run-live-btn").addEventListener("click", runSampleLive);
    $("sample-missing-live-btn").addEventListener("click", runSampleLive);
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

  function start() {
    init();
    updateActions();
    if (location.hash === "#sample") loadCachedSample();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
