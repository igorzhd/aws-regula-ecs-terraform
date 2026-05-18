// ============================================================
// app.js — Document Verification Frontend
// ============================================================

const API_BASE = (window.APP_CONFIG?.API_BASE ?? 'http://localhost:8001').replace(/\/$/, '');

// ── State ────────────────────────────────────────────────────
let frontFile = null;
let backFile  = null;
let currentPage = 1;
const PAGE_SIZE = 20;

// ── Helpers ──────────────────────────────────────────────────
// Regula CheckResult enum: 0=ERROR(FAIL), 1=OK(PASS), 2=WAS_NOT_DONE(N/A)
function statusBadge(status) {
  if (status === 1) return '<span class="badge badge-pass"><span class="badge-dot"></span>PASS</span>';
  if (status === 0) return '<span class="badge badge-fail"><span class="badge-dot"></span>FAIL</span>';
  return '<span class="badge badge-na"><span class="badge-dot"></span>N/A</span>';
}

function statusBadgeSm(status, tooltip = '') {
  const t = tooltip ? ` title="${escHtml(tooltip)}"` : '';
  if (status === 1) return `<span class="badge badge-sm badge-pass"${t}><span class="badge-dot"></span>PASS</span>`;
  if (status === 0) return `<span class="badge badge-sm badge-fail"${t}><span class="badge-dot"></span>FAIL</span>`;
  return `<span class="badge badge-sm badge-na"${t}><span class="badge-dot"></span>N/A</span>`;
}

// Field display names (user-friendly overrides for Regula's internal names)
const FIELD_DISPLAY_NAMES = { 8: 'Last Name', 9: 'First Name' };
// Display order for text fields by field_type: First Name, Last Name, Doc Number, Expiry, Issue, DOB
const FIELD_DISPLAY_ORDER = [9, 8, 2, 3, 4, 5];

// ── Strict overall computation ────────────────────────────────
// Overall = PASS only when optical and MRZ checks pass, and text fields have no
// genuine read failures or real value mismatches.
//
// CheckResult enum: 0=ERROR(FAIL), 1=OK(PASS), 2=WAS_NOT_DONE(N/A)
//
// "Hard fail" for a field source = validity=0(ERROR) AND value is empty — OCR could not
// read the field at all. validity=0 with a non-empty value is a format-validation
// warning from Regula (the template mask didn't match), NOT a read failure.
// Expiry is NOT checked here — detailsOptical.expiry fires on format issues too,
// not just actual document expiry (Regula's own optical status is the authoritative check).
function computeStrict(data) {
  const s = data.statuses || {};
  const issues = [];
  let strictOverall = 1;  // 1=OK=PASS until a hard failure is found

  if (s.optical_status === 0) {  // 0=ERROR=FAIL
    strictOverall = 0;
    issues.push('Optical checks failed');
  }
  if (s.mrz_check === 0) {  // 0=ERROR=FAIL
    strictOverall = 0;
    issues.push('MRZ (machine-readable zone) validation failed');
  }

  const seenTypes = new Set();
  const dedupedFields = ((data.text_fields || {}).fields || [])
    .filter(f => {
      if (seenTypes.has(f.field_type)) return false;
      seenTypes.add(f.field_type);
      return true;
    })
    .sort((a, b) => {
      const ai = FIELD_DISPLAY_ORDER.indexOf(a.field_type);
      const bi = FIELD_DISPLAY_ORDER.indexOf(b.field_type);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });

  for (const f of dedupedFields) {
    if (f.overall_status !== 0) continue;  // skip fields that aren't ERROR/FAIL

    const hasHardFail = (f.sources || []).some(sv => sv.validity === 0 && (sv.value ?? '') === '');
    const srcVals = {};
    (f.sources || []).forEach(sv => { srcVals[sv.source] = sv.value ?? ''; });
    const hasGenuineMismatch = (f.comparisons || []).some(c => {
      if (c.status !== 0) return false;  // skip non-FAIL comparisons
      const l = srcVals[c.source_left]  ?? '';
      const r = srcVals[c.source_right] ?? '';
      return l === '' || l !== r;
    });

    if (!hasHardFail && !hasGenuineMismatch) continue;

    strictOverall = 0;  // 0=ERROR=FAIL
    const displayName = FIELD_DISPLAY_NAMES[f.field_type] || f.field_name;
    const failSrcs = (f.sources || []).filter(sv => sv.validity === 0 && (sv.value ?? '') === '').map(sv => sv.source);
    const parts = [];
    if (failSrcs.length) parts.push(`${failSrcs.join('+')} could not be read`);
    else if (hasGenuineMismatch) parts.push('source values do not match');
    issues.push(`${displayName}: ${parts.join('; ') || 'validation failed'}`);
  }

  return { strictOverall, issues };
}

