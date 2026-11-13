#!/usr/bin/env python3
"""Low-level .docx helpers that work directly on the package XML (requires lxml).

Usage:
  python docx_xml_tools.py text IN.docx
      Numbered paragraphs (body order) with their text - use to find anchors.
  python docx_xml_tools.py merge-runs IN.docx [-o OUT.docx]
      Merge adjacent runs with identical formatting; drop rsid/proofErr noise so
      visible phrases become findable strings. Rendering is unchanged.
  python docx_xml_tools.py track-replace IN.docx --find OLD --replace NEW
                           [--author NAME] [--all] [-o OUT.docx]
      Replace text as a tracked change (<w:del> old + <w:ins> new). Only the
      changed words are marked; matches spanning differently formatted runs are
      reported and skipped.
  python docx_xml_tools.py comment IN.docx --find TEXT --text COMMENT
                           [--author NAME] [-o OUT.docx]
      Anchor a comment on the first occurrence of TEXT (falls back to the whole
      paragraph when the text spans formatting changes).
  python docx_xml_tools.py list-changes IN.docx
      List tracked insertions/deletions and comments.
  python docx_xml_tools.py accept-all IN.docx [-o OUT.docx]
      Accept every tracked change (ins/del/move/format, deleted paragraph marks
      and table rows) and produce a clean copy.

Without -o the input file is rewritten in place. Only the main document part is
edited by merge-runs/track-replace/comment; accept-all also cleans headers,
footers, footnotes and endnotes.
"""

from __future__ import annotations

import argparse
import copy
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
PARSER = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)


# ---------------------------------------------------------------- package io
class Package:
    def __init__(self, path: Path):
        self.path = path
        with zipfile.ZipFile(path) as zf:
            self.names = zf.namelist()
            self.parts = {n: zf.read(n) for n in self.names}
        self.trees: dict[str, etree._ElementTree] = {}

    def xml(self, name: str):
        if name not in self.trees:
            self.trees[name] = etree.ElementTree(etree.fromstring(self.parts[name], PARSER))
        return self.trees[name].getroot()

    def set_xml(self, name: str, root) -> None:
        self.trees[name] = etree.ElementTree(root)
        if name not in self.names:
            self.names.append(name)

    def save(self, out: Path) -> None:
        for name, tree in self.trees.items():
            self.parts[name] = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        tmp = Path(tempfile.mkstemp(suffix=".docx")[1])
        order = ["[Content_Types].xml"] + [n for n in self.names if n != "[Content_Types].xml"]
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in order:
                if not name.endswith("/"):
                    zf.writestr(name, self.parts[name])
        shutil.move(str(tmp), out)


def run_text(r) -> str:
    return "".join(t.text or "" for t in r if t.tag in (W + "t", W + "delText"))


def para_text(p) -> str:
    return "".join(run_text(r) for r in p.iter(W + "r") if r.getparent().tag != W + "del")


def rpr_key(r) -> bytes:
    rpr = r.find(W + "rPr")
    return etree.tostring(rpr, method="c14n") if rpr is not None else b""


def strip_noise(root) -> None:
    for el in list(root.iter(W + "proofErr", W + "lastRenderedPageBreak")):
        el.getparent().remove(el)
    for el in root.iter():
        for attr in list(el.attrib):
            if attr.startswith(W + "rsid"):
                del el.attrib[attr]


def is_text_only_run(r) -> bool:
    return all(c.tag in (W + "rPr", W + "t", W + "delText") for c in r)


def merge_runs(root) -> int:
    strip_noise(root)
    merged = 0
    for parent in root.iter():
        prev = None
        for child in list(parent):
            if child.tag == W + "r" and is_text_only_run(child):
                if prev is not None and rpr_key(prev) == rpr_key(child):
                    tag = W + "delText" if prev.find(W + "delText") is not None else W + "t"
                    text = run_text(prev) + run_text(child)
                    for t in [c for c in prev if c.tag in (W + "t", W + "delText")]:
                        prev.remove(t)
                    t = etree.SubElement(prev, tag)
                    t.text = text
                    t.set(XML_SPACE, "preserve")
                    parent.remove(child)
                    merged += 1
                    continue
                prev = child
            else:
                prev = None
    return merged


def make_run(template, text: str, deleted: bool = False):
    r = etree.Element(W + "r")
    rpr = template.find(W + "rPr")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = etree.SubElement(r, W + ("delText" if deleted else "t"))
    t.text = text
    t.set(XML_SPACE, "preserve")
    return r


def split_run(r, start: int, end: int):
    """Split a text-only run into (before, middle, after); returns the middle run."""
    text = run_text(r)
    parent, idx = r.getparent(), r.getparent().index(r)
    pieces = [(text[:start], False), (text[start:end], True), (text[end:], False)]
    middle = None
    parent.remove(r)
    for piece, is_mid in pieces:
        if piece or is_mid:
            new = make_run(r, piece)
            parent.insert(idx, new)
            idx += 1
            if is_mid:
                middle = new
    return middle


