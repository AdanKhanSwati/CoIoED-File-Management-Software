"""Dependency-free, local-only PILOT hearing notice exporters.
No case data, metadata or media leaves the host. Plain Latin PDF fonts are a known
pilot limitation; official issuance requires approved Unicode PDF renderer.
"""
import io, zipfile, html, textwrap, json, math, re
from xml.sax.saxutils import escape
from datetime import date,datetime

COLUMNS=['S. #','CoIoED No.','Name/Parentage','Missing Date','Complainant & Address','Officers to Attend','Special Instructions']
# Derived from supplied 792 x 612 pt landscape notice, nominal widths in points.
WIDTHS=[27,42,74,53,163,205,210]


def district_caption(value):
    """Format manually entered districts: A, B & C. Keep a single district unchanged."""
    value=str(value or '').strip()
    parts=[p.strip() for p in re.split(r'\s*(?:,|\s+&\s+)\s*',value) if p.strip()]
    if len(parts)<2:return value
    if len(parts)==2:return ' & '.join(parts)
    return ', '.join(parts[:-1])+' & '+parts[-1]


def session_label(time,date_string):
    """Case-preserving clock time, standardized lowercase 'onwards on' wording."""
    time=str(time or '').strip()
    time=re.sub(r'(?i)\s+onwards?(?:\s+on)?\s*$', '', time).strip()
    return f'({time} onwards on {date_label(date_string)})'


def annex_lines(sec):
    return [sec['annex_label'], sec['annex_prefix']+' '+sec['letter_ref'], 'dated '+sec['letter_date']]


def date_label(s):
    try:
        d=date.fromisoformat(s)
        suffix='th' if 11<=d.day<=13 else {1:'st',2:'nd',3:'rd'}.get(d.day%10,'th')
        return f'{d.strftime("%A")} {d.day}{suffix} {d.strftime("%B %Y")}'
    except ValueError: return s

def display_date(s):
    if not s:return ''
    try:return date.fromisoformat(s).strftime('%d/%m/%Y')
    except ValueError:return str(s)

def cnic_display(snapshot):
    """Render the Comprehensive List's CNIC cell inline in the schedule.

    An unannotated identifier is the missing person's CNIC, as specified by
    Inquiry Branch. If the source value includes a relative's label (e.g.
    "Son CNIC" / "Brother CNIC"), preserve that label instead of adding an
    independent ownership/status line. Existing explicitly saved relationship
    metadata is used only when the CNIC value itself has no annotation.
    Never rewrite or mutate the underlying source register.
    """
    value=str(snapshot.get('cnic') or '').strip()
    if not value:return ''
    # The Comprehensive List may already say "12345-1234567-1 Son CNIC".
    # Keep its original wording; only collapse whitespace for readable print.
    visual=' '.join(value.split())
    if re.search(r'[A-Za-z]',visual):
        return visual
    relationship=' '.join(str(snapshot.get('cnic_relation') or '').split())
    if snapshot.get('cnic_owner')=='Relative / complainant' and relationship:
        return visual+' '+relationship+' CNIC'
    # No annotation means the number belongs to the missing person (MP).
    return 'CNIC: '+visual


def compact_attendees(values):
    """One numbered entry per non-empty officer, regardless of original slot count."""
    return [str(v).strip() for v in (values or []) if str(v or '').strip()]


def paired_attendees(values):
    """Paired rows, numbered in reading order, with no blank number labels."""
    people=compact_attendees(values)
    return [(f'{i+1}. {people[i]}', f'{i+2}. {people[i+1]}' if i+1<len(people) else '')
            for i in range(0,len(people),2)]