// ── Image quality check detail text ──────────────────────────
// result=0 means the check RAN and FAILED — never remap to N/A.
// result=2 means the check was not performed (WAS_NOT_DONE).
function qualityDetail(c, result) {
  const details = {
    0: { pass: 'No glare',              fail: 'Glare detected on document',          na: 'Check not performed' },
    1: { pass: 'Image is sharp',        fail: 'Image is blurry',                     na: 'Check not performed' },
    2: { pass: 'Sufficient resolution', fail: 'Image resolution too low',            na: 'Check not performed' },
    3: { pass: 'Color confirmed',       fail: 'Image appears grayscale',             na: 'Check not performed' },
    4: { pass: 'Document is straight',  fail: 'Document angle too steep',            na: 'Check not performed' },
    5: { pass: 'Fully in frame',        fail: 'Document not fully in frame',         na: 'Check not performed' },
    6: { pass: 'Not a screen capture',  fail: 'Appears to be a photo of a screen',  na: 'Check not performed' },
    7: { pass: 'Portrait detected',     fail: 'Portrait not detected',              na: 'Check not performed' },
    8: { pass: 'No handwriting',        fail: 'Handwritten text detected',           na: 'Check not performed' },
    9: { pass: 'Good brightness',       fail: 'Too dark or overexposed',            na: 'Check not performed' },
  };
  const d = details[c.type];
  if (!d) return '';
  if (result === 1) return d.pass;  // 1=OK=PASS
  if (result === 0) return d.fail;  // 0=ERROR=FAIL
  return d.na;                      // 2=WAS_NOT_DONE=N/A
}

// ── Per-page quality checks renderer (used inside each crop column) ──
function renderPageQuality(p) {
  if (!p || !(p.checks || []).length) return '';
  const rows = p.checks.map(c => {
    const detail = qualityDetail(c, c.result);
    return `
      <div class="quality-check">
        <div class="quality-check-left">
          <span class="quality-check-name">${escHtml(c.name)}</span>
          ${detail ? `<span class="quality-check-detail">${escHtml(detail)}</span>` : ''}
        </div>
        <span class="quality-check-right">${statusBadge(c.result)}</span>
      </div>`;
  }).join('');
  return `<div class="crop-section-label crop-section-label--quality">Image Quality</div><div class="crop-quality-checks">${rows}</div>`;
}

// ── Crop doc info renderer ────────────────────────────────────
function renderCropDocInfo(p) {
  if (!p || !p.name) return '';
  const metas = [
    p.country   && `<div class="crop-doc-meta-item"><span class="doc-meta-key">Country:</span> <span class="doc-meta-val">${escHtml(p.country)}</span></div>`,
    p.icao_code && `<div class="crop-doc-meta-item"><span class="doc-meta-key">ICAO:</span> <span class="doc-meta-val">${escHtml(p.icao_code)}</span></div>`,
    p.doc_year  && `<div class="crop-doc-meta-item"><span class="doc-meta-key">Year:</span> <span class="doc-meta-val">${escHtml(p.doc_year)}</span></div>`,
  ].filter(Boolean).join('');
  return `<div class="crop-section-label crop-section-label--doctype">Document Type</div><div class="crop-doc-info"><div class="crop-doc-name">${escHtml(p.name)}</div><div class="crop-doc-meta">${metas}</div></div>`;
}

