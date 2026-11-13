"""Historical actual proceedings entry for CoIoED; no new tables or network calls.

This is deliberately distinct from approved/issued hearing *schedule* dates.
"""
from __future__ import annotations
from datetime import date
from html import escape
import re

SOURCES = (
    'Original hearing order',
    'Proceedings register',
    'Physical case file',
    'Other official record',
)


def form_html(handler, case_id, documents):
    """A short per-case form, governed by the existing proceedings.add permission."""
    opts = '<option value="">No scanned order linked</option>' + ''.join(
        f'<option value="{int(d["id"])}">{escape(str(d["original_name"]), quote=True)}</option>'
        for d in documents
    )
    sources = ''.join(f'<option value="{escape(name)}">{escape(name)}</option>' for name in SOURCES)
    return f'''<section class="panel" id="historical-proceeding-form" aria-labelledby="historic-proceeding-title">
      <h2 id="historic-proceeding-title">Record an earlier proceeding date</h2>
      <p class="muted small">Use this for a hearing that took place before the software was introduced. You can add dates from older orders or the physical case file; each entry is retained in the case history.</p>
      <form method="post" action="/proceedings/historical/add">
        {handler.csrf()}<input type="hidden" name="case_id" value="{int(case_id)}">
        <div class="two">
          <div class="field"><label for="historical_date">Actual proceeding date</label><input id="historical_date" type="date" name="proceeding_date" max="{date.today().isoformat()}" required></div>
          <div class="field"><label for="historical_source">Where the date was taken from</label><select id="historical_source" name="source_type" required>{sources}</select></div>
          <div class="field"><label for="historical_reference">Order number, page or record reference</label><input id="historical_reference" name="source_reference" maxlength="250" placeholder="e.g. Last order, page 12"></div>
          <div class="field"><label for="historical_attachment">Existing scanned supporting document (optional)</label><select id="historical_attachment" name="document_id">{opts}</select></div>
        </div>
        <div class="field"><label for="historical_notes">Brief proceedings summary / remarks (optional)</label><textarea id="historical_notes" name="summary" rows="3" maxlength="12000" placeholder="May be left blank if only the date is available from the record."></textarea></div>
        <label class="coioed-history-verification"><input type="checkbox" name="verified" value="yes" style="width:auto"> I have checked this date against the official record</label>
        <p class="muted small">Only verified entries contribute to the Last Verified Proceeding Date and hearing-rotation age. For verification, provide an order/page reference or link a scanned document. Unverified entries stay in the history for later checking.</p>
        <button type="submit">Save historical proceeding</button>
      </form>
    </section>'''


def add_entry(handler, form):
    """Store one past actual proceeding using the existing append-only schema/audit."""
    from app import db, audit, now
    import proceedings
    try:
        case_id = int(form.get('case_id', ''))
    except (TypeError, ValueError):
        raise ValueError('Select a valid case file.')
    raw_date = str(form.get('proceeding_date') or '').strip()
    try:
        parsed = date.fromisoformat(raw_date)
        if parsed.isoformat() != raw_date or parsed > date.today():
            raise ValueError
    except ValueError:
        raise ValueError('Enter a real historical proceeding date, not a future date.')
    source = str(form.get('source_type') or '')
    if source not in SOURCES:
        raise ValueError('Select the official record used to establish the date.')
    reference = str(form.get('source_reference') or '').strip()
    if len(reference) > 250:
        raise ValueError('Record reference is too long.')
    document_id = str(form.get('document_id') or '').strip()
    verified = form.get('verified') == 'yes'
    if verified and len(reference) < 3 and not document_id:
        raise ValueError('To verify a historical date, include an order/page reference or select its scanned record.')
    # Do not claim that proceedings details are known when only a date is transcribed.
    notes = str(form.get('summary') or '').strip()
    summary = notes if notes else 'Historical proceeding date transcribed; order details not entered.'
    if len(summary) > 12000:
        raise ValueError('Summary is too long.')
    payload = {
        'proceeding_date': raw_date,
        'proceeding_type': 'Inquiry hearing',
        'summary': summary,
        'directions': '',
        'next_date': '',
        'document_id': document_id,
        'source_reference': f'{source}: {reference}'.strip()[:500],
        'verified': 'yes' if verified else 'no',
    }
    with db() as c:
        if not c.execute('SELECT 1 FROM cases WHERE id=?',(case_id,)).fetchone():
            raise ValueError('This case could not be found.')
        # Avoid duplicate quick-entry dates, while retaining independently entered
        # JIT/PTF/court events that can legitimately occur on the same day.
        existing = c.execute('''SELECT 1 FROM case_proceedings p
          WHERE p.case_id=? AND p.proceeding_date=? AND p.proceeding_type='Inquiry hearing'
          AND NOT EXISTS(SELECT 1 FROM case_proceedings n WHERE n.supersedes_id=p.id)
          LIMIT 1''',(case_id,raw_date)).fetchone()
        if existing:
            raise ValueError('An inquiry proceeding for this case and date is already recorded. Review the existing case chronology before entering it again.')
        entry_id = proceedings.add(c, case_id, payload, handler.user['id'], now())
        audit(c, handler.user['id'], 'HISTORICAL_PROCEEDING_ENTRY', 'case', case_id,
              f'Proceeding #{entry_id} dated {raw_date}; verified={int(verified)}; source={source}')
    return handler.redirect(f'/case/{case_id}#case-proceedings')

