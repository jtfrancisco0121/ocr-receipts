from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from resibo.config import Settings
from resibo.extractor import CallPolicy, extract_batch
from resibo.llm import LLMResult
from resibo.models import ErrorCode, ResiboError, Status
from tests.helpers import FakeLLM, raw_invoice, result

TODAY = date(2026, 11, 23)


def pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def settings(**kw) -> Settings:
    return Settings(**kw)


def inv(number: str, pages: list[int], **kw):
    return raw_invoice(invoice_number=number, source_pages=pages, **kw)


def run(llm, pages, policy=None, name="march.pdf", **kw):
    return extract_batch(pdf(pages), name, llm, settings(**kw), policy, TODAY)


def test_single_chunk():
    llm = FakeLLM({(1, 3): result(inv("A", [1]), inv("B", [2, 3], seller_tin="999-999-999-00000"))})
    out = run(llm, 3)
    assert [i.id for i in out.invoices] == ["INV-001", "INV-002"]
    assert out.page_count == 3 and out.chunks == 1 and len(llm.calls) == 1
    assert out.invoices[0].status == Status.OK


def test_pdf_checks_run_first():
    with pytest.raises(ResiboError) as exc:
        extract_batch(b"nope", "x.pdf", FakeLLM({}), settings(), None, TODAY)
    assert exc.value.code == ErrorCode.NOT_A_PDF


def test_ids_follow_page_order_even_if_model_order_differs():
    llm = FakeLLM({(1, 2): result(inv("B", [2]), inv("A", [1]))})
    out = run(llm, 2)
    assert [(i.id, i.invoice_number) for i in out.invoices] == [
        ("INV-001", "A"),
        ("INV-002", "B"),
    ]


def test_invoice_across_chunk_boundary_is_reread_whole():
    first = result(inv("A", [1, 2]), inv("B", [9, 10], is_complete=False))
    second = result(inv("B", [9, 10, 11, 12]), inv("C", [13, 14]), inv("D", [15, 16]))
    third = result(inv("E", [21]))
    llm = FakeLLM({(1, 10): first, (9, 20): second, (21, 23): third})
    out = run(llm, 23)
    assert [i.invoice_number for i in out.invoices] == ["A", "B", "C", "D", "E"]
    assert [c["first"] for c in llm.calls] == [1, 9, 21]
    assert out.invoices[1].source_pages == [9, 10, 11, 12]


def test_complete_receipt_sharing_restart_page_is_not_duplicated():
    first = result(
        inv("A", [1]),
        inv("B", [9, 10], is_complete=False),
        inv("R", [9], seller_name="Corner Store", seller_tin=""),
    )
    second = result(
        inv("B", [9, 10]),
        inv("R", [9], seller_name="Corner Store", seller_tin=""),
    )
    llm = FakeLLM({(1, 10): first, (9, 15): second})
    out = run(llm, 15)
    numbers = [i.invoice_number for i in out.invoices]
    assert numbers == ["A", "B", "R"]
    assert all("DUPLICATE" not in [f.code for f in i.flags] for i in out.invoices)


def test_loop_guard_accepts_and_flags_split():
    # the re-read starting at page 9 is again incomplete from its first page to its last page
    other = {"seller_tin": "999-999-999-00000"}
    first = result(inv("A", [1], **other), inv("B", [9, 10], is_complete=False))
    second = result(inv("B", list(range(9, 21)), is_complete=False))
    third = result(inv("C", [25], **other))
    llm = FakeLLM({(1, 10): first, (9, 20): second, (21, 30): third})
    out = run(llm, 30, pages_per_chunk=10)
    assert [(c["first"], c["last"]) for c in llm.calls] == [(1, 10), (9, 20), (21, 30)]
    b = out.invoices[1]
    codes = [f.code for f in b.flags]
    assert "SPLIT_ACROSS_CHUNKS" in codes and "INCOMPLETE_DOCUMENT" in codes
    assert b.status == Status.REVIEW
    assert [i.invoice_number for i in out.invoices] == ["A", "B", "C"]


def test_partial_invoice_at_end_of_file_is_accepted():
    llm = FakeLLM({(1, 2): result(inv("A", [1, 2], is_complete=False))})
    out = run(llm, 2)
    assert [f.code for f in out.invoices[0].flags] == ["INCOMPLETE_DOCUMENT"]


