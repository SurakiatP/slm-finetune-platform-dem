"""Invoice starters require grounded text and invoice-disjoint splits."""

from copy import deepcopy

from scripts.prepare_template_invoice import prepare_records


def invoice(split="train", identifier="1", seller="Example Store"):
    payload = {
        "header": {"seller": seller, "invoice_date": "09/18/2015", "invoice_no": identifier},
        "items": [
            {
                "item_desc": "Desk",
                "item_qty": "2,00",
                "item_net_price": "10,00",
                "item_net_worth": "20,00",
                "item_vat": "10%",
                "item_gross_worth": "22,00",
            }
        ],
        "summary": {
            "total_net_worth": "$ 20,00",
            "total_vat": "$ 2,00",
            "total_gross_worth": "$ 22,00",
        },
    }
    text = f"Invoice {identifier}\nSeller: {seller}\n09/18/2015\nDesk 2,00 10,00 20,00 10% 22,00\n$ 20,00 $ 2,00 $ 22,00"
    return {"split": split, "source_id": split + ":" + identifier, "text": text, "data": payload}


def test_full_ocr_and_grounded_json_only_without_truncation():
    good = invoice()
    hallucinated = invoice(identifier="2")
    hallucinated["data"]["items"][0]["item_desc"] = "Never on invoice"
    missing = invoice(identifier="3")
    del missing["data"]["summary"]["total_vat"]
    long = invoice(identifier="4")
    long["text"] += "x" * 3000
    result, dropped = prepare_records(
        [good, hallucinated, missing, long], lambda m: sum(len(x["content"]) for x in m)
    )
    assert len(result["train"]) == 1
    assert good["text"] in result["train"][0][0]["question"]
    assert '"tax": "$ 2,00"' in result["train"][0][0]["answer"]
    assert dropped == {"ungrounded_value": 1, "missing_fields": 1, "over_token_limit": 1}


def test_original_test_wins_invoice_and_normalized_input_groups():
    train = invoice()
    test = invoice("test")
    test["text"] = test["text"].replace("\n", "   \n")
    valid = invoice("validation", "2")
    duplicate = deepcopy(valid)
    duplicate["split"] = "train"
    duplicate["source_id"] = "train:duplicate"
    duplicate["text"] = "  " + duplicate["text"].upper()
    result, dropped = prepare_records([train, valid, test, duplicate], lambda m: 10)
    assert not result["train"]
    assert len(result["validation"]) == len(result["test"]) == 1
    assert dropped["heldout_invoice"] == 2


def test_heldout_group_stays_reserved_when_heldout_row_is_invalid():
    train = invoice()
    test = invoice("test")
    test["data"]["summary"]["total_vat"] = "not grounded"
    result, dropped = prepare_records([train, test], lambda m: 10)
    assert not any(result.values())
    assert dropped == {"heldout_invoice": 1, "ungrounded_value": 1}