// ── Verification summary renderer ─────────────────────────────
// failureDetails: array of {category, message, detail} from backend failure_details field.
function renderSummary(strictOverall, failureDetails) {
  const isFail = strictOverall === 0;
  const mod = isFail ? 'fail' : 'pass';

  const iconSvg = isFail
    ? `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`
    : `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

  const details = failureDetails.length === 0
    ? '<p class="summary-ok">✓ All checks passed</p>'
    : failureDetails.map(fd => {
        const isQA = fd.category === 'image_quality';
        const msgText = isQA
          ? `⚠ Image quality: ${escHtml(fd.message)}`
          : `● ${escHtml(fd.message)}`;
        return `
        <div class="failure-item${isQA ? ' failure-item--qa' : ''}">
          <div class="failure-msg${isQA ? ' failure-msg--qa' : ''}">${msgText}</div>
          <div class="failure-detail">${escHtml(fd.detail)}</div>
        </div>`;
      }).join('');

  return `
    <div class="card results-wide summary-card">
      <div class="card-title">Verification Summary</div>
      <div class="summary-body">
        <div class="summary-indicator summary-indicator--${mod}">
          <div class="summary-anim-ring"></div>
          <div class="summary-icon-wrap">${iconSvg}</div>
          <div class="summary-verdict">${isFail ? 'FAIL' : 'PASS'}</div>
        </div>
        <div class="summary-details-zone">
          <div class="summary-details-label">Details</div>
          ${details}
        </div>
      </div>
    </div>`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function fmtMs(ms) {
  if (ms == null) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms/1000).toFixed(1)}s`;
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Router ───────────────────────────────────────────────────
function route() {
  const hash = window.location.hash || '#process';
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));

  if (hash.startsWith('#session/')) {
    const id = hash.slice('#session/'.length);
    showView('view-detail');
    renderDetail(id);
  } else if (hash === '#history') {
    showView('view-history');
    document.querySelector('[data-view="history"]')?.classList.add('active');
    renderHistory(1);
  } else {
    showView('view-process');
    document.querySelector('[data-view="process"]')?.classList.add('active');
  }
}

function showView(id) {
  document.getElementById(id)?.classList.add('active');
}

function navigate(hash) {
  window.location.hash = hash;
}

window.addEventListener('hashchange', route);
window.addEventListener('load', route);

// ── Upload zone setup ────────────────────────────────────────
function setupUploadZone(zoneId, inputId, previewImgId, labelText, onFile) {
  const zone  = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  const img   = document.getElementById(previewImgId);
  if (!zone || !input) return;

  function setFile(file) {
    if (!file || !file.type.startsWith('image/')) return;
    onFile(file);
    const reader = new FileReader();
    reader.onload = e => {
      img.src = e.target.result;
      zone.querySelector('.upload-placeholder').classList.add('hidden');
      zone.querySelector('.upload-preview').classList.remove('hidden');
      zone.classList.add('has-file');
    };
    reader.readAsDataURL(file);
    updateProcessBtn();
  }

  function clearFile() {
    onFile(null);
    img.src = '';
    zone.querySelector('.upload-placeholder').classList.remove('hidden');
    zone.querySelector('.upload-preview').classList.add('hidden');
    zone.classList.remove('has-file');
    input.value = '';
    updateProcessBtn();
  }

  input.addEventListener('change', e => { if (e.target.files[0]) setFile(e.target.files[0]); });

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) { input.files = e.dataTransfer.files; setFile(file); }
  });

  zone.querySelector('.remove-btn')?.addEventListener('click', e => {
    e.stopPropagation(); e.preventDefault(); clearFile();
  });
}

function updateProcessBtn() {
  const btn = document.getElementById('process-btn');
  if (btn) btn.disabled = !frontFile;
}

// ── Reset both upload zones ───────────────────────────────────
function resetUploads() {
  frontFile = null;
  backFile  = null;
  ['zone-front', 'zone-back'].forEach(zoneId => {
    const zone = document.getElementById(zoneId);
    if (!zone) return;
    zone.querySelector('.upload-placeholder').classList.remove('hidden');
    zone.querySelector('.upload-preview').classList.add('hidden');
    zone.classList.remove('has-file', 'zone-not-provided');
    const input = zone.querySelector('input[type=file]');
    if (input) input.value = '';
    const img = zone.querySelector('.upload-preview img');
    if (img) img.src = '';
  });
  updateProcessBtn();
}