def test_recursive_halving_on_max_tokens():
    truncated = LLMResult({}, 100, 16000, "max_tokens")
    llm = FakeLLM(
        {
            (1, 4): truncated,
            (1, 2): truncated,
            (1, 1): result(inv("A", [1])),
            (2, 2): result(inv("B", [2])),
            (3, 4): result(inv("C", [3, 4])),
        }
    )
    out = run(llm, 4)
    assert [i.invoice_number for i in out.invoices] == ["A", "B", "C"]
    assert [(c["first"], c["last"]) for c in llm.calls] == [(1, 4), (1, 2), (1, 1), (2, 2), (3, 4)]
    assert out.halvings == 2


def test_halving_stitches_an_invoice_cut_by_the_halving():
    truncated = LLMResult({}, 1, 1, "max_tokens")
    llm = FakeLLM(
        {
            (1, 4): truncated,
            (1, 2): result(inv("A", [1]), inv("B", [2], is_complete=False)),
            (2, 4): result(inv("B", [2, 3, 4])),
        }
    )
    out = run(llm, 4)
    assert [i.invoice_number for i in out.invoices] == ["A", "B"]
    assert out.invoices[1].source_pages == [2, 3, 4]


def test_single_page_truncated_is_error():
    llm = FakeLLM({(1, 1): LLMResult({}, 1, 1, "max_tokens")})
    with pytest.raises(ResiboError) as exc:
        run(llm, 1)
    assert exc.value.code == ErrorCode.EXTRACTION_TRUNCATED


def test_context_window_exceeded_is_handled_like_max_tokens():
    llm = FakeLLM(
        {
            (1, 2): LLMResult({}, 1, 1, "model_context_window_exceeded"),
            (1, 1): result(inv("A", [1])),
            (2, 2): result(inv("B", [2])),
        }
    )
    assert len(run(llm, 2).invoices) == 2


def test_refusal_and_unknown_stop_reason():
    with pytest.raises(ResiboError) as exc:
        run(FakeLLM({(1, 1): LLMResult({}, 1, 1, "refusal")}), 1)
    assert exc.value.code == ErrorCode.UPSTREAM_REFUSED
    with pytest.raises(ResiboError) as exc:
        run(FakeLLM({(1, 1): LLMResult({}, 1, 1, "pause_turn")}), 1)
    assert exc.value.code == ErrorCode.UPSTREAM_ERROR


def test_repair_invalid_then_valid():
    bad = result(inv("A", [7]))  # page 7 is outside 1-1
    llm = FakeLLM({(1, 1): [bad, result(inv("A", [1]))]})
    out = run(llm, 1)
    assert out.repair_retries == 1 and len(llm.calls) == 2
    assert llm.calls[0]["repair_errors"] is None
    assert "source_pages" in llm.calls[1]["repair_errors"]
    assert out.invoices[0].source_pages == [1]


def test_repair_for_malformed_output():
    llm = FakeLLM({(1, 1): [LLMResult({}, 5, 5, "end_turn"), result(inv("A", [1]))]})
    assert run(llm, 1).repair_retries == 1


def test_invalid_twice_is_extraction_invalid():
    llm = FakeLLM({(1, 1): result(inv("A", [9]))})
    with pytest.raises(ResiboError) as exc:
        run(llm, 1)
    assert exc.value.code == ErrorCode.EXTRACTION_INVALID
    assert "source_pages" in exc.value.message
    assert len(llm.calls) == 2


def test_empty_source_pages_is_invalid():
    llm = FakeLLM({(1, 1): result(inv("A", []))})
    with pytest.raises(ResiboError) as exc:
        run(llm, 1)
    assert exc.value.code == ErrorCode.EXTRACTION_INVALID


def test_value_problems_do_not_trigger_repair():
    llm = FakeLLM({(1, 1): result(inv("A", [1], invoice_date="2026-02-30"))})
    out = run(llm, 1)
    assert len(llm.calls) == 1
    assert out.invoices[0].invoice_date is None
    assert "invoice_date" in out.invoices[0].uncertain_fields


def test_usage_summed_across_chunks_and_repairs():
    llm = FakeLLM(
        {
            (1, 2): [result(inv("A", [9])), result(inv("A", [1]))],
            (3, 4): result(inv("B", [3], seller_tin="999-999-999-00000")),
        }
    )
    out = run(llm, 4, pages_per_chunk=2)
    assert len(llm.calls) == 3
    assert out.input_tokens == 300 and out.output_tokens == 150


