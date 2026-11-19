"""spreadsheet_read / spreadsheet_export: values plus the formatting people use (colours, bold, notes)."""
from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest

import crabagent.core  # noqa: F401  (registers the built-in tools)
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import spreadsheet as tool
from crabagent.core.agent.tools.registry import registry
from crabagent.core.database import DEFAULT_AGENTS
from crabagent.core.office import spreadsheet as sheets
from crabagent.core.office.spreadsheet import SpreadsheetError, color_name, read_workbook

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rels(*items: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{REL}/{t}" Target="{target}"/>' for i, t, target in items)
    return f'<Relationships xmlns="{PKG}">{body}</Relationships>'


SHARED = ["Cliente", "Valor", "Status", "ACME", "pendente", None, "ok", "Data", "Gama"]


def _shared_strings() -> str:
    items = []
    for text in SHARED:
        if text is None:  # rich text: two runs
            items.append("<si><r><t>Beta</t></r><r><t xml:space=\"preserve\"> Ltda</t></r></si>")
        else:
            items.append(f"<si><t>{text}</t></si>")
    return f'<sst xmlns="{MAIN}">{"".join(items)}</sst>'


STYLES = f"""<styleSheet xmlns="{MAIN}">
<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>
<fonts count="3"><font><sz val="11"/></font><font><b/><sz val="11"/></font><font><strike/><color rgb="FFFF0000"/>
 </font></fonts>
<fills count="5">
 <fill><patternFill patternType="none"/></fill>
 <fill><patternFill patternType="gray125"/></fill>
 <fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor theme="4" tint="0.5999938962981048"/></patternFill></fill>
 <fill><patternFill patternType="solid"><fgColor indexed="13"/></patternFill></fill>
</fills>
<cellXfs count="7">
 <xf numFmtId="0" fontId="0" fillId="0"/><xf numFmtId="0" fontId="1" fillId="0"/>
 <xf numFmtId="0" fontId="0" fillId="2"/><xf numFmtId="0" fontId="0" fillId="3"/>
 <xf numFmtId="0" fontId="2" fillId="4"/><xf numFmtId="164" fontId="0" fillId="0"/>
 <xf numFmtId="14" fontId="0" fillId="0"/>
</cellXfs></styleSheet>"""

THEME = """<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements>
<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2>
<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="ED7D31"/></a:accent1>
<a:accent2><a:srgbClr val="4472C4"/></a:accent2></a:clrScheme></a:themeElements></a:theme>"""

SHEET1 = f"""<worksheet xmlns="{MAIN}" xmlns:r="{REL}">
<cols><col min="4" max="4" hidden="1" width="0"/></cols>
<sheetData>
<row r="1"><c r="A1" t="s" s="1"><v>0</v></c><c r="B1" t="s" s="1"><v>1</v></c><c r="C1" t="s" s="1"><v>2</v></c>
 <c r="D1" t="s" s="1"><v>7</v></c></row>
<row r="2"><c r="A2" t="s" s="2"><v>3</v></c><c r="B2" s="2"><v>1068.5</v></c><c r="C2" t="s" s="2"><v>4</v></c></row>
<row r="3"><c r="A3" t="s" s="3"><v>5</v></c><c r="B3"><f>B2*2</f><v>2137</v></c><c r="C3" t="b" s="4"><v>1</v></c>
 <c r="D3" s="6"><v>45597.5</v></c><c r="E3" s="5"><v>45597</v></c></row>
<row r="4" hidden="1"><c r="A4" t="inlineStr"><is><t>Delta</t></is></c><c r="B4" t="e"><v>#DIV/0!</v></c></row>
<row r="5" s="2" customFormat="1"><c r="A5" t="s"><v>8</v></c></row>
<row r="7"><c r="A7" t="s"><v>6</v></c></row>
<row r="10"><c r="A10" t="str"><v>Total</v></c></row>
</sheetData>
<mergeCells count="1"><mergeCell ref="A10:B10"/></mergeCells>
<conditionalFormatting sqref="B2:B7"><cfRule type="cellIs" dxfId="0" priority="1" operator="greaterThan">
<formula>1000</formula></cfRule></conditionalFormatting>
</worksheet>"""

COMMENTS = f"""<comments xmlns="{MAIN}"><authors><author>Ana</author></authors><commentList>
<comment ref="A7" authorId="0"><text><r><t>Ana:</t></r><r><t xml:space="preserve"> conferir valor</t></r></text>
 </comment>
<comment ref="F9" authorId="0"><text><t>nota numa célula vazia</t></text></comment>
</commentList></comments>"""

SHEET2 = f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>'


def make_xlsx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("_rels/.rels", _rels(("rId1", "officeDocument", "xl/workbook.xml")))
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><workbookPr/><sheets>'
                   '<sheet name="Faturas" sheetId="1" r:id="rId1"/>'
                   '<sheet name="Escondida" sheetId="2" state="hidden" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", _rels(
            ("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "worksheet", "worksheets/sheet2.xml"),
            ("rId3", "styles", "styles.xml"), ("rId4", "sharedStrings", "sharedStrings.xml"),
            ("rId5", "theme", "theme/theme1.xml"),
        ))
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/theme/theme1.xml", THEME)
        z.writestr("xl/sharedStrings.xml", _shared_strings())
        z.writestr("xl/worksheets/sheet1.xml", SHEET1)
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", _rels(("rId1", "comments", "../comments1.xml")))
        z.writestr("xl/comments1.xml", COMMENTS)
        z.writestr("xl/worksheets/sheet2.xml", SHEET2)
    return path