def find_in_runs(root, needle: str, first_only: bool):
    """Yield (run, start, end) for matches fully inside one text-only run."""
    for r in list(root.iter(W + "r")):
        if not is_text_only_run(r) or r.getparent().tag == W + "del":
            continue
        text = run_text(r)
        pos = text.find(needle)
        if pos >= 0:
            yield r, pos, pos + len(needle)
            if first_only:
                return


class Ids:
    def __init__(self, root):
        found = [int(v) for v in root.xpath("//@w:id", namespaces={"w": W_NS}) if str(v).lstrip("-").isdigit()]
        self.next = max(found, default=0) + 1

    def take(self) -> str:
        self.next += 1
        return str(self.next - 1)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- commands
def cmd_text(pkg: Package, _args) -> None:
    body = pkg.xml("word/document.xml").find(W + "body")
    for i, p in enumerate(body.iter(W + "p"), start=1):
        style = p.find(f"{W}pPr/{W}pStyle")
        label = f" [{style.get(W + 'val')}]" if style is not None else ""
        print(f"{i:4}{label} {para_text(p)}")


def cmd_merge_runs(pkg: Package, _args) -> bool:
    n = merge_runs(pkg.xml("word/document.xml"))
    print(f"Merged {n} runs")
    return True


def cmd_track_replace(pkg: Package, args) -> bool:
    root = pkg.xml("word/document.xml")
    merge_runs(root)
    ids, date, count = Ids(root), now_iso(), 0
    for r, s, e in list(find_in_runs(root, args.find, not args.all)):
        mid = split_run(r, s, e)
        parent, idx = mid.getparent(), mid.getparent().index(mid)
        parent.remove(mid)
        d = etree.Element(W + "del", {W + "id": ids.take(), W + "author": args.author, W + "date": date})
        d.append(make_run(mid, args.find, deleted=True))
        parent.insert(idx, d)
        if args.replace:
            ins = etree.Element(W + "ins", {W + "id": ids.take(), W + "author": args.author, W + "date": date})
            ins.append(make_run(mid, args.replace))
            parent.insert(idx + 1, ins)
        count += 1
    if not count:
        hits = [i for i, p in enumerate(root.iter(W + "p"), 1) if args.find in para_text(p)]
        msg = (f"text spans differently formatted runs in paragraph(s) {hits}; edit those runs manually"
               if hits else "text not found")
        print(f"No tracked replacement made: {msg}")
        return False
    print(f"Made {count} tracked replacement(s) by {args.author}")
    return True


def ensure_comments_part(pkg: Package):
    name = "word/comments.xml"
    if name in pkg.parts or name in pkg.trees:
        return pkg.xml(name)
    root = etree.Element(W + "comments", nsmap={"w": W_NS})
    pkg.set_xml(name, root)
    rels = pkg.xml("word/_rels/document.xml.rels")
    existing = {rel.get("Id") for rel in rels}
    rid = next(f"rId{i}" for i in range(1, 10000) if f"rId{i}" not in existing)
    etree.SubElement(rels, f"{{{REL_NS}}}Relationship", Id=rid, Type=COMMENTS_REL, Target="comments.xml")
    ct = pkg.xml("[Content_Types].xml")
    etree.SubElement(ct, f"{{{CT_NS}}}Override", PartName="/word/comments.xml", ContentType=COMMENTS_CT)
    return root


def cmd_comment(pkg: Package, args) -> bool:
    root = pkg.xml("word/document.xml")
    merge_runs(root)
    comments = ensure_comments_part(pkg)
    used = [int(c.get(W + "id")) for c in comments.findall(W + "comment")]
    # keep comment ids clear of every other annotation id (revisions, bookmarks)
    cid = str(max(max(used, default=-1), Ids(root).next - 1) + 1)
    match = next(find_in_runs(root, args.find, True), None)
    if match:
        r, s, e = match
        first = last = split_run(r, s, e)
        container = first.getparent()
    else:
        para = next((p for p in root.iter(W + "p") if args.find in para_text(p)), None)
        if para is None:
            print("Text not found; no comment added")
            return False
        runs = [c for c in para if c.tag in (W + "r", W + "ins", W + "del", W + "hyperlink")]
        if not runs:
            print("Paragraph has no runs to anchor on")
            return False
        first, last, container = runs[0], runs[-1], para
    container.insert(container.index(first), etree.Element(W + "commentRangeStart", {W + "id": cid}))
    end = etree.Element(W + "commentRangeEnd", {W + "id": cid})
    container.insert(container.index(last) + 1, end)
    ref_run = etree.Element(W + "r")
    etree.SubElement(ref_run, W + "commentReference", {W + "id": cid})
    container.insert(container.index(end) + 1, ref_run)

    initials = "".join(w[0] for w in args.author.split()[:3]).upper() or "R"
    c = etree.SubElement(comments, W + "comment",
                         {W + "id": cid, W + "author": args.author, W + "date": now_iso(), W + "initials": initials})
    for i, line in enumerate(args.text.split("\n")):
        p = etree.SubElement(c, W + "p")
        if i == 0:
            etree.SubElement(etree.SubElement(p, W + "r"), W + "annotationRef")
        t = etree.SubElement(etree.SubElement(p, W + "r"), W + "t")
        t.text = line
        t.set(XML_SPACE, "preserve")
    where = "exact text" if match else "whole paragraph"
    print(f"Added comment {cid} by {args.author} anchored on {where}")
    return True


