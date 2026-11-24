// Resibo landing page: the sample rack, drawn from GET /api/sample/result (one request per load).
// Every value from the API is inserted with textContent or attribute setters, never as HTML.
(function () {
  "use strict";

  var R = window.Resibo;
  var el = R.el;
  var clear = R.clear;

  var SAMPLE_URL = "/api/sample/result";
  var DEFAULT_SLIP = "INV-006";
  var UNAVAILABLE = "Sample not available right now";

  var state = { batch: null, models: [], selected: null };

  function $(id) {
    return document.getElementById(id);
  }

  function pageText(label) {
    return (/[-,]/.test(label) ? "pp. " : "p. ") + label;
  }

  function selectedModel() {
    return state.models.filter(function (model) { return model.id === state.selected; })[0];
  }

  // --- the redrawn document ------------------------------------------------------------------

  function docRow(label, value, field, model, className) {
    var marked = model.flagField === field ? " flagged-field" : "";
    return el("div", { className: "doc-row" + marked, attrs: { "data-field": field } }, [
      el("span", { text: label }),
      el("span", { className: "mono " + (className || ""), text: value }),
    ]);
  }

  function fieldValue(model, key) {
    return model.fields.filter(function (field) { return field.key === key; })[0].value;
  }

  function renderDocument(model) {
    var lines = model.doc.lines.length
      ? model.doc.lines.map(function (line) {
          return el("tr", {}, [
            el("td", { text: line.description }),
            el("td", { className: "num mono", text: line.quantity }),
            el("td", { className: "num mono", text: line.amount }),
          ]);
        })
      : [el("tr", {}, [el("td", { text: "No line items", attrs: { colspan: "3" } })])];
    var children = [
      el("div", { className: "doc-head" }, [
        el("div", { className: "doc-seller", text: model.seller }),
        el(
          "div",
          {
            className: "serial" + (model.flagField === "invoice_number" ? " flagged-field" : ""),
            text: "No. " + model.number,
          },
        ),
      ]),
      el("div", { className: "doc-meta mono" }, [
        el("span", { text: "TIN " + model.doc.tin }),
        el("span", {
          className: model.flagField === "invoice_date" ? "flagged-field" : "",
          text: "Date " + fieldValue(model, "invoice_date"),
        }),
      ]),
      el("p", { className: "doc-address", text: model.doc.address }),
      el("p", { className: "doc-buyer" }, [
        el("span", { className: "doc-label", text: "Sold to " }),
        el("span", { text: model.doc.buyer }),
      ]),
      el("table", { className: "doc-lines" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { text: "Description", attrs: { scope: "col" } }),
            el("th", { text: "Qty", className: "num", attrs: { scope: "col" } }),
            el("th", { text: "Amount", className: "num", attrs: { scope: "col" } }),
          ]),
        ]),
        el("tbody", {}, lines),
      ]),
      docRow("Vatable sales", fieldValue(model, "vatable_sales"), "vatable_sales", model),
      docRow("VAT 12%", fieldValue(model, "vat_amount"), "vat_amount", model),
      docRow("TOTAL AMOUNT DUE", fieldValue(model, "total_amount"), "total_amount", model, "doc-total"),
    ];
    if (model.rejected) children.push(el("span", { className: "stamp", text: "REJECTED" }));
    return el("div", { className: "doc", attrs: { "aria-label": "Redrawn document" } }, children);
  }

  // --- what Resibo read ----------------------------------------------------------------------

  function renderRead(model) {
    var rows = model.fields.map(function (field) {
      var value =
        field.key === "status"
          ? el("span", {
              className: "badge badge-" + model.status.toLowerCase(),
              text: model.status,
            })
          : el("span", { className: "mono", text: field.value });
      return el(
        "div",
        { className: "read-row" + (field.flagged ? " flagged-field" : "") },
        [el("dt", { text: field.label }), el("dd", {}, [value])],
      );
    });
    var children = [
      el("h3", { className: "read-title", text: "What Resibo read" }),
      el("dl", { className: "read-list" }, rows),
    ];
    if (model.flagMessage) children.push(el("p", { className: "flag-note", text: model.flagMessage }));
    return el("div", { className: "read" }, children);
  }

  // --- slips ---------------------------------------------------------------------------------

  function slipButton(model) {
    var selected = model.id === state.selected;
    var button = el(
      "button",
      {
        className: "slip" + (model.rejected ? " slip-rejected" : ""),
        attrs: {
          type: "button",
          role: "tab",
          id: "slip-" + model.id,
          "aria-selected": selected ? "true" : "false",
          "aria-controls": "rack-panel",
          tabindex: selected ? "0" : "-1",
        },
      },
      [
        el("span", { className: "slip-page mono", text: pageText(model.pages) }),
        el("span", { className: "slip-number mono", text: model.number }),
        el("span", { className: "slip-seller", text: model.seller }),
        el("span", { className: "slip-status", text: model.label }),
      ],
    );
    button.addEventListener("click", function () { select(model.id, false); });
    button.addEventListener("keydown", function (event) { onKey(event, model.id); });
    return button;
  }

  function onKey(event, id) {
    var ids = state.models.map(function (model) { return model.id; });
    var at = ids.indexOf(id);
    var next = -1;
    if (event.key === "ArrowRight") next = (at + 1) % ids.length;
    else if (event.key === "ArrowLeft") next = (at - 1 + ids.length) % ids.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = ids.length - 1;
    if (next === -1) return;
    event.preventDefault();
    select(ids[next], true);
  }

  function select(id, focus) {
    state.selected = id;
    render();
    if (focus) {
      var button = $("slip-" + id);
      if (button) button.focus();
    }
  }

  // --- page ----------------------------------------------------------------------------------

  function render() {
    var body = $("rack-body");
    clear(body);
    var model = selectedModel();
    var tablist = el(
      "div",
      { className: "slips", attrs: { role: "tablist", "aria-label": "Sample documents" } },
      state.models.map(slipButton),
    );
    var panel = el(
      "div",
      {
        className: "rack-panel",
        attrs: { role: "tabpanel", id: "rack-panel", "aria-labelledby": "slip-" + model.id },
      },
      [renderDocument(model), renderRead(model)],
    );
    body.appendChild(tablist);
    body.appendChild(panel);
  }

  function showUnavailable() {
    fillTally(null);
    var body = $("rack-body");
    clear(body);
    body.appendChild(
      el("p", { className: "rack-unavailable" }, [
        el("span", { text: UNAVAILABLE + ". " }),
        el("a", { className: "link", text: "Open the app", attrs: { href: "/app" } }),
      ]),
    );
  }

  // The recorded-run group of the tally comes from the same single response as the rack.
  function fillTally(batch) {
    var s = batch ? batch.summary : null;
    $("tally-documents").textContent = s ? String(s.documents) : "—";
    $("tally-flagged").textContent = s ? String(s.review + s.error) : "—";
    $("tally-time").textContent = batch ? (batch.duration_ms / 1000).toFixed(1) + " s" : "—";
  }

  function show(batch) {
    state.batch = batch;
    fillTally(batch);
    state.models = (batch.invoices || []).map(R.slipModel);
    if (!state.models.length) {
      showUnavailable();
      return;
    }
    var preferred = state.models.filter(function (model) { return model.id === DEFAULT_SLIP; })[0];
    state.selected = (preferred || state.models[0]).id;
    render();
  }

  async function load() {
    try {
      var response = await fetch(SAMPLE_URL);
      if (!response.ok) {
        showUnavailable();
        return;
      }
      show(await response.json());
    } catch (err) {
      showUnavailable();
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