def notice_data(notice,days):
    """Both exporters consume an identical snapshot, including saved special instructions.
    Existing notice records may store any number of attendee slots, including blank slots.
    """
    def fld(d,k):return str(d.get(k) or '')
    sections=[]
    for data in days:
        day=data['day']; rows=[]; officer_pairs=[]
        serial=0;seen=set()
        items=data['items']
        for item in items:
            gid=item.get('group_id')
            if gid and gid in seen:continue
            serial+=1
            cluster=[it for it in items if it.get('group_id')==gid] if gid else [item]
            if gid and len(cluster) in (2,3) and item.get('group'):
                seen.add(gid)
                group=item['group']
                members=[]
                for member in cluster:
                    s=member['snapshot']
                    name=' '.join(v for v in (fld(s,'name'),fld(s,'parentage')) if v).strip()
                    if fld(s,'cnic'):name+='\n'+cnic_display(s)
                    members.append([fld(s,'case_no'),name,display_date(fld(s,'missing_date'))])
                try:people=json.loads(group['attendees_json'] or '[]')
                except (TypeError,ValueError):people=[]
                officers='\n'.join(f'{idx}. {person}' for idx,person in enumerate(compact_attendees(people),1))
                rows.append({'group':True,'serial':str(serial),'members':members,
                             'address':fld(group,'shared_address'),'officers':officers,
                             'instructions':fld(group,'special_instructions')})
                officer_pairs.append([])
                continue
            s=item['snapshot']
            name=' '.join(v for v in (fld(s,'name'),fld(s,'parentage')) if v).strip()
            if fld(s,'cnic'):name+='\n'+cnic_display(s)
            people=compact_attendees(item.get('attendees') or [])
            officers='\n'.join(f'{idx}. {person}' for idx,person in enumerate(people,1))
            instructions=fld(item,'special_instructions') or fld(item,'instructions')
            rows.append([str(serial),fld(s,'case_no'),name,display_date(fld(s,'missing_date')),fld(s,'address'),officers,instructions])
            officer_pairs.append([])
        sections.append({'date':fld(day,'hearing_date'),'time':fld(day,'hearing_time'),
          'area':district_caption(fld(day,'area_caption')),
          'annex_label':fld(notice,'annex_label'),'annex_prefix':fld(notice,'annex_prefix'),
          'letter_ref':fld(notice,'letter_reference'),'letter_date':fld(notice,'letter_date'),
          'title':fld(day,'title') or fld(notice,'title') or 'SCHEDULE - INQUIRY HEARING',
          'rows':rows,'officer_pairs':officer_pairs})
    return sections

def render_html(sections):
    markup='''<!doctype html><html><head><meta charset="UTF-8"><title>CoIoED Inquiry Hearing Schedule</title><style>
    @page {size:11in 8.5in;margin:9mm} *{box-sizing:border-box}body{font:11px Arial,'Segoe UI',sans-serif;color:#111;margin:0}
    .controls{padding:14px;background:#e5f1ea;margin-bottom:15px;font:12px Arial,sans-serif}.controls a{background:#087b50;color:white;text-decoration:none;border-radius:4px;padding:9px 12px;margin-right:8px;display:inline-block}
    .section{margin:0 0 15px}.annex{text-align:right;white-space:pre-line;font-weight:700;font-size:10px;line-height:1.15;min-height:37px}
    .heading{text-align:center;font-weight:700;line-height:1.15;margin:0 0 8px}.heading div{margin:2px}
    table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:9px}thead{display:table-header-group}tr{break-inside:avoid;page-break-inside:avoid}
    th,td{border:1px solid black;padding:3px;white-space:pre-wrap;overflow-wrap:anywhere;vertical-align:top}th{font-weight:700}
    .officers-list{margin:0;padding:0;white-space:pre-wrap;line-height:1.16;overflow-wrap:anywhere}
    @media print{.controls{display:none}.section{break-before:auto}.heading{break-after:avoid}}
    </style></head><body><div class="controls"><strong>For a clean print without browser date, URL/IP address or page numbers, use the Download PDF button on the hearing notice.</strong><br>Browser Print (Ctrl+P) adds those items unless you disable <b>Headers and footers</b> under More settings. Choose Letter landscape.</div>'''
    widths=[round(x/sum(WIDTHS)*100,3) for x in WIDTHS]
    for section_no,sec in enumerate(sections):
        # A single annex block belongs to the start of the consolidated notice, not each hearing day.
        annex='\n'.join(annex_lines(sec)) if section_no==0 else ''
        annex_html='<div class="annex">'+html.escape(annex)+'</div>' if section_no==0 else ''
        markup+='<div class="section">'+annex_html+'<div class="heading"><div>'+html.escape(sec['title'])+'</div><div>'+html.escape(session_label(sec['time'],sec['date']))+'</div><div>('+html.escape(sec['area'])+')</div></div>'
        markup+='<table><colgroup>'+''.join(f'<col style="width:{w}%">' for w in widths)+'</colgroup><thead><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in COLUMNS)+'</tr></thead><tbody>'
        for i,row in enumerate(sec['rows']):
            if isinstance(row,dict) and row.get('group'):
                members=row['members'];n=len(members)
                same_date=len({m[2] for m in members})==1
                for ix,m in enumerate(members):
                    markup+='<tr class="combined-case">'
                    if ix==0:markup+=f'<td rowspan="{n}">'+html.escape(row['serial'])+'</td>'
                    markup+='<td>'+html.escape(m[0])+'</td><td>'+html.escape(m[1])+'</td>'
                    if ix==0 and same_date:markup+=f'<td rowspan="{n}">'+html.escape(m[2])+'</td>'
                    elif not same_date:markup+='<td>'+html.escape(m[2])+'</td>'
                    if ix==0:
                        markup+=(f'<td rowspan="{n}">'+html.escape(row['address'])+'</td>'
                            +f'<td rowspan="{n}"><div class="officers-list">'+html.escape(row['officers'])+'</div></td>'
                            +f'<td rowspan="{n}">'+html.escape(row['instructions'])+'</td>')
                    markup+='</tr>'
            else:
                officer_html='<div class="officers-list">'+html.escape(row[5])+'</div>' if row[5] else ''
                markup+='<tr>'+''.join('<td>'+(officer_html if j==5 else html.escape(v))+'</td>' for j,v in enumerate(row))+'</tr>'
        markup+='</tbody></table></div>'
    return markup+'</body></html>'