def cmd_list_changes(pkg: Package, _args) -> None:
    root = pkg.xml("word/document.xml")
    for kind in ("ins", "del", "moveFrom", "moveTo"):
        for el in root.iter(W + kind):
            text = "".join(run_text(r) for r in el.iter(W + "r"))
            print(f"{kind:8} id={el.get(W + 'id')} author={el.get(W + 'author')} date={el.get(W + 'date')}: {text!r}")
    for kind in ("rPrChange", "pPrChange"):
        n = sum(1 for _ in root.iter(W + kind))
        if n:
            print(f"{kind}: {n} formatting change(s)")
    if "word/comments.xml" in pkg.parts:
        for c in pkg.xml("word/comments.xml").findall(W + "comment"):
            text = " ".join(para_text(p) for p in c.iter(W + "p"))
            print(f"comment  id={c.get(W + 'id')} author={c.get(W + 'author')}: {text!r}")


def accept_part(root) -> None:
    for tag in ("del", "moveFrom"):
        for el in list(root.iter(W + tag)):
            parent = el.getparent()
            if parent is not None and parent.tag in (W + "rPr", W + "trPr"):
                continue  # paragraph-mark / row markers handled below
            parent.remove(el)
    for tag in ("ins", "moveTo"):
        for el in list(root.iter(W + tag)):
            parent = el.getparent()
            if parent is not None and parent.tag in (W + "rPr", W + "trPr"):
                parent.remove(el)
                continue
            idx = parent.index(el)
            for child in list(el):
                parent.insert(idx, child)
                idx += 1
            parent.remove(el)
    for tag in ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
                "tblGridChange", "numberingChange", "moveFromRangeStart", "moveFromRangeEnd",
                "moveToRangeStart", "moveToRangeEnd"):
        for el in list(root.iter(W + tag)):
            el.getparent().remove(el)
    for tr in list(root.iter(W + "tr")):
        marker = tr.find(f"{W}trPr/{W}del")
        if marker is not None:
            tr.getparent().remove(tr)
    for p in list(root.iter(W + "p")):
        marker = p.find(f"{W}pPr/{W}rPr/{W}del")
        if marker is None:
            continue
        marker.getparent().remove(marker)
        nxt = p.getnext()
        has_content = any(any(c.tag != W + "rPr" for c in r) for r in p.iter(W + "r"))
        only_para_in_cell = p.getparent().tag == W + "tc" and len(p.getparent().findall(W + "p")) == 1
        if not has_content and not only_para_in_cell:
            p.getparent().remove(p)  # paragraph fully deleted
        elif nxt is not None and nxt.tag == W + "p":
            for child in [c for c in nxt if c.tag != W + "pPr"]:
                p.append(child)
            nxt.getparent().remove(nxt)


def cmd_accept_all(pkg: Package, _args) -> bool:
    targets = [n for n in pkg.names if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$", n)]
    for name in targets:
        accept_part(pkg.xml(name))
    print(f"Accepted all tracked changes in {len(targets)} part(s)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("text", "merge-runs", "list-changes", "accept-all", "track-replace", "comment"):
        sp = sub.add_parser(name)
        sp.add_argument("input")
        sp.add_argument("-o", "--output")
        if name == "track-replace":
            sp.add_argument("--find", required=True)
            sp.add_argument("--replace", default="")
            sp.add_argument("--author", default="Reviewer")
            sp.add_argument("--all", action="store_true")
        if name == "comment":
            sp.add_argument("--find", required=True)
            sp.add_argument("--text", required=True)
            sp.add_argument("--author", default="Reviewer")
    args = ap.parse_args()
    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        return 2
    pkg = Package(src)
    handler = {
        "text": cmd_text, "merge-runs": cmd_merge_runs, "list-changes": cmd_list_changes,
        "accept-all": cmd_accept_all, "track-replace": cmd_track_replace, "comment": cmd_comment,
    }[args.cmd]
    changed = handler(pkg, args)
    if changed:
        out = Path(args.output) if args.output else src
        pkg.save(out)
        print(f"Saved {out}")
    return 0 if changed is not False else 1


if __name__ == "__main__":
    sys.exit(main())