def verification_html(handler, case_id, records):
    """Create an explicit source-review step for prior unverified entries."""
    awaiting = [r for r in records if not r['verified']]
    if not awaiting:
        return ''
    parts = [f'<details class="panel"><summary><strong>Verify earlier proceedings ({len(awaiting)} awaiting review)</strong></summary>',
             '<p class="muted small">Only verify after checking the actual order or case file. Confirmation creates a new audit-linked version; the original unverified record remains preserved.</p>']
    for pr in awaiting[:100]:
        eid = int(pr['id'])
        parts.append(f'''<form method="post" action="/proceedings/historical/verify" style="border-top:1px solid #ddd;padding:12px 0">
          {handler.csrf()}
          <input type="hidden" name="case_id" value="{int(case_id)}"><input type="hidden" name="entry_id" value="{eid}">
          <strong>{escape(pr['proceeding_date'])}</strong> · {escape(pr['proceeding_type'])}
          <div class="field"><label>Order, page or physical-record reference used for verification</label><input name="verification_reference" required maxlength="180" placeholder="e.g. Original order page 4"></div>
          <label><input type="checkbox" name="confirmed" value="yes" required style="width:auto"> I verified this proceeding date against the physical record</label>
          <button type="submit" class="secondary">Verify this proceeding</button>
        </form>''')
    parts.append('</details>')
    if len(awaiting)>100:
        parts.append('<p class="muted">Showing the first 100 unverified entries in this case.</p>')
    return ''.join(parts)


def verify_entry(handler, form):
    """Retain original row, append a verified superseding record, and audit it."""
    from app import db, audit, now
    import proceedings
    if form.get('confirmed') != 'yes':
        raise ValueError('You must confirm you checked the actual official record.')
    try:
        case_id = int(form.get('case_id',''))
        entry_id = int(form.get('entry_id',''))
    except (ValueError, TypeError):
        raise ValueError('Invalid case or proceeding selected.')
    ref = str(form.get('verification_reference') or '').strip()
    if len(ref)<3 or len(ref)>180:
        raise ValueError('Enter the actual order/page reference used for verification.')
    with db() as c:
        original = c.execute('''SELECT * FROM case_proceedings p WHERE id=? AND case_id=?
           AND NOT EXISTS(SELECT 1 FROM case_proceedings newer WHERE newer.supersedes_id=p.id)''',
          (entry_id,case_id)).fetchone()
        if not original:raise ValueError('This entry is missing or already superseded. Refresh the case file.')
        if original['verified']:raise ValueError('This proceeding was already verified.')
        payload={
            'proceeding_date':original['proceeding_date'],
            'proceeding_type':original['proceeding_type'],
            'summary':original['summary'],
            'directions':original['directions'],
            'next_date':original['next_date'],
            'document_id':str(original['document_id'] or ''),
            'source_reference':(original['source_reference']+' | Verified: '+ref)[:500],
            'supersedes_id':str(entry_id),
            'verified':'yes',
        }
        new_id=proceedings.add(c,case_id,payload,handler.user['id'],now())
        audit(c,handler.user['id'],'VERIFY_HISTORICAL_PROCEEDING','case',case_id,
            f'Proceeding #{entry_id} superseded by #{new_id}; date={original["proceeding_date"]}')
    return handler.redirect(f'/case/{case_id}#case-proceedings')