def excel_bytes(sections):
    """Editable one-sheet official schedule with top alignment and real pagination."""
    from hearing_excel_v121 import excel_bytes as create_excel
    return create_excel(sections)

def pdf_bytes(sections):
    """Text-based PDF for this English specimen. Pilot uses standard PDF Helvetica encoding.
    The file is not suitable for issuing if any Unicode non-Latin text is present."""
    # Prevent silent character replacement in official outputs.
    def walk_strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,dict):
            for x in value.values():yield from walk_strings(x)
        elif isinstance(value,(list,tuple)):
            for x in value:yield from walk_strings(x)
    for value in walk_strings(sections):
        try:value.encode('cp1252')
        except UnicodeEncodeError:raise ValueError('This pilot PDF exporter cannot safely print non-Latin text. Use Print / Save as PDF preview, or an approved Unicode renderer.')
    paper_w,paper_h=792,612; pad=9; top=paper_h-13; bottom=14
    pages=[];canvas=[]; y=top
    def new_page():
        nonlocal canvas,y
        if canvas:pages.append(''.join(canvas))
        canvas=[];y=top
    def cmd(s):canvas.append(s+'\n')
    def safe(s):
        b=s.encode('cp1252','replace')
        return ''.join(('\\'+chr(x) if x in (40,41,92) else '\\%03o'%x if x<32 or x>=127 else chr(x)) for x in b)
    def text(x,y,txt,pt=7,bold=False,align=None):
        if not txt:return
        width=len(txt)*pt*(.53 if bold else .49)
        if align=='center':x-=width/2
        if align=='right':x-=width
        cmd(f'BT /{"F2" if bold else "F1"} {pt:.1f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({safe(txt)}) Tj ET')
    def rule(x,y,x2,y2):cmd(f'0.50 w {x:.2f} {y:.2f} m {x2:.2f} {y2:.2f} l S')
    def rect(x,y,w,h):cmd(f'0.50 w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S')
    def lines(s,width,pt=7):
        if not s:return ['']
        cap=max(4,int(width/(pt*.58)))
        out=[]
        for para in str(s).split('\n'):
            out.extend(textwrap.wrap(para,width=cap,break_long_words=True,replace_whitespace=False,drop_whitespace=False) or [''])
        return out
    def officer_layout(row, width, fontsize=6.65):
        """Return each officer as compact lines, without empty slots or two columns."""
        return lines(row[5],width-6,fontsize) if row[5] else []
    def group_seg_heights(row):
        segments=[]
        for member in row['members']:
            base=max(len(lines(member[j],WIDTHS[j+1]-6,6.75)) for j in range(3))
            segments.append(max(24,6+8.25*base))
        shared=[row['serial'],row['address'],row['officers'],row['instructions']]
        widths=[WIDTHS[0],WIDTHS[4],WIDTHS[5],WIDTHS[6]]
        needed=max(len(lines(value,width-6,6.75)) for value,width in zip(shared,widths))
        required=max(sum(segments),6+8.25*needed)
        segments[-1]+=required-sum(segments)
        return segments
    def row_height(row,header=False,pairs=None):
        if header:return 21
        if isinstance(row,dict) and row.get('group'):return sum(group_seg_heights(row))
        generic=[lines(value,WIDTHS[i]-6,6.75) for i,value in enumerate(row) if i!=5]
        base=max(map(len,generic)) if generic else 1
        officer=officer_layout(row,WIDTHS[5])
        return max(28, 6+8.25*max(base,len(officer)))
    def row_box(row,header=False,pairs=None):
        nonlocal y
        h=row_height(row,header,pairs)
        if h>paper_h-100:raise ValueError('The combined hearing row is too long for one page. Shorten its shared details or separate the cases; no information was truncated.')
        if isinstance(row,dict) and row.get('group'):
            top=y
            rect(pad,y-h,sum(WIDTHS),h)
            x=pad
            for width in WIDTHS[:-1]:
                x+=width;rule(x,y,x,y-h)
            col_x=[pad+sum(WIDTHS[:i])+3 for i in range(7)]
            for j,line in enumerate(lines(row['serial'],WIDTHS[0]-6,6.75)):
                text(col_x[0],top-10-j*8.25,line,6.75,True)
            for idx,val in [(4,row['address']),(5,row['officers']),(6,row['instructions'])]:
                for j,line in enumerate(lines(val,WIDTHS[idx]-6,6.65)):
                    text(col_x[idx],top-10-j*8.25,line,6.65)
            segs=group_seg_heights(row);p=top
            same_date=len({m[2] for m in row['members']})==1
            if same_date:
                for j,line in enumerate(lines(row['members'][0][2],WIDTHS[3]-6,6.75)):
                    text(col_x[3],top-10-j*8.25,line,6.75)
            for mi,member in enumerate(row['members']):
                for idx,col in [(0,1),(1,2)]+([] if same_date else [(2,3)]):
                    for j,line in enumerate(lines(member[idx],WIDTHS[col]-6,6.75)):
                        text(col_x[col],p-10-j*8.25,line,6.75,col==1)
                p-=segs[mi]
                if mi<len(segs)-1:
                    rule(pad+WIDTHS[0],p,pad+sum(WIDTHS[:3]),p)
                    if not same_date:rule(pad+sum(WIDTHS[:3]),p,pad+sum(WIDTHS[:4]),p)
            y-=h
            return
        rect(pad,y-h,sum(WIDTHS),h)
        xpos=pad
        for i,(width,value) in enumerate(zip(WIDTHS,row)):
            if i:rule(xpos,y,xpos,y-h)
            if i==5 and not header:
                for j,line in enumerate(officer_layout(row,width)):
                    text(xpos+3,y-10-j*8.25,line,6.65)
            else:
                wrap=lines(value,width-6,7.0 if header else 6.75)
                for j,line in enumerate(wrap):text(xpos+3,y-10-j*8.25,line,7.0 if header else 6.75,header or (i==1))
            xpos+=width
        y-=h
    def page_heading(sec,include_annex=False):
        nonlocal y
        if include_annex:
            for j,line in enumerate(annex_lines(sec)):
                text(paper_w-12,y-j*10,line,8,True,'right')
            y-=39
        text(paper_w/2,y,sec['title'],9,True,'center');y-=14
        text(paper_w/2,y,session_label(sec['time'],sec['date']),8,True,'center');y-=12
        text(paper_w/2,y,'('+sec['area']+')',8,True,'center');y-=14
        row_box(COLUMNS,True)

    for section_no,sec in enumerate(sections):
        include_annex=(section_no==0)
        # Leave enough room for the day header plus at least a short case row.
        if y < (155 if include_annex else 115):new_page()
        page_heading(sec,include_annex=include_annex)
        for row_index,row in enumerate(sec['rows']):
            pairs=sec.get('officer_pairs',[])
            pairs=pairs[row_index] if row_index<len(pairs) else []
            height=row_height(row,False,pairs)
            if y-height<bottom:
                new_page()
                # Hearing-day title and ANNEX-A never repeat on overflow pages.
                row_box(COLUMNS,True)
            row_box(row,pairs=pairs)
        y-=12
    if canvas:pages.append(''.join(canvas))
    objects=[]
    def add(data):objects.append(data.encode('latin1') if isinstance(data,str) else data);return len(objects)
    pages_id=add(b'')
    font1=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    font2=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
    children=[]
    for contents in pages:
        cb=contents.encode('latin1')
        content_id=add(b'<< /Length '+str(len(cb)).encode()+b' >>\nstream\n'+cb+b'\nendstream')
        pg_id=add(f'<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {paper_w} {paper_h}] /Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> /Contents {content_id} 0 R >>')
        children.append(pg_id)
    objects[pages_id-1]=f'<< /Type /Pages /Kids [ {" ".join(str(i)+" 0 R" for i in children)} ] /Count {len(children)} >>'.encode()
    root=add(f'<< /Type /Catalog /Pages {pages_id} 0 R >>')
    out=io.BytesIO();out.write(b'%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(out.tell());out.write(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=out.tell();out.write(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for off in offsets[1:]:out.write(f'{off:010d} 00000 n \n'.encode())
    out.write(f'trailer\n<< /Size {len(objects)+1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return out.getvalue()