// ── Process view ─────────────────────────────────────────────
function initProcessView() {
  setupUploadZone('zone-front', 'input-front', 'preview-front', 'Front', f => { frontFile = f; });
  setupUploadZone('zone-back',  'input-back',  'preview-back',  'Back',  f => { backFile = f; });

  document.getElementById('process-btn')?.addEventListener('click', async () => {
    if (!frontFile) return;
    showProcessing(true);
    hideError();
    try {
      const fd = new FormData();
      fd.append('image_front', frontFile);
      if (backFile) fd.append('image_back', backFile);

      const res = await fetch(`${API_BASE}/process`, { method: 'POST', body: fd });
      const data = await res.json();

      if (!res.ok) {
        showError(data.message || `Error ${res.status}`);
        return;
      }
      showResults(data, false);
    } catch (err) {
      showError(`Could not reach the API. Is it running? (${err.message})`);
    } finally {
      showProcessing(false);
    }
  });
}

function showProcessing(on) {
  document.getElementById('process-spinner').classList.toggle('hidden', !on);
  const btn = document.getElementById('process-btn');
  if (btn) btn.disabled = on || !frontFile;
}

function showError(msg) {
  const el = document.getElementById('process-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}

function hideError() {
  document.getElementById('process-error')?.classList.add('hidden');
}

function showResults(data, isDetail) {
  const container = document.getElementById(isDetail ? 'detail-results' : 'results');
  if (!container) return;
  container.innerHTML = renderResults(data, isDetail);
  container.classList.remove('hidden');
  if (!isDetail) {
    document.getElementById('process-btn')?.classList.add('hidden');
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (!backFile) document.getElementById('zone-back')?.classList.add('zone-not-provided');
  }

  // Wire up Download JSON button
  container.querySelector('.js-download-json')?.addEventListener('click', () => {
    const url = `${API_BASE}/sessions/${data.session_id}/download`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `regula_${data.transaction_id}.json`;
    a.click();
  });

  // Wire up New / Back button
  const newBtn = container.querySelector('.js-new-process');
  if (newBtn) newBtn.addEventListener('click', () => {
    container.classList.add('hidden');
    resetUploads();
    document.getElementById('process-btn')?.classList.remove('hidden');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}

// ── Results renderer ─────────────────────────────────────────
function renderResults(data, isDetail) {
  const docType = (data.doc_type || []).find(p => p.page === 0) || {};
  const processedAt = fmtDate(data.processed_at);
  const elapsed = fmtMs(data.elapsed_time_ms);

  // Compute strict overall (stricter than Regula's lenient algorithm)
  const { strictOverall, issues } = computeStrict(data);
  // Backend document_verdict excludes image quality from PASS/FAIL.
  // Fall back to the JS-side strictOverall for old sessions without it.
  const documentVerdict = (data.document_verdict != null) ? data.document_verdict : strictOverall;

  // --- Header ---
  const backBtn = isDetail
    ? `<button class="btn btn-outline js-back-history" onclick="navigate('#history')">← Back to History</button>`
    : `<button class="btn btn-primary js-new-process">Process New</button>`;

  const header = `
    <div class="results-header">
      <div class="results-meta-labels">
        <div class="results-meta-row"><span class="results-meta-key">Processed</span><span class="results-meta-val">${processedAt}</span></div>
        ${elapsed ? `<div class="results-meta-row"><span class="results-meta-key">Processing time</span><span class="results-meta-val">${elapsed}</span></div>` : ''}
      </div>
      <div class="results-actions">
        <button class="btn btn-secondary js-download-json">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 2v8M5 7l3 3 3-3M3 13h10"/>
          </svg>
          Download JSON
        </button>
        ${backBtn}
      </div>
    </div>`;

  // --- Document images with inline doc type + quality ---
  const imgs = data.document_images || {};
  const docTypeByPage = {};
  (data.doc_type || []).forEach(p => { docTypeByPage[p.page] = p; });
  const qualityByPage = {};
  (data.image_quality || []).forEach(p => { qualityByPage[p.page] = p; });

  const cropItems = [
    imgs.page_0 ? `
      <div class="crop-item">
        <div class="crop-label">Front</div>
        <div class="crop-img-wrap"><img src="${escHtml(imgs.page_0)}" alt="Front crop" loading="lazy"/></div>
        ${renderCropDocInfo(docTypeByPage[0])}
        ${renderPageQuality(qualityByPage[0])}
      </div>` : '',
    imgs.page_1 ? `
      <div class="crop-item">
        <div class="crop-label">Back</div>
        <div class="crop-img-wrap"><img src="${escHtml(imgs.page_1)}" alt="Back crop" loading="lazy"/></div>
        ${renderCropDocInfo(docTypeByPage[1])}
        ${renderPageQuality(qualityByPage[1])}
      </div>` : '',
  ].filter(Boolean).join('');

  const hasBoth = !!(imgs.page_0 && imgs.page_1);
  const cropsCard = `
    <div class="card results-wide">
      <div class="card-title">Document Images</div>
      <div class="crop-images${hasBoth ? '' : ' crop-images--single'}" style="${hasBoth ? '--crop-cols:2' : ''}">${cropItems || '<p style="color:var(--text-3);font-size:13px">No images available</p>'}</div>
    </div>`;

  // --- Summary ---
  const summaryCard = renderSummary(documentVerdict, data.failure_details || []);

  // --- Verification Status ---
  const seenFt = new Set();
  const dedupedFields = (data.text_fields?.fields || [])
    .filter(f => {
      if (seenFt.has(f.field_type)) return false;
      seenFt.add(f.field_type);
      return true;
    })
    .sort((a, b) => {
      const ai = FIELD_DISPLAY_ORDER.indexOf(a.field_type);
      const bi = FIELD_DISPLAY_ORDER.indexOf(b.field_type);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  // Hard fail = source validity=0(ERROR) AND value is empty (field unreadable by OCR).
  // validity=0 with a non-empty value is a format mask warning, not a read failure.
  const hasHardFieldFail = dedupedFields.some(f => {
    if (f.overall_status !== 0) return false;  // skip non-FAIL fields (0=ERROR=FAIL)
    if ((f.sources || []).some(sv => sv.validity === 0 && (sv.value ?? '') === '')) return true;
    const sv2 = {};
    (f.sources || []).forEach(s => { sv2[s.source] = s.value ?? ''; });
    return (f.comparisons || []).some(c => {
      if (c.status !== 0) return false;  // skip non-FAIL comparisons
      const l = sv2[c.source_left] ?? '', r = sv2[c.source_right] ?? '';
      return l === '' || l !== r;
    });
  });
  const strictTextField = hasHardFieldFail ? 0 : (data.text_fields?.overall_status ?? 2);

  // --- Text fields (flat single-row-per-field layout) ---
  const tf = data.text_fields || {};

  // ── DIAGNOSTIC LOG ───────────────────────────────────────────
  // Dumps the raw SDK integers to the browser console (DevTools → Console).
  // Regula CheckResult enum: 0=ERROR(FAIL), 1=OK(PASS), 2=WAS_NOT_DONE(N/A)
  if (dedupedFields.length > 0) {
    console.group('[TextFields] Raw SDK data');
    dedupedFields.forEach(f => {
      console.group(`Field: ${f.field_name} (type ${f.field_type})`);
      console.log('  overall_status  (field.status):', f.overall_status, f.overall_label);
      console.log('  validity_status (field.validityStatus):', f.validity_status, f.validity_label);
      console.log('  comparison_status:', f.comparison_status, f.comparison_label);
      (f.sources || []).forEach(sv => {
        const note = sv.validity === 2 ? '← WAS_NOT_DONE (not checksummed for this source)'
                   : sv.validity === 0 ? '← ERROR: checksum/format FAILED'
                   : '← OK: checksum/format PASSED';
        console.log(`  [${sv.source}] value="${sv.value}"  originalValidity=${sv.validity} (${sv.validity_label}) ${note}`);
      });
      (f.comparisons || []).forEach(c => {
        const note = c.status === 2 ? '← WAS_NOT_DONE; UI computes from values'
                   : c.status === 1 ? '← OK: sources matched'
                   : '← ERROR: sources did not match';
        console.log(`  [CMP] ${c.source_left} vs ${c.source_right}: status=${c.status} (${c.status_label}) ${note}`);
      });
      console.groupEnd();
    });
    console.groupEnd();
  }

  // Collect all source names that appear across the dataset, in encounter order
  const allSourceNames = [];
  const seenSrcNames = new Set();
  dedupedFields.forEach(f => {
    (f.sources || []).forEach(sv => {
      if (!seenSrcNames.has(sv.source)) {
        seenSrcNames.add(sv.source);
        allSourceNames.push(sv.source);
      }
    });
  });

  const tableRows = dedupedFields.map(f => {
    const sources = f.sources || [];
    const comparisons = f.comparisons || [];
    const srcMap = {};
    sources.forEach(sv => { srcMap[sv.source] = sv; });

    // VALIDITY: aggregate from per-source originalValidity using the correct enum.
    // CheckResult: 0=ERROR(FAIL), 1=OK(PASS), 2=WAS_NOT_DONE(N/A)
    // Rule: any source ERROR(0) → field FAIL; no ERROR but any OK(1) → PASS; all N/A → N/A.
    // f.validity_status from the SDK is also available but can be N/A even when sources fail;
    // aggregating from individual sources is more reliable.
    let displayFieldStatus;
    if (sources.some(sv => sv.validity === 0)) {
      displayFieldStatus = 0;  // 0=ERROR=FAIL: at least one source failed checksum/format
    } else if (sources.some(sv => sv.validity === 1)) {
      displayFieldStatus = 1;  // 1=OK=PASS: at least one passed, none failed
    } else {
      displayFieldStatus = 2;  // all WAS_NOT_DONE=N/A
    }

    // FIX 1: No per-source validity badges inside value cells.
    // Per-source originalValidity is a checksum-only check (MRZ gets ICAO check-digit
    // validation; VISUAL is never checksummed so always N/A by design). Showing badges
    // here added noise without user-interpretable meaning. The VALIDITY column already
    // captures this information at the field level.
    const sourceCells = allSourceNames.map(src => {
      const sv = srcMap[src];
      if (!sv) return `<td class="src-col src-col-empty"><span class="src-empty">—</span></td>`;
      return `<td class="src-col"><div class="src-val">${escHtml(sv.value || '—')}</div></td>`;
    }).join('');

    // FIX 3: Comparison cell — use the corrected enum (1=OK=PASS, 0=ERROR=FAIL).
    // When the SDK's c.status is not an explicit 1(PASS), compute from string values:
    // both present and equal → PASS; differ → FAIL with values shown; one missing → N/A.
    let compCell;
    if (sources.length < 2 || comparisons.length === 0) {
      compCell = '<span class="src-empty">—</span>';
    } else {
      const evaluated = comparisons.map(c => {
        const lVal = (srcMap[c.source_left]?.value) ?? '';
        const rVal = (srcMap[c.source_right]?.value) ?? '';
        let kind;
        if (c.status === 1) {
          kind = 'pass';  // 1=OK=PASS: SDK explicitly confirmed match
        } else if (lVal !== '' && rVal !== '') {
          kind = lVal === rVal ? 'pass' : 'fail';  // compute from actual values
        } else {
          kind = 'na';  // at least one value missing, cannot compare
        }
        return { source_left: c.source_left, source_right: c.source_right, lVal, rVal, kind };
      });

      const fails  = evaluated.filter(r => r.kind === 'fail');
      const passes = evaluated.filter(r => r.kind === 'pass');

      if (fails.length === 0) {
        compCell = passes.length > 0 ? statusBadge(1) : statusBadge(2);
      } else {
        compCell = fails.map(r => `
          <div class="comp-fail-entry">
            <div class="comp-fail-label">
              <span class="comp-sources">${escHtml(r.source_left)} vs ${escHtml(r.source_right)}</span>
              ${statusBadgeSm(0)}
            </div>
            <div class="comp-fail-detail">${escHtml(r.source_left)}: ${escHtml(r.lVal || '—')} / ${escHtml(r.source_right)}: ${escHtml(r.rVal || '—')}</div>
          </div>`).join('');
      }
    }

    const rowCls = displayFieldStatus === 0 ? 'row-fail' : '';  // 0=ERROR=FAIL
    const fieldDisplayName = FIELD_DISPLAY_NAMES[f.field_type] || f.field_name;

    return `<tr class="${rowCls}">
      <td class="field-name-cell">${escHtml(fieldDisplayName)}</td>
      ${sourceCells}
      <td>${statusBadge(displayFieldStatus)}</td>
      <td class="comp-cell">${compCell}</td>
    </tr>`;
  }).join('');

  const sourceHeaders = allSourceNames.map(s => `<th>${escHtml(s)}</th>`).join('');

  const fieldsCard = `
    <div class="card results-wide">
      <div class="card-title">Main Text Fields</div>
      ${tableRows
        ? `<div class="fields-table-wrap"><table class="fields-table-v2">
            <thead><tr>
              <th>Field</th>${sourceHeaders}<th>Validity</th><th>Comparison</th>
            </tr></thead>
            <tbody>${tableRows}</tbody>
          </table></div>`
        : '<p style="color:var(--text-3);font-size:13px">No text fields extracted</p>'}
    </div>`;

  return `
    ${header}
    ${summaryCard}
    ${cropsCard}
    ${fieldsCard}`;
}

// ── History view ─────────────────────────────────────────────
async function renderHistory(page) {
  const container = document.getElementById('history-content');
  if (!container) return;
  container.innerHTML = `<div class="spinner-wrap"><div class="spinner"></div><span>Loading sessions…</span></div>`;

  try {
    const res = await fetch(`${API_BASE}/sessions?page=${page}&page_size=${PAGE_SIZE}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentPage = page;
    container.innerHTML = renderHistoryTable(data);

    // Wire pagination
    container.querySelector('.js-prev')?.addEventListener('click', () => renderHistory(page - 1));
    container.querySelector('.js-next')?.addEventListener('click', () => renderHistory(page + 1));
  } catch (err) {
    container.innerHTML = `<div class="error-banner">Failed to load sessions: ${escHtml(err.message)}</div>`;
  }
}

function renderHistoryTable(data) {
  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  if (!data.sessions || data.sessions.length === 0) {
    return `<div class="empty-state">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity=".3">
        <path d="M9 12h6M9 16h6M7 4H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V6a2 2 0 00-2-2h-2"/>
        <path d="M9 4h6v4H9z"/>
      </svg>
      <p>No sessions yet. <a href="#process" style="color:var(--primary)">Process your first document.</a></p>
    </div>`;
  }

  const rows = data.sessions.map(s => {
    const name = [s.surname, s.given_names].filter(Boolean).join(', ') || '—';
    const docNum = s.document_number || '—';
    const docType = s.document_name
      ? `<span style="color:var(--text)">${escHtml(s.document_name)}</span>`
      : `<span style="color:var(--text-3)">—</span>`;
    const thumb = s.thumbnail_url
      ? `<img class="thumb" src="${escHtml(s.thumbnail_url)}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : `<span class="thumb-placeholder"></span>`;

    return `<tr onclick="navigate('#session/${escHtml(s.session_id)}')">
      <td>${thumb}</td>
      <td class="td-time">${fmtDate(s.created_at)}</td>
      <td class="td-doctype">${docType}</td>
      <td class="td-name">${escHtml(name)}</td>
      <td>${escHtml(docNum)}</td>
      <td>${statusBadge(s.overall_status)}</td>
    </tr>`;
  }).join('');

  const pagination = totalPages > 1 ? `
    <div class="pagination">
      <button class="btn btn-outline js-prev" ${currentPage <= 1 ? 'disabled' : ''}>← Prev</button>
      <span class="pagination-info">Page ${currentPage} of ${totalPages}</span>
      <button class="btn btn-outline js-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next →</button>
    </div>` : '';

  return `
    <div class="history-header">
      <div>
        <div class="page-title">Session History</div>
        <div class="history-count">${data.total} session${data.total !== 1 ? 's' : ''} total</div>
      </div>
      <button class="btn btn-primary" onclick="navigate('#process')">+ Process New</button>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div class="session-table-wrap">
        <table class="session-table">
          <thead>
            <tr>
              <th></th>
              <th>Date / Time</th>
              <th>Document Type</th>
              <th>Name</th>
              <th>Doc Number</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
    ${pagination}`;
}

// ── Detail view ───────────────────────────────────────────────
async function renderDetail(sessionId) {
  const container = document.getElementById('detail-results');
  if (!container) return;
  container.innerHTML = `<div class="spinner-wrap"><div class="spinner"></div><span>Loading session…</span></div>`;
  container.classList.remove('hidden');

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      container.innerHTML = `<div class="error-banner">${escHtml(err.message || `Session not found`)}</div>`;
      return;
    }
    const data = await res.json();
    showResults(data, true);
  } catch (err) {
    container.innerHTML = `<div class="error-banner">Failed to load session: ${escHtml(err.message)}</div>`;
  }
}

// ── Boot ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initProcessView();
});
