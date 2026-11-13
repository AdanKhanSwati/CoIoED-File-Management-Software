"""No real case data; verifies 2- and 3-person groups preserve distinct parentage."""
import io, zipfile
import xml.etree.ElementTree as ET
from notice_export import notice_data, render_html, excel_bytes, pdf_bytes

def sample(no,name,parent,missing):
    return {'case_no':no, 'name':name, 'parentage':parent, 'cnic':'', 'missing_date':missing, 'address':'Demo address'}

base={'annex_label':'ANNEX-A','annex_prefix':'To:','letter_reference':'SYNTHETIC','letter_date':'2026-11-10','title':'SCHEDULE - INQUIRY HEARING'}
day={'hearing_date':'2026-11-18','hearing_time':'10:30 AM','area_caption':'Mardan & Swat','title':''}
for count in (2,3):
    items=[]
    for ix in range(count):
        items.append({'group_id':7,'group':{'shared_address':'Shared complainant demo', 'attendees_json':'["Officer A","Officer B"]','special_instructions':'Test only'},'snapshot':sample(str(100+ix)+'-K','Person_'+str(ix),'S/o Parent_'+str(ix),'2026-01-01')})
    sections=notice_data(base,[{'day':day,'items':items}]);row=sections[0]['rows'][0]
    assert row['group'] and len(row['members'])==count
    assert len(sections[0]['rows'])==1
    for i in range(count):
        assert f'Person_{i}\nS/o Parent_{i}' in row['members'][i][1]
        for j in range(count):
            if i!=j:assert f'Parent_{j}' not in row['members'][i][1]
    html=render_html(sections)
    for i in range(count): assert f'Person_{i}' in html and f'Parent_{i}' in html
    xlsx=excel_bytes(sections)
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        ws=z.read('xl/worksheets/sheet1.xml').decode()
        for i in range(count): assert f'Person_{i}' in ws and f'Parent_{i}' in ws
    pdf=pdf_bytes(sections)
    assert pdf.startswith(b'%PDF-')
    print('PASS: distinct clubbed parentage for',count,'people in HTML, Excel, PDF output')