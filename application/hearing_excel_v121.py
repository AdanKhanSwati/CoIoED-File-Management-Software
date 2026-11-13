"""Dependency-free official seven-column CoIoED schedule Excel exporter.

One worksheet, one calendar day per printed section; continuation pages repeat
ONLY the table header. No populated case cells are vertically bottom-aligned.
"""
from __future__ import annotations
import io, math, re, zipfile
from xml.sax.saxutils import escape

HEADERS=('S. #','CoIoED No.','Name/Parentage','Missing Date',
         'Complainant & Address','Officers to Attend','Special Instructions')
COL_WIDTHS=(5.7,11.6,24.5,13.5,31.5,34.0,36.5)
CHARS_PER_LINE=(5,10,24,12,31,33,35)
PRINT_POINTS=468.0
# Indexed style records in the standalone OOXML stylesheet below.
BODY=0;HEADER=1;TITLE=2;ANNEX=3;CENTER=4;CENTER_BOLD=5;OFFICER=6

def _safe(s):
    value=str('' if s is None else s)
    value=''.join(ch for ch in value if ch in '\t\n\r' or ord(ch)>=32)
    if value and value[0] in '=+-@':value="'"+value
    return escape(value, {'"':'&quot;'})

def _approx_lines(value,col):
    t=str(value or '').replace('\r\n','\n').replace('\r','\n')
    if not t:return 1
    cap=CHARS_PER_LINE[col]
    # Word-wrapping overestimation prevents the old clipping/bottom-anchoring.
    total=0
    for p in t.split('\n'):
        if not p:total+=1;continue
        words=p.split(' ');used=0;count=1
        for word in words:
            width=max(1,len(word))
            if used and used+1+width>cap:count+=1;used=width
            else:used+=width+(1 if used else 0)
            if used>cap:
                count+=math.ceil((used-cap)/cap);used=(used-1)%cap+1
        total+=count
    return max(1,total)

def _cell_height(values):
    count=max(_approx_lines(values[i],i) for i in range(7))
    return min(395.0,max(31.0,12.8*count+12.0))

class Sheet:
    def __init__(self):
        self.rows=[];self.merges=[];self.breaks=[];self.number=0;self.points=0
    def add(self,values,height=28,styles=None):
        self.number+=1;rowid=self.number;self.points+=height
        styles=styles or [BODY]*7
        body=[]
        for index,value in enumerate(values):
            if value is None:continue
            coordinate=chr(65+index)+str(rowid)
            body.append(f'<c r="{coordinate}" s="{styles[index]}" t="inlineStr"><is><t xml:space="preserve">{_safe(value)}</t></is></c>')
        self.rows.append(f'<row r="{rowid}" ht="{height:.2f}" customHeight="1">{"".join(body)}</row>')
        return rowid
    def merge(self,col_a,row_a,col_b,row_b):
        if row_a!=row_b or col_a!=col_b:
            self.merges.append(f'<mergeCell ref="{col_a}{row_a}:{col_b}{row_b}"/>')
    def break_page(self):
        if self.number:
            self.breaks.append(self.number) # Break *after* this row.
            self.points=0

