# Copyright (c) Anthropic, PBC.
# Licensed under the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0).
# Adapted from Anthropic claude-code skills/pdf/scripts/fill_fillable_fields.py.
# Modified for CrabAgent: added post-write verification of field values and --flatten.
"""Fill AcroForm fields from a JSON list and verify the result.

Usage:
    python fill_fillable_fields.py <input.pdf> <field_values.json> <output.pdf> [--flatten]

field_values.json: [{"field_id": "...", "page": 1, "value": "..."}, ...]
(field_id/page as reported by extract_form_field_info.py; checkboxes take their
checked_value / unchecked_value, radio groups and choices one of their option values)

The output stays an interactive form unless --flatten is given. After writing,
the file is reopened and every value is compared with what was requested.
"""

import json
import sys

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__ when run in place
from extract_form_field_info import get_field_info  # noqa: E402


def fill_pdf_fields(input_pdf_path: str, fields_json_path: str, output_pdf_path: str, flatten: bool = False):
    with open(fields_json_path) as f:
        fields = json.load(f)
    fields_by_page = {}
    for field in fields:
        if "value" in field:
            field_id = field["field_id"]
            page = field["page"]
            if page not in fields_by_page:
                fields_by_page[page] = {}
            fields_by_page[page][field_id] = field["value"]

    reader = PdfReader(input_pdf_path)

    has_error = False
    field_info = get_field_info(reader)
    fields_by_ids = {f["field_id"]: f for f in field_info}
    for field in fields:
        existing_field = fields_by_ids.get(field["field_id"])
        if not existing_field:
            has_error = True
            print(f"ERROR: `{field['field_id']}` is not a valid field ID")
        elif field["page"] != existing_field["page"]:
            has_error = True
            print(f"ERROR: Incorrect page number for `{field['field_id']}` "
                  f"(got {field['page']}, expected {existing_field['page']})")
        else:
            if "value" in field:
                err = validation_error_for_field_value(existing_field, field["value"])
                if err:
                    print(err)
                    has_error = True
    if has_error:
        sys.exit(1)

    writer = PdfWriter(clone_from=reader)
    for page, field_values in fields_by_page.items():
        writer.update_page_form_field_values(
            writer.pages[page - 1], field_values, auto_regenerate=False, flatten=flatten
        )

    if flatten:
        writer.remove_annotations(subtypes="/Widget")
        writer.root_object.pop(NameObject("/AcroForm"), None)
    else:
        writer.set_need_appearances_writer(True)

    with open(output_pdf_path, "wb") as f:
        writer.write(f)

    verify(output_pdf_path, fields_by_page, flatten)


def verify(output_pdf_path: str, fields_by_page: dict, flatten: bool):
    """Reopen the written file and confirm the stored values match the request."""
    reader = PdfReader(output_pdf_path)
    if flatten:
        widgets = sum(
            1 for page in reader.pages for a in (page.get("/Annots") or [])
            if a.get_object().get("/Subtype") == "/Widget"
        )
        status = "OK" if widgets == 0 and "/AcroForm" not in reader.trailer["/Root"] else "CHECK"
        print(f"{status}: flattened output written to {output_pdf_path} ({widgets} widget annotations left). "
              "Render the pages to confirm the values are painted.")
        return
    stored = reader.get_fields() or {}
    problems = []
    for values in fields_by_page.values():
        for field_id, expected in values.items():
            actual = stored.get(field_id, {}).get("/V")
            if str(actual) != str(expected):
                problems.append(f"{field_id}: expected {expected!r}, found {actual!r}")
    total = sum(len(v) for v in fields_by_page.values())
    if problems:
        print(f"WARNING: {len(problems)} of {total} fields did not read back as written:")
        for p in problems:
            print(f"  {p}")
    else:
        print(f"OK: {total} fields filled and verified in {output_pdf_path}. "
              "Render the pages to check appearance (clipping, font size).")


def validation_error_for_field_value(field_info, field_value):
    field_type = field_info["type"]
    field_id = field_info["field_id"]
    if field_type == "checkbox":
        checked_val = field_info["checked_value"]
        unchecked_val = field_info["unchecked_value"]
        if field_value != checked_val and field_value != unchecked_val:
            return (f'ERROR: Invalid value "{field_value}" for checkbox field "{field_id}". '
                    f'The checked value is "{checked_val}" and the unchecked value is "{unchecked_val}"')
    elif field_type == "radio_group":
        option_values = [opt["value"] for opt in field_info["radio_options"]]
        if field_value not in option_values:
            return f'ERROR: Invalid value "{field_value}" for radio group field "{field_id}". Valid values are: {option_values}'
    elif field_type == "choice":
        choice_values = [opt["value"] for opt in field_info["choice_options"]]
        if field_value not in choice_values:
            return f'ERROR: Invalid value "{field_value}" for choice field "{field_id}". Valid values are: {choice_values}'
    return None


def monkeypatch_pydpf_method():
    from pypdf.constants import FieldDictionaryAttributes
    from pypdf.generic import DictionaryObject

    original_get_inherited = DictionaryObject.get_inherited

    def patched_get_inherited(self, key: str, default=None):
        result = original_get_inherited(self, key, default)
        if key == FieldDictionaryAttributes.Opt:
            if isinstance(result, list) and all(isinstance(v, list) and len(v) == 2 for v in result):
                result = [r[0] for r in result]
        return result

    DictionaryObject.get_inherited = patched_get_inherited


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--flatten"]
    if len(args) != 3:
        print("Usage: fill_fillable_fields.py [input pdf] [field_values.json] [output pdf] [--flatten]")
        sys.exit(1)
    monkeypatch_pydpf_method()
    fill_pdf_fields(args[0], args[1], args[2], flatten="--flatten" in sys.argv)