ODS_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"'
)

ODS_STYLES = f"""<office:document-styles {ODS_NS}><office:styles>
<style:style style:name="Default" style:family="table-cell"/>
<style:style style:name="Destaque" style:family="table-cell" style:parent-style-name="Default">
 <style:table-cell-properties fo:background-color="#c6efce"/>
 <style:text-properties fo:font-weight="bold" fo:color="#006100"/></style:style>
</office:styles></office:document-styles>"""

ODS_CONTENT = f"""<office:document-content {ODS_NS}><office:automatic-styles>
<style:style style:name="ta1" style:family="table"><style:table-properties table:display="true"/></style:style>
<style:style style:name="ta2" style:family="table"><style:table-properties table:display="false"/></style:style>
<style:style style:name="ce1" style:family="table-cell" style:parent-style-name="Destaque"/>
<style:style style:name="ce2" style:family="table-cell" style:parent-style-name="Default">
 <style:table-cell-properties fo:background-color="#ffc7ce"/></style:style>
<style:style style:name="ce3" style:family="table-cell"><style:table-cell-properties fo:background-color="transparent"/>
 <style:text-properties fo:color="#ff0000" style:text-line-through-style="solid" fo:font-style="italic"/></style:style>
</office:automatic-styles><office:body><office:spreadsheet>
<table:table table:name="Faturas" table:style-name="ta1">
 <table:table-column table:number-columns-repeated="2" table:default-cell-style-name="Default"/>
 <table:table-column table:visibility="collapse" table:default-cell-style-name="ce2"/>
 <table:table-column table:number-columns-repeated="1021"/>
 <table:table-header-rows><table:table-row>
  <table:table-cell table:style-name="ce1" office:value-type="string"><text:p>Cliente</text:p></table:table-cell>
  <table:table-cell table:style-name="ce1" office:value-type="string"><text:p>Valor</text:p></table:table-cell>
 </table:table-row></table:table-header-rows>
 <table:table-row>
  <table:table-cell table:style-name="ce2" office:value-type="string"><text:p>ACME<text:s text:c="2"/>SA</text:p>
   </table:table-cell>
  <table:table-cell table:style-name="ce2" office:value-type="currency" office:value="1068.5">
   <text:p>R$ 1.068,50</text:p></table:table-cell>
  <table:table-cell office:value-type="percentage" office:value="0.15"><text:p>15%</text:p></table:table-cell>
 </table:table-row>
 <table:table-row table:visibility="collapse">
  <table:table-cell office:value-type="date" office:date-value="2024-11-01"><text:p>01/11/2024</text:p>
   </table:table-cell>
  <table:table-cell office:value-type="time" office:time-value="PT12H30M00S"><text:p>12:30</text:p></table:table-cell>
  <table:table-cell table:style-name="ce3" office:value-type="boolean" office:boolean-value="true">
   <text:p>VERDADEIRO</text:p></table:table-cell>
 </table:table-row>
 <table:table-row table:number-rows-repeated="2">
  <table:table-cell office:value-type="float" office:value="7" table:number-columns-repeated="2">
   <text:p>7</text:p></table:table-cell>
 </table:table-row>
 <table:table-row>
  <table:table-cell table:number-columns-spanned="2" office:value-type="string" table:formula="of:=SUM([.A4:.A5])">
   <text:p>Total</text:p><office:annotation><dc:creator>Ana</dc:creator><text:p>conferir</text:p></office:annotation>
  </table:table-cell><table:covered-table-cell/>
 </table:table-row>
 <table:table-row table:number-rows-repeated="1048570"><table:table-cell table:number-columns-repeated="1024"/>
  </table:table-row>
</table:table>
<table:table table:name="Oculta" table:style-name="ta2"><table:table-column/>
 <table:table-row><table:table-cell office:value-type="float" office:value="1"><text:p>1</text:p>
  </table:table-cell></table:table-row>
</table:table>
</office:spreadsheet></office:body></office:document-content>"""


