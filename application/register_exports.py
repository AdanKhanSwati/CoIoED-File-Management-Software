"""Dependency-free full-register exports for OFFLINE TEST USE ONLY.
Official source workbook layout can only be matched after the master workbook has been supplied and mapped.
"""
from __future__ import annotations
import csv, io, zipfile, textwrap
from xml.sax.saxutils import escape
from datetime import date

HEADERS = ['S. #','CoIoED No.','Name/Parentage','CNIC / Status','Missing Date','Complainant & Address','District','Region','Current Status','Physical File Location']
WIDTHS = [4,12,25,20,14,38,18,17,20,22]

def register_data(cases):
    values=[]
    for i,c in enumerate(cases,1):
        vals=[i,c['case_no'],(' '.join(x for x in (c['name'],c['parentage']) if x)),c['cnic'] or 'Not provided',c['missing_date'],(str(c['complainant_name'] or '')+'; '+str(c['address'] or '')).strip('; '),c['district'],c['region'],c['status'],c['file_branch']]
        values.append([str(v or '') for v in vals])
    return HEADERS, values

def csv_bytes(headers, rows):
    out=io.StringIO();w=csv.writer(out);w.writerow(headers);w.writerows(rows)
    return b'\xef\xbb\xbf'+out.getvalue().encode('utf-8')

