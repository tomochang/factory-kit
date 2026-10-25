"""Minimal xlsx reader and writer (first sheet, strings and numbers only), standard library only."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _first_sheet(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheet = wb.find(M + "sheets/" + M + "sheet")
    rid = sheet.get(R + "id") if sheet is not None else None
    if rid and "xl/_rels/workbook.xml.rels" in z.namelist():
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            if rel.get("Id") == rid:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    return "xl/worksheets/sheet1.xml"


def read_rows(data):
    """Rows of the first sheet as lists of strings. Raises ValueError if data is not an xlsx."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
                shared.append("".join(t.text or "" for t in si.iter(M + "t")))
        root = ET.fromstring(z.read(_first_sheet(z)))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ValueError("not an xlsx: %s" % e)
    out = []
    for r in root.iter(M + "row"):
        cells = {}
        for c in r.findall(M + "c"):
            t = c.get("t")
            v = c.find(M + "v")
            if t == "s":
                val = shared[int(v.text)] if v is not None else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(M + "t"))
            else:
                val = v.text if v is not None and v.text is not None else ""
            cells[_col(c.get("r"))] = val
        if cells:
            out.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return out


def _ref(i, j):
    s, n = "", j + 1
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return "%s%d" % (s, i + 1)


def make_xlsx(rows):
    """A small valid xlsx with the rows as inline strings (used for planted fixtures and the canary)."""
    body = []
    for i, row in enumerate(rows):
        cells = "".join('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                        % (_ref(i, j), escape(str(v))) for j, v in enumerate(row))
        body.append('<row r="%d">%s</row>' % (i + 1, cells))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="%s"><sheetData>%s'
             '</sheetData></worksheet>' % (M[1:-1], "".join(body)))
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.'
        'org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName='
        '"/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+'
        'xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.'
        'org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="%s" xmlns:r="%s"><sheets><sheet '
        'name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>' % (M[1:-1], R[1:-1]),
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
        'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()