def make_ods(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", ODS_CONTENT)
        z.writestr("styles.xml", ODS_STYLES)
    return path


def _cell(book, row: int, col: int, sheet: int = 0):
    return book.sheets[sheet].cells[row][col]


# ── the reader ───────────────────────────────────────────────────────────


class TestXlsx:
    def test_values_of_every_kind(self, tmp_path: Path):
        book = read_workbook(str(make_xlsx(tmp_path / "f.xlsx")))
        assert [s.name for s in book.sheets] == ["Faturas", "Escondida"]
        assert [s.hidden for s in book.sheets] == [False, True]
        assert _cell(book, 2, 1).value == "ACME"
        assert _cell(book, 2, 2).value == 1068.5
        assert _cell(book, 3, 1).value == "Beta Ltda"  # rich text runs joined
        assert _cell(book, 3, 2).value == 2137 and _cell(book, 3, 2).formula == "=B2*2"
        assert _cell(book, 3, 3).value is True
        assert _cell(book, 3, 4).value == "2024-11-01 12:00:00"  # built-in date format 14
        assert _cell(book, 3, 5).value == "2024-11-01"  # custom dd/mm/yyyy
        assert _cell(book, 4, 1).value == "Delta" and _cell(book, 4, 2).value == "#DIV/0!"
        assert _cell(book, 10, 1).value == "Total"

    def test_formatting(self, tmp_path: Path):
        book = read_workbook(str(make_xlsx(tmp_path / "f.xlsx")))
        sheet = book.sheets[0]
        assert _cell(book, 1, 1).bold
        assert _cell(book, 2, 1).fill == "#FFC7CE"  # direct RGB
        assert _cell(book, 3, 1).fill == "#F8CBAD"  # theme accent1 (#ED7D31), lighter 60%
        c3 = _cell(book, 3, 3)
        assert (c3.fill, c3.color, c3.strike) == ("#FFFF00", "#FF0000", True)  # palette index 13
        assert sheet.row_fills == {5: "#FFC7CE"}  # whole-row style
        assert sheet.hidden_rows == {4} and sheet.hidden_cols == {4}
        assert sheet.merged == ["A10:B10"] and sheet.conditional_formats == ["B2:B7"]
        assert _cell(book, 7, 1).comment == "Ana: conferir valor"
        assert _cell(book, 9, 6).comment == "nota numa célula vazia" and _cell(book, 9, 6).value is None


class TestOds:
    def test_values_styles_and_structure(self, tmp_path: Path):
        book = read_workbook(str(make_ods(tmp_path / "f.ods")))
        sheet = book.sheets[0]
        assert [(s.name, s.hidden) for s in book.sheets] == [("Faturas", False), ("Oculta", True)]
        header = _cell(book, 1, 1)
        assert (header.value, header.fill, header.color, header.bold) == ("Cliente", "#C6EFCE", "#006100", True)
        assert _cell(book, 2, 1).value == "ACME  SA"  # <text:s text:c="2"/>
        assert (_cell(book, 2, 2).value, _cell(book, 2, 2).fill) == (1068.5, "#FFC7CE")
        assert (_cell(book, 2, 3).value, _cell(book, 2, 3).fill) == (0.15, "#FFC7CE")  # column default style
        assert [_cell(book, 3, c).value for c in (1, 2, 3)] == ["2024-11-01", "12:30:00", True]
        c3 = _cell(book, 3, 3)
        assert (c3.fill, c3.color, c3.italic, c3.strike) == (None, "#FF0000", True, True)
        assert [_cell(book, r, c).value for r in (4, 5) for c in (1, 2)] == [7, 7, 7, 7]  # repeated rows/cols
        total = _cell(book, 6, 1)
        assert (total.value, total.formula, total.comment) == ("Total", "=SUM([.A4:.A5])", "conferir")
        assert sheet.merged == ["A6:B6"]
        assert sheet.hidden_rows == {3} and 3 in sheet.hidden_cols
        assert (sheet.max_row, sheet.max_col) == (6, 3)  # the million empty trailing rows are ignored


class TestCsvAndOtherFormats:
    def test_semicolon_cp1252_csv(self, tmp_path: Path):
        path = tmp_path / "dados.csv"
        path.write_bytes('Nome;Valor;Obs\r\nJoão;1.068,50;"linha1\nlinha2"\r\nAção;2;\r\n'.encode("cp1252"))
        book = read_workbook(str(path))
        assert book.kind == "csv" and "cp1252" in book.notes[0] and "semicolon" in book.notes[0]
        assert _cell(book, 2, 1).value == "João" and _cell(book, 2, 2).value == "1.068,50"
        assert _cell(book, 2, 3).value == "linha1\nlinha2" and _cell(book, 3, 1).value == "Ação"

    def test_old_xls_without_libreoffice_says_what_to_do(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(sheets.shutil, "which", lambda name: None)
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)
        with pytest.raises(SpreadsheetError, match="save it as .xlsx or .ods"):
            read_workbook(str(path))

    def test_password_protected_xlsx_is_named_as_such(self, tmp_path: Path):
        path = tmp_path / "locked.xlsx"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)
        with pytest.raises(SpreadsheetError, match="password-protected"):
            read_workbook(str(path))

    def test_colour_names(self):
        assert [color_name(c) for c in ("#FFC7CE", "#C6EFCE", "#FFEB9C", "#FFFF00", "#D9D9D9", "#0070C0")] == [
            "light red", "light green", "light yellow", "yellow", "light gray", "blue",
        ]