def xlsx_bytes(headers, rows):
    """Native XLSX, all identifiers explicitly stored as strings to preserve leading zeroes."""
    def col(i):
        a=''; i+=1
        while i:
            i,r=divmod(i-1,26);a=chr(65+r)+a
        return a
    def cell(r,j,val,style):
        v=str(val or '')
        if v[:1] in ('=','+','-','@'):v="'"+v
        v=''.join(ch for ch in v if ch in '\n\t\r' or ord(ch)>=32)
        return f'<c r="{col(j)}{r}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(v)}</t></is></c>'
    xml_rows=[]
    for r,record in enumerate([headers]+list(rows),1):
        st=1 if r==1 else 0
        xml_rows.append(f'<row r="{r}">'+''.join(cell(r,j,val,st) for j,val in enumerate(record))+'</row>')
    last=max(len(rows)+1,1);lastcol=col(len(headers)-1)
    sheet='''<?xml version="1.0" encoding="UTF-8"?>'''+f'''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView tabSelected="1" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'''+''.join(f'<col min="{i}" max="{i}" width="{v}" customWidth="1"/>' for i,v in enumerate((WIDTHS+[18]*max(0,len(headers)-len(WIDTHS)))[:len(headers)],1))+'</cols><sheetData>'+''.join(xml_rows)+f'</sheetData><autoFilter ref="A1:{lastcol}{last}"/><pageMargins left=".25" right=".25" top=".35" bottom=".35" header="0" footer="0"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'
    styles='''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="10"/><name val="Arial"/></font><font><b/><sz val="10"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDBEEE4"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf fontId="0" fillId="0" borderId="0" numFmtId="0"/></cellStyleXfs><cellXfs count="2"><xf fontId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>'''
    d=io.BytesIO()
    with zipfile.ZipFile(d,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        z.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Complete Case Register" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
        z.writestr('xl/styles.xml',styles)
    return d.getvalue()

def pdf_bytes(headers, rows):
    """Plain Latin-script PDF. Refuse lossy Urdu/non-Latin conversion."""
    for v in list(headers)+[c for row in rows for c in row]:
        try:str(v).encode('cp1252')
        except UnicodeEncodeError:raise ValueError('PDF exporter cannot safely render non-Latin case data; use approved Unicode renderer after system integration.')
    W,H=(1191,842) if len(headers)>10 else (842,595)
    pad=15;usable=W-2*pad
    if len(headers)>10:
        # Source master has 13 columns; keep all columns and data in A3 landscape.
        weights=[26,65,128,88,110,78,65,72,76,85,82,106,120][:len(headers)]
        if len(headers)>len(weights):weights.extend([90]*(len(headers)-len(weights)))
    else:weights=[25,62,130,86,68,150,70,67,85,69][:len(headers)]
    widths=[round(w*usable/sum(weights),2) for w in weights]
    pages=[];cmd=[]; y=H-20
    def cmdline(a):cmd.append(a+'\n')
    def esc(s):
        return ''.join(('\\'+c if c in '\\()' else c) for c in str(s)).replace('\n',' ')
    def text(x,y,v,size=7,bold=False):cmdline(f'BT /{"F2" if bold else "F1"} {size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({esc(v)}) Tj ET')
    def line(x1,y1,x2,y2):cmdline(f'.5 w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S')
    def rect(x1,y1,w,h):cmdline(f'.5 w {x1:.2f} {y1:.2f} {w:.2f} {h:.2f} re S')
    def wrap(s,w,size=6):
        cap=max(3,int(w/(size*.58)))
        return [p for z in str(s).split('\n') for p in (textwrap.wrap(z,cap,break_long_words=True,break_on_hyphens=False) or [''])]
    def headers_new():
        nonlocal y,cmd
        if cmd:pages.append(''.join(cmd));cmd=[]
        y=H-22
        text(pad,y,'COMMISSION OF INQUIRY ON ENFORCED DISAPPEARANCES - COMPLETE CASE REGISTER (PILOT)',10,True);y-=16
        text(pad,y,f'Exported: {date.today().isoformat()}  |  Total cases: {len(rows)}  |  Page {len(pages)+1}',7);y-=10
        drawrow(headers,True)
    def drawrow(row,head=False):
        nonlocal y
        size=6 if not head else 6.4;wr=[wrap(v,w-6,size) for v,w in zip(row,widths)]
        height=max(22 if head else 25, 8*max(len(a) for a in wr)+7)
        if height>H-95:raise ValueError('An individual row is too long to fit in PDF without splitting; review the record text.')
        if y-height<20 and not head:headers_new()
        rect(pad,y-height,sum(widths),height)
        x=pad
        for i,(w,lines) in enumerate(zip(widths,wr)):
            if i:line(x,y,x,y-height)
            for j,v in enumerate(lines):text(x+3,y-9-j*8,v,size,head or i==1)
            x+=w
        y-=height
    headers_new()
    for r in rows:drawrow(r)
    if cmd:pages.append(''.join(cmd))
    objects=[None]
    def add(s):objects.append(s);return len(objects)-1
    catalog=add('');pagesobj=add('');font=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');bold=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>')
    pageobjs=[]
    for body in pages:
        stream=body.encode('cp1252')
        content=add(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'endstream')
        pg=add(f'<< /Type /Page /Parent {pagesobj} 0 R /MediaBox [0 0 {W} {H}] /Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>')
        pageobjs.append(pg)
    objects[catalog]=f'<< /Type /Catalog /Pages {pagesobj} 0 R >>'
    objects[pagesobj]='<< /Type /Pages /Kids ['+' '.join(f'{p} 0 R' for p in pageobjs)+f'] /Count {len(pageobjs)} >>'
    buf=io.BytesIO();buf.write(b'%PDF-1.4\n');offsets=[0]
    for i,item in enumerate(objects[1:],1):
        offsets.append(buf.tell());buf.write(f'{i} 0 obj\n'.encode());buf.write(item if isinstance(item,bytes) else item.encode('ascii'));buf.write(b'\nendobj\n')
    start=buf.tell();buf.write(f'xref\n0 {len(objects)}\n0000000000 65535 f \n'.encode())
    for p in offsets[1:]:buf.write(f'{p:010d} 00000 n \n'.encode())
    buf.write(f'trailer\n<< /Size {len(objects)} /Root {catalog} 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
    return buf.getvalue()


def print_html(headers, rows):
    """Unicode-safe browser print view for PDF output when native Latin PDF cannot render source text."""
    from html import escape as h
    cells=lambda r,tag:''.join(f'<{tag}>{h(str(v or ""))}</{tag}>' for v in r)
    title='CoIoED Comprehensive Case Register - Print to PDF'
    out=['<!doctype html><html><head><meta charset="utf-8"><title>'+title+'</title>',
    '<style>@page{size:A3 landscape;margin:9mm}html,body{font-family:Arial,"Noto Sans",sans-serif;color:#152f25;font-size:8px}',
    'table{border-collapse:collapse;width:100%;table-layout:fixed;word-break:break-word}',
    'th,td{border:1px solid #777;padding:3px;vertical-align:top}th{background:#d9eae0}',
    'thead{display:table-header-group}tr{break-inside:avoid}button{margin:10px;background:#08784e;color:white;padding:10px;border:0}',
    '@media print{button,.alert{display:none}}</style></head><body>',
    '<div class="alert"><b>Unicode-safe print preview.</b> Choose Print - Save as PDF (A3 landscape). Printout retains source field values and ordering.</div>',
    '<button onclick="window.print()">Print / Save as PDF</button><h2>Commission of Inquiry on Enforced Disappearances</h2>',
    f'<p>Comprehensive Case Register - {len(rows)} source rows. Authorized offline copy only.</p>',
    '<table><thead><tr>'+cells(headers,'th')+'</tr></thead><tbody>']
    out.extend('<tr>'+cells(r,'td')+'</tr>' for r in rows)
    out.append('</tbody></table></body></html>')
    return ''.join(out).encode('utf-8')