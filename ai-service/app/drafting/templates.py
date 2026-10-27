"""Jinja templates for sections drafted locally from library data (no external call)."""
from __future__ import annotations

TEMPLATES = {
"letterhead": """<div class="lh-own">{{ company.name | upper }}<small>{% if company.cin %}CIN: {{ company.cin }}, {% endif %}{{ company.city }}{% if certifications %}. {{ certifications }}{% endif %}</small></div>""",

"covering_letter": """{% include 'letterhead' %}
<p class="split"><span>Ref: {{ fill('reference no.') }}</span><span>Date: {{ fill('date') }}</span></p>
<p>To,<br>{{ fill('addressee as per RFP') }}<br>{{ rfp.department or fill('department') }}</p>
<p><b>Subject:</b> Submission of technical bid against RFP No. {{ rfp.rfp_ref or fill('RFP no.') }}{% if rfp.title %} for {{ rfp.title }}{% endif %}</p>
<p>Dear Sir/Madam,</p>
<p>We, the undersigned, offer to provide the services for the above assignment in accordance with your RFP. We hereby submit our proposal, comprising this technical bid and a financial bid submitted separately.</p>
<p>We hereby declare that:</p>
<ol>
<li>All information and statements made in this proposal are true, and we accept that any misrepresentation in it may lead to our disqualification.</li>
<li>Our proposal shall remain valid for {{ rfp.bid_validity or fill('bid validity') }} from the bid due date.</li>
<li>We have paid the EMD of {{ rfp.emd or fill('EMD amount') }} vide {{ fill('instrument / e-payment reference') }} dated {{ fill('date') }}.</li>
<li>We have not been blacklisted by any Central or State Government department or PSU as on the bid due date.</li>
<li>We agree to abide by all the terms and conditions of the RFP and its corrigenda, if any.</li>
</ol>
<p>We understand that you are not bound to accept any proposal you receive.</p>
<p>Yours faithfully,</p>
<p class="sign-space">(Authorised signatory)<br>Name: {{ fill('name') }}<br>Designation: {{ fill('designation') }}<br>For and on behalf of {{ company.name }}</p>""",

"bidder_profile": """<table><thead><tr><th style="width:38%">Particulars</th><th>Details</th></tr></thead><tbody>
<tr><td>Name of the bidder</td><td>{{ company.name }}</td></tr>
<tr><td>Corporate Identity Number</td><td>{{ company.cin or fill('CIN') }}{% if refs.incorporation %} {{ xref('doc', refs.incorporation) }}{% endif %}</td></tr>
<tr><td>Registered office</td><td>{{ company.city }}</td></tr>
<tr><td>PAN</td><td>{{ details.pan or fill('PAN') }}{% if refs.pan %} {{ xref('doc', refs.pan) }}{% endif %}</td></tr>
<tr><td>GSTIN</td><td>{{ details.gstin or fill('GSTIN') }}{% if refs.gst %} {{ xref('doc', refs.gst) }}{% endif %}</td></tr>
<tr><td>Certifications</td><td>{% for c in certs %}{{ c.title }} {{ xref('doc', c.id) }}{% if not loop.last %}<br>{% endif %}{% else %}{{ fill('certifications') }}{% endfor %}</td></tr>
<tr><td>Contact person for this bid</td><td>{{ fill('name, mobile, e-mail') }}</td></tr>
</tbody></table>
{% if profile_html %}<h3>Company profile</h3>{{ profile_html | safe }}{% endif %}""",

"pq_matrix": """<p>Compliance with each pre-qualification criterion of the RFP is set out below, with page references to the supporting documents in this bid.</p>
<table><thead><tr><th style="width:11%">Clause</th><th>Criterion</th><th style="width:12%">Complied</th><th style="width:32%">Supporting document</th></tr></thead><tbody>
{% for r in pq %}<tr><td>{{ r.clause }}</td><td>{{ r.text }}</td>
<td>{% if r.status == 'met' or r.status == 'undertaking' or r.form_section %}Yes{% elif r.status == 'partial' or r.status == 'review' %}{{ fill('confirm') }}{% else %}{{ fill('to be provided') }}{% endif %}</td>
<td>{% for d in r.evidence %}{% if d in docs %}{{ docs[d].title }} {{ xref('doc', d) }}<br>{% endif %}{% endfor %}{% if r.form_section %}{{ r.form_title }} {{ xref('sec', r.form_section) }}{% endif %}{% if not r.evidence and not r.form_section %}—{% endif %}</td></tr>
{% endfor %}</tbody></table>""",

"separator": """<div class="separator">
<div class="sep-kind">{{ kind_label }}</div>
<div class="sep-clause">{% if clause %}Clause {{ clause }}{% endif %}</div>
<div class="sep-text">{{ text }}</div>
<table class="sep-table"><tbody>
{% for label, value in facts %}{% if value %}<tr><th style="width:30%">{{ label }}</th><td>{{ value }}</td></tr>{% endif %}{% endfor %}
<tr><th style="width:30%">Compliance</th><td><b>{{ compliance }}</b></td></tr>
<tr><th>Supporting documents</th><td>{% for d in documents %}{{ d.title }}{% if d.earlier %} (enclosed earlier){% endif %} {{ xref('doc', d.id) }}<br>{% endfor %}{% for s in sections %}{{ s.title }} {{ xref('sec', s.id) }}<br>{% endfor %}{% if not documents and not sections %}{{ fill('documents to be enclosed') }}{% endif %}</td></tr>
{% if remarks %}<tr><th>Remarks</th><td>{{ remarks }}</td></tr>{% endif %}
</tbody></table></div>""",

"tec_matrix": """<p>Our response to each technical evaluation criterion of the RFP is set out below, with page references to the supporting documents and sections of this bid. Each criterion is followed by a separator page and its documents.</p>
<table><thead><tr><th style="width:11%">Clause</th><th>Parameter</th><th style="width:9%">Max marks</th><th style="width:38%">Supporting documents / reference</th></tr></thead><tbody>
{% for t in tec %}<tr><td>{{ t.clause }}</td><td>{{ t.parameter }}</td><td>{% if t.max_marks is not none %}{{ '%g' | format(t.max_marks) }}{% endif %}</td>
<td>{% for d in t.documents %}{{ d.title }} {{ xref('doc', d.id) }}<br>{% endfor %}{% for s in t.sections %}{{ s.title }} {{ xref('sec', s.id) }}<br>{% endfor %}{{ t.note }}{% if not t.documents and not t.sections and not t.note %}—{% endif %}</td></tr>
{% endfor %}</tbody></table>""",

"citations": """{% for p in projects %}<h3>Citation {{ loop.index }}: {{ p.project }}</h3>
<table><tbody>
<tr><th style="width:32%">Client</th><td>{{ p.client or fill('client') }}</td></tr>
<tr><th>Contract value</th><td>{% if p.value_cr %}₹ {{ '%.2f' | format(p.value_cr) }} Cr{% else %}{{ fill('value') }}{% endif %}</td></tr>
<tr><th>Period</th><td>{{ p.period or fill('start and end dates') }}</td></tr>
<tr><th>Status</th><td>{{ 'Completed' if p.completed else fill('status') }}</td></tr>
<tr><th>Scope of work</th><td>{{ p.scope or fill('brief scope') }}</td></tr>
<tr><th>Supporting documents</th><td>Work order {{ xref('doc', p.id) }}{% for cc in p.certificates %}; certificate {{ xref('doc', cc) }}{% endfor %}</td></tr>
</tbody></table>
{% else %}<p>{{ fill('add project citations') }}</p>{% endfor %}""",

"personnel": """<p>The key personnel proposed for this assignment, mapped to the requirements of the RFP, are listed below. Their CVs are enclosed.</p>
<table><thead><tr><th>Position</th><th>RFP requirement</th><th>Proposed</th><th style="width:9%">Years</th><th style="width:13%">CV</th></tr></thead><tbody>
{% for p in people %}<tr><td>{{ p.role }}</td><td>{{ p.qualification }}{% if p.min_years %}; {{ p.min_years | int }}+ years{% endif %}</td>
<td>{{ p.name or fill('name') }}</td><td>{{ (p.years | int) if p.years else '' }}</td><td>{% if p.document_id %}{{ xref('doc', p.document_id) }}{% else %}{{ fill('CV') }}{% endif %}</td></tr>
{% endfor %}</tbody></table>""",

"undertaking_blacklisting": """<p class="center"><i>(On the letterhead of the bidder)</i></p>
<p>To,<br>{{ fill('addressee as per RFP') }}<br>{{ rfp.department or '' }}</p>
<p><b>Subject:</b> Undertaking on non-blacklisting, RFP No. {{ rfp.rfp_ref or fill('RFP no.') }}</p>
<p>We, {{ company.name }}, having our registered office at {{ company.city }}, hereby declare that the company has not been blacklisted or debarred by any Ministry or Department of the Government of India, any State Government, or any Public Sector Undertaking as on the date of submission of this bid.</p>
<p>If this declaration is found to be incorrect at any stage, the purchaser may forfeit our EMD or performance security and disqualify our bid.</p>
<p>Place: {{ company.city }}<br>Date: {{ fill('date') }}</p>
<p class="sign-space">(Authorised signatory)<br>Name: {{ fill('name') }}</p>""",

"manpower_certificate": """<p class="center"><i>(On the letterhead of the bidder)</i></p>
<p><b>Subject:</b> Certificate of IT manpower strength, RFP No. {{ rfp.rfp_ref or fill('RFP no.') }}</p>
<p>This is to certify that {{ company.name }} has {{ fill('headcount from HR register') }} IT professionals on its payroll as on {{ fill('date') }}, as per the EPFO records (ECR for {{ fill('month') }}) enclosed.</p>
<table><thead><tr><th>Category</th><th style="width:30%">Number on payroll</th></tr></thead><tbody>
<tr><td>Software developers and leads</td><td>{{ fill('no.') }}</td></tr><tr><td>Architects, analysts and project managers</td><td>{{ fill('no.') }}</td></tr>
<tr><td>QA, security and database administrators</td><td>{{ fill('no.') }}</td></tr><tr><td>O&amp;M and helpdesk</td><td>{{ fill('no.') }}</td></tr>
<tr><th>Total</th><th>{{ fill('total') }}</th></tr></tbody></table>
<p class="sign-space">(Authorised signatory / Head of HR)<br>Name: {{ fill('name') }}</p>""",

"undertaking_local_office": """<p class="center"><i>(On the letterhead of the bidder)</i></p>
<p><b>Subject:</b> Undertaking to establish a project office, RFP No. {{ rfp.rfp_ref or fill('RFP no.') }}</p>
<p>We, {{ company.name }}, hereby undertake to establish a project office at {{ fill('city') }} within {{ fill('period as per RFP') }} of signing the contract, staffed for the duration of the contract.</p>
<p>Place: {{ company.city }}<br>Date: {{ fill('date') }}</p>
<p class="sign-space">(Authorised signatory)<br>Name: {{ fill('name') }}</p>""",
}