def excel_bytes(sections):
    from notice_export import annex_lines,session_label
    sh=Sheet()
    if not sections:sections=[{'rows':[],'title':'SCHEDULE - INQUIRY HEARING','time':'','date':'','area':'',
                               'annex_label':'ANNEX-A','annex_prefix':'To:','letter_ref':'','letter_date':''}]
    for date_index,sec in enumerate(sections):
        if date_index:
            sh.break_page() # A new hearing date begins on a new printed page.
        if date_index==0:
            v='\n'.join(annex_lines(sec))
            rr=sh.add([None,None,None,None,v,None,None],50,[BODY,BODY,BODY,BODY,ANNEX,BODY,BODY]);sh.merge('E',rr,'G',rr)
        for title in (sec.get('title') or 'SCHEDULE - INQUIRY HEARING',
                      session_label(sec.get('time',''),sec.get('date','')),
                      '('+sec.get('area','')+')'):
            rr=sh.add([title,None,None,None,None,None,None],22,[TITLE]+[BODY]*6)
            sh.merge('A',rr,'G',rr)
        sh.add(HEADERS,29,[HEADER]*7)
        for row in sec.get('rows',[]):
            if isinstance(row,dict) and row.get('group'):
                members=row['members'];n=len(members)
                if n not in (2,3):raise ValueError('Combined hearing entry must contain 2 or 3 people.')
                same_date=len({x[2] for x in members})==1
                # Individual person cells occupy one subrow each; other fields span the group.
                member_heights=[max(32,12.8*max(_approx_lines(m[j],j+1) for j in range(3))+11) for m in members]
                shared=['', '', '', '', row.get('address',''),row.get('officers',''),row.get('instructions','')]
                needed=max(_approx_lines(shared[i],i) for i in (4,5,6))*12.8+13
                deficit=max(0,needed-sum(member_heights))
                for j in range(n):member_heights[j]+=deficit/n
                total=sum(member_heights)
                if sh.points+total>PRINT_POINTS and sh.points>80:
                    sh.break_page();sh.add(HEADERS,29,[HEADER]*7)
                first=sh.number+1
                for index,m in enumerate(members):
                    vals=[row['serial'] if index==0 else None,m[0],m[1],
                          m[2] if index==0 or not same_date else None,
                          row.get('address','') if index==0 else None,
                          row.get('officers','') if index==0 else None,
                          row.get('instructions','') if index==0 else None]
                    styles=[CENTER,CENTER_BOLD,BODY,BODY,BODY,OFFICER,BODY]
                    sh.add(vals,min(407.0,member_heights[index]),styles)
                last=sh.number
                for col in ('A','E','F','G')+ (('D',) if same_date else ()):
                    sh.merge(col,first,col,last)
            else:
                values=[str(v or '') for v in row]
                if len(values)!=7:raise ValueError('Export case row must have seven fields')
                needed=_cell_height(values)
                if sh.points+needed>PRINT_POINTS and sh.points>80:
                    sh.break_page();sh.add(HEADERS,29,[HEADER]*7)
                sh.add(values,needed,[CENTER,CENTER_BOLD,BODY,BODY,BODY,OFFICER,BODY])
    n=sh.number
    colxml=''.join(f'<col min="{i}" max="{i}" width="{v:.2f}" customWidth="1"/>' for i,v in enumerate(COL_WIDTHS,1))
    merged=f'<mergeCells count="{len(sh.merges)}">{"".join(sh.merges)}</mergeCells>' if sh.merges else ''
    breakxml=''
    if sh.breaks:
        breakxml=f'<rowBreaks count="{len(sh.breaks)}" manualBreakCount="{len(sh.breaks)}">'+''.join(f'<brk id="{i}" man="1" max="16383"/>' for i in sh.breaks)+'</rowBreaks>'
    ws=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
       <sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>
       <dimension ref="A1:G{n}"/>
       <sheetViews><sheetView showGridLines="0" zoomScale="85" workbookViewId="0"/></sheetViews>
       <sheetFormatPr defaultRowHeight="16"/><cols>{colxml}</cols>
       <sheetData>{''.join(sh.rows)}</sheetData>{merged}
       <printOptions horizontalCentered="1" gridLines="0" headings="0"/>
       <pageMargins left="0.24" right="0.24" top="0.34" bottom="0.34" header="0" footer="0"/>
       <pageSetup paperSize="1" orientation="landscape" fitToWidth="1" fitToHeight="0" usePrinterDefaults="0"/>
       <headerFooter><oddHeader></oddHeader><oddFooter></oddFooter></headerFooter>
       {breakxml}
    </worksheet>'''
    # No formula cells, no hidden fields. Content is editable and top aligned.
    styles='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <fonts count="3"><font><sz val="9"/><name val="Aptos"/></font><font><b/><sz val="9"/><name val="Aptos"/></font><font><b/><sz val="10"/><name val="Aptos"/></font></fonts>
    <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE5EBE7"/></patternFill></fill></fills>
    <borders count="2"><border/><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border></borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="7">
      <xf fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
      <xf fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
      <xf fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
      <xf fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1"/></xf>
      <xf fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="top" wrapText="1"/></xf>
      <xf fontId="1" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="top" wrapText="1"/></xf>
      <xf fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
    </cellXfs></styleSheet>'''
    contenttypes='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    workbook=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Hearing Schedule" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">'Hearing Schedule'!$A$1:$G${n}</definedName></definedNames></workbook>'''
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',contenttypes)
        z.writestr('_rels/.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml',workbook)
        z.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        z.writestr('xl/styles.xml',styles)
        z.writestr('xl/worksheets/sheet1.xml',ws)
    return out.getvalue()