def test_public_policy_caps_halving_depth_and_repairs():
    truncated = LLMResult({}, 1, 1, "max_tokens")
    llm = FakeLLM({(1, 4): truncated, (1, 2): truncated, (3, 4): truncated})
    with pytest.raises(ResiboError) as exc:
        run(llm, 4, CallPolicy(max_halving_depth=1, max_repairs=1))
    assert exc.value.code == ErrorCode.EXTRACTION_TRUNCATED
    assert [(c["first"], c["last"]) for c in llm.calls] == [(1, 4), (1, 2)]

    bad = result(inv("A", [9]))
    llm = FakeLLM({(1, 1): bad, (2, 2): bad})
    with pytest.raises(ResiboError) as exc:
        run(llm, 2, CallPolicy(max_repairs=1), pages_per_chunk=1)
    assert exc.value.code == ErrorCode.EXTRACTION_INVALID
    assert len(llm.calls) == 2  # one call + the single allowed repair; no second repair


def test_reserve_and_settle_called_for_every_call_including_failures():
    events = []

    def reserve(pages):
        events.append(("reserve", pages))
        return len(events)

    def settle(token, tin, tout):
        events.append(("settle", token, tin, tout))

    truncated = LLMResult({}, 7, 8, "max_tokens")
    llm = FakeLLM(
        {
            (1, 2): truncated,
            (1, 1): [result(inv("A", [9])), result(inv("A", [1]))],
            (2, 2): result(inv("B", [2], seller_tin="999-999-999-00000")),
        }
    )
    out = run(llm, 2, CallPolicy(reserve=reserve, settle=settle))
    assert len(llm.calls) == 4  # chunk, half, repair, half
    assert [e[0] for e in events].count("reserve") == 4
    assert [e[0] for e in events].count("settle") == 4
    assert events[0] == ("reserve", 2) and events[1] == ("settle", 1, 7, 8)
    assert out.llm_calls == 4


def test_settle_called_with_none_when_the_call_fails():
    class Boom:
        def extract(self, *a, **k):
            raise ResiboError(ErrorCode.UPSTREAM_ERROR, "down")

    events = []
    policy = CallPolicy(
        reserve=lambda pages: "tok", settle=lambda t, i, o: events.append((t, i, o))
    )
    with pytest.raises(ResiboError):
        run(Boom(), 1, policy)
    assert events == [("tok", None, None)]


def test_budget_refusal_stops_the_request_mid_way():
    calls = {"n": 0}

    def reserve(pages):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ResiboError(ErrorCode.DEMO_BUDGET_EXHAUSTED, "used up")

    llm = FakeLLM({(1, 1): result(inv("A", [1])), (2, 2): result(inv("B", [2]))})
    with pytest.raises(ResiboError) as exc:
        run(llm, 2, CallPolicy(reserve=reserve), pages_per_chunk=1)
    assert exc.value.code == ErrorCode.DEMO_BUDGET_EXHAUSTED
    assert len(llm.calls) == 1


def test_every_call_gets_total_pages_and_file_name_and_the_right_pages():
    truncated = LLMResult({}, 1, 1, "max_tokens")
    llm = FakeLLM(
        {
            (1, 4): truncated,
            (1, 2): [result(inv("A", [9])), result(inv("A", [1]))],
            (3, 4): result(inv("B", [3], seller_tin="999-999-999-00000")),
        }
    )
    run(llm, 4, name="quarter.pdf")
    assert len(llm.calls) == 4  # chunk, half, repair, half
    assert all(c["total_pages"] == 4 and c["file_name"] == "quarter.pdf" for c in llm.calls)
    sent = [len(PdfReader(BytesIO(c["pdf_bytes"])).pages) for c in llm.calls]
    assert sent == [4, 2, 2, 2]


def test_on_checked_hook_runs_after_checks_and_before_calls():
    order = []
    policy = CallPolicy(on_checked=lambda pages: order.append(("checked", pages)))
    llm = FakeLLM({(1, 2): result(inv("A", [1]))})
    llm_extract = llm.extract

    def spy(*a, **k):
        order.append(("call",))
        return llm_extract(*a, **k)

    llm.extract = spy
    run(llm, 2, policy)
    assert order == [("checked", 2), ("call",)]
    with pytest.raises(ResiboError):
        extract_batch(b"junk", "x", llm, settings(), policy, TODAY)
    assert order == [("checked", 2), ("call",)]  # rejected files never reach the hook


def test_validation_flags_applied():
    llm = FakeLLM({(1, 1): result(inv("A", [1], total_amount="99999.00"))})
    out = run(llm, 1)
    assert out.invoices[0].status == Status.ERROR
    assert [f.code for f in out.invoices[0].flags] == ["TOTAL_MISMATCH"]