# ── the tools ────────────────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(workspace=tmp_path)


class TestSpreadsheetRead:
    def test_overview_summarises_colours_and_structure(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        out = tool.spreadsheet_read("f.xlsx", context=ctx)
        assert 'Sheet 1 "Faturas"' in out and 'Sheet 2 "Escondida" [hidden sheet]' in out
        assert "header (row 1): A Cliente | B Valor | C Status | D Data" in out
        assert "rows filled #FFC7CE light red (2): 2, 5" in out
        assert "single cells filled #FFFF00 yellow: 1" in out
        assert "hidden rows (1): 4" in out and "hidden columns: D" in out
        assert "bold rows (1): 1" in out and "merged (1): A10:B10" in out
        assert "conditional formatting on B2:B7" in out and "formulas: 1 cells" in out
        assert "notes/comments (2)" in out

    def test_rows_show_values_with_their_formatting(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        out = tool.spreadsheet_read("f.xlsx", mode="rows", formulas=True, context=ctx)
        lines = {line.split(" ", 1)[0].rstrip(":"): line for line in out.splitlines()}
        assert lines["2"].startswith("2 [fill #FFC7CE light red]: A=ACME | B=1068.5 | C=pendente")
        assert "A=Beta Ltda {fill #F8CBAD light orange}" in lines["3"]
        assert "B=2137 {=B2*2}" in lines["3"]
        assert "C=TRUE {fill #FFFF00 yellow, color #FF0000 red, strike-through}" in lines["3"]
        assert lines["4"].startswith("4 [hidden]: A=Delta")
        assert 'A=ok {note: "Ana: conferir valor"}' in lines["7"]
        assert out.splitlines()[1].startswith("header (row 1): A Cliente")

    def test_rows_filtered_by_colour_and_paged(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        red = tool.spreadsheet_read("f.xlsx", mode="rows", fill="red", context=ctx)
        assert 'matches "red": 2' in red
        assert [line.split(" ")[0] for line in red.splitlines()[2:]] == ["2", "5"]
        assert "rows whose fill matches \"none\": 4" in tool.spreadsheet_read("f.xlsx", mode="rows", fill="none",
                                                                             context=ctx)
        page = tool.spreadsheet_read("f.xlsx", mode="rows", max_rows=2, columns="A:B", context=ctx)
        assert "Showing 2 of 6 rows. Next page: start_row=4." in page
        assert "C=" not in page

    def test_sheet_choice_and_errors(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        assert "A=1" in tool.spreadsheet_read("f.xlsx", mode="rows", sheet="escondida", header_row=0, context=ctx)
        assert "A=1" in tool.spreadsheet_read("f.xlsx", mode="rows", sheet="2", header_row=0, context=ctx)
        assert 'No sheet called "Nope"' in tool.spreadsheet_read("f.xlsx", mode="rows", sheet="Nope", context=ctx)
        assert "File not found" in tool.spreadsheet_read("missing.xlsx", context=ctx)

    def test_the_same_table_reads_the_same_from_ods(self, tmp_path: Path, ctx):
        make_ods(tmp_path / "f.ods")
        out = tool.spreadsheet_read("f.ods", mode="rows", context=ctx)
        assert "2 [fill #FFC7CE light red]: A=ACME  SA | B=1068.5 | C=0.15" in out
        assert "3 [hidden]: A=2024-11-01 | B=12:30:00 | C=TRUE {color #FF0000 red, italic, strike-through}" in out


class TestSpreadsheetExport:
    def test_export_turns_formatting_into_columns(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        result = tool.spreadsheet_export("f.xlsx", "out/f.csv", context=ctx)
        assert "Wrote 6 rows" in result and "#FFC7CE light red: 2" in result
        rows = list(csv.DictReader((tmp_path / "out/f.csv").open(encoding="utf-8")))
        assert list(rows[0])[:11] == ["row", "fill", "fill_name", "text_color", "bold", "hidden", "cell_formats",
                                      "Cliente", "Valor", "Status", "Data"]
        by_row = {r["row"]: r for r in rows}
        row2 = by_row["2"]
        assert (row2["fill_name"], row2["Cliente"], row2["Valor"]) == ("light red", "ACME", "1068.5")
        assert by_row["4"]["hidden"] == "yes"
        assert by_row["5"]["fill"] == "#FFC7CE"
        assert "C: fill #FFFF00 yellow, color #FF0000 red, strike-through" in by_row["3"]["cell_formats"]
        assert by_row["3"]["Status"] == "TRUE"

    def test_export_never_overwrites_by_accident(self, tmp_path: Path, ctx):
        make_xlsx(tmp_path / "f.xlsx")
        (tmp_path / "f.csv").write_text("keep me")
        assert "already exists" in tool.spreadsheet_export("f.xlsx", "f.csv", context=ctx)
        assert (tmp_path / "f.csv").read_text() == "keep me"
        assert "is the spreadsheet itself" in tool.spreadsheet_export("f.xlsx", "f.xlsx", overwrite=True, context=ctx)
        assert "Wrote" in tool.spreadsheet_export("f.xlsx", "f.csv", overwrite=True, context=ctx)


def test_both_tools_are_registered_and_export_follows_write_permission():
    assert registry.get("spreadsheet_read") is not None and registry.get("spreadsheet_export") is not None
    for agent in DEFAULT_AGENTS:
        perms = agent.get("tool_permissions", {})
        assert perms.get("spreadsheet_read") != "deny", agent["name"]
        if perms.get("write") == "deny":
            assert perms.get("spreadsheet_export") == "deny", agent["name"]
        else:
            assert perms.get("spreadsheet_export") != "deny", agent["name"]
