(() => {
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const form = document.getElementById('search-form');
  const statusBox = document.getElementById('status');
  const results = document.getElementById('results');
  const body = document.getElementById('results-body');
  const empty = document.getElementById('results-empty');
  const summary = document.getElementById('results-summary');
  const selectAll = document.getElementById('select-all');
  const downloadButton = document.getElementById('download');
  const mailboxSelect = document.getElementById('mailbox');
  const folderFilter = document.getElementById('folder-filter');
  const foldersBox = document.getElementById('mailbox-folders');
  const tableWrap = results?.querySelector('.table-wrap');
  let currentSearch = null;

  const invoiceTypes = new Set(['agua', 'luz', 'gas']);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const formatDate = (value) => value ? new Intl.DateTimeFormat('es-ES', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(value)) : '—';
  const setEmptyState = (visible) => {
    empty.hidden = !visible;
    empty.style.display = visible ? 'grid' : 'none';
  };

  const setStatus = (text, tone = '', busy = false) => {
    statusBox.hidden = false;
    statusBox.className = `notice-module grid-span-12 ${tone}`;
    statusBox.setAttribute('aria-busy', String(busy));
    statusBox.innerHTML = `<span class="status-message">${busy ? '<span class="loading-indicator" aria-hidden="true"></span>' : ''}<span>${escapeHtml(text)}</span></span>`;
  };

  const setDefaults = () => {
    const end = new Date();
    const start = new Date(end);
    start.setMonth(start.getMonth() - 1);
    document.getElementById('end-date').value = end.toISOString().slice(0, 10);
    document.getElementById('start-date').value = start.toISOString().slice(0, 10);
  };

  async function getJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'No se pudo completar la operación.');
    return data;
  }

  const setSearchBusy = (busy) => {
    form?.setAttribute('aria-busy', String(busy));
    form?.querySelectorAll('input, select, button').forEach((control) => { control.disabled = busy; });
    const submit = form?.querySelector('button[type=submit]');
    if (submit) submit.innerHTML = busy ? '<span class="button-spinner" aria-hidden="true"></span> Buscando…' : 'Buscar facturas <span aria-hidden="true">→</span>';
  };

  const setDownloadBusy = (busy) => {
    [selectAll, downloadButton].forEach((control) => { if (control) control.disabled = busy; });
    results?.querySelectorAll('.candidate').forEach((control) => { control.disabled = busy; });
    results?.setAttribute('aria-busy', String(busy));
  };

  const filterFolders = () => {
    const query = folderFilter?.value.trim().toLowerCase() || '';
    const choices = [...(foldersBox?.querySelectorAll('.folder-choice') || [])];
    let visible = 0;
    choices.forEach((choice) => {
      const matches = !query || choice.dataset.folderName.includes(query);
      choice.hidden = !matches;
      if (matches) visible += 1;
    });
    const empty = foldersBox?.querySelector('.folder-filter-empty');
    if (empty) empty.hidden = visible > 0;
  };

  const renderFolders = (folders, defaultFolder) => {
    const selectedByDefault = new Set(['INBOX', defaultFolder].filter(Boolean));
    foldersBox.innerHTML = `${folders.map((folder) => `<label class="folder-choice" data-folder-name="${escapeHtml(folder.name.toLowerCase())}"><input class="mailbox-folder" type="checkbox" value="${escapeHtml(folder.name)}"${selectedByDefault.has(folder.name) ? ' checked' : ''}><span><strong>${escapeHtml(folder.name)}</strong>${folder.has_children ? '<small>Contiene subcarpetas</small>' : ''}</span></label>`).join('')}<span class="inline-status folder-filter-empty" hidden>No hay carpetas con ese nombre.</span>`;
    filterFolders();
  };

  const loadFolders = async () => {
    const mailboxId = mailboxSelect?.value;
    if (!mailboxId || !foldersBox) return;
    foldersBox.innerHTML = '<span class="inline-status">Cargando carpetas IMAP…</span>';
    try {
      const data = await getJson(`/api/mailboxes/${mailboxId}/folders`);
      renderFolders(data.folders || [], data.default_folder);
      if (!data.folders?.length) foldersBox.innerHTML = '<span class="inline-status">El buzón no ha devuelto carpetas.</span>';
    } catch (error) {
      foldersBox.innerHTML = `<label class="folder-choice" data-folder-name="inbox"><input class="mailbox-folder" type="checkbox" value="INBOX" checked><span><strong>INBOX</strong><small>No se pudieron cargar las subcarpetas</small></span></label><span class="field-error">${escapeHtml(error.message)}</span>`;
    }
  };

  async function pollSearch(id) {
    try {
      const data = await getJson(`/api/searches/${id}`);
      if (data.status === 'queued' || data.status === 'running') {
        setStatus(data.status === 'queued' ? 'Búsqueda en cola. Conectando con el buzón…' : 'Analizando mensajes y buscando facturas…', '', true);
        window.setTimeout(() => pollSearch(id), 1500);
        return;
      }
      if (data.status === 'failed') {
        setStatus(`La búsqueda falló: ${data.error || 'error desconocido'}`, 'error');
        setSearchBusy(false);
        return;
      }
      if (data.status !== 'completed') return;

      setStatus(`Búsqueda completada · ${data.total_found || 0} coincidencias`, 'success');
      const resultData = await getJson(`/api/searches/${id}/results`);
      results.hidden = false;
      const hasResults = resultData.results.length > 0;
      if (tableWrap) tableWrap.hidden = !hasResults;
      body.innerHTML = resultData.results.map((item) => `<tr><td class="check-column"><input type="checkbox" class="candidate" value="${escapeHtml(item.id)}" checked aria-label="Seleccionar ${escapeHtml(item.subject)}"></td><td>${escapeHtml(item.invoice_type)}</td><td>${escapeHtml(item.provider)}</td><td>${escapeHtml(item.subject)}<small class="table-subline">${escapeHtml(item.sender)}</small></td><td>${formatDate(item.received_at)}</td><td>${escapeHtml(item.links)}</td></tr>`).join('');
      setEmptyState(!hasResults);
      summary.textContent = hasResults ? `${resultData.results.length} coincidencias · selecciona los documentos que quieres descargar.` : 'No se encontraron mensajes que coincidan con los filtros.';
      results.scrollIntoView({behavior: 'smooth', block: 'start'});
      setSearchBusy(false);
    } catch (error) {
      setStatus(error.message, 'error');
      setSearchBusy(false);
    }
  }

  const uniqueFileName = (fileName, usedNames) => {
    if (!usedNames.has(fileName)) { usedNames.add(fileName); return fileName; }
    const dot = fileName.lastIndexOf('.');
    const stem = dot > 0 ? fileName.slice(0, dot) : fileName;
    const extension = dot > 0 ? fileName.slice(dot) : '';
    let index = 2;
    let candidate = `${stem}-${index}${extension}`;
    while (usedNames.has(candidate)) { index += 1; candidate = `${stem}-${index}${extension}`; }
    usedNames.add(candidate);
    return candidate;
  };

  async function saveDocumentsToFolder(documents, spreadsheets, folderName) {
    if (!window.showDirectoryPicker) throw new Error('Tu navegador no permite elegir una carpeta local. Descarga el ZIP como alternativa.');
    const directory = await window.showDirectoryPicker({mode: 'readwrite'});
    const rootDirectory = await directory.getDirectoryHandle(folderName, {create: true});
    const types = [...new Set(documents.map((document) => document.invoice_type))];
    if (types.some((type) => !invoiceTypes.has(type))) throw new Error('La descarga contiene un tipo de factura no válido.');
    const useTypeFolders = types.length > 1;
    const usedNamesByFolder = new Map();
    const folders = new Map();

    for (const document of documents) {
      const folderKey = useTypeFolders ? document.invoice_type : 'root';
      let targetDirectory = folders.get(folderKey);
      if (!targetDirectory) {
        targetDirectory = useTypeFolders ? await rootDirectory.getDirectoryHandle(folderKey, {create: true}) : rootDirectory;
        folders.set(folderKey, targetDirectory);
        usedNamesByFolder.set(folderKey, new Set());
      }
      const response = await fetch(document.download_url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`No se pudo descargar ${document.file_name}.`);
      const usedNames = usedNamesByFolder.get(folderKey);
      const handle = await targetDirectory.getFileHandle(uniqueFileName(document.file_name, usedNames), {create: true});
      const writable = await handle.createWritable();
      await writable.write(await response.blob());
      await writable.close();
    }

    for (const spreadsheet of spreadsheets) {
      const folderKey = useTypeFolders ? spreadsheet.invoice_type : 'root';
      let targetDirectory = folders.get(folderKey);
      if (!targetDirectory) {
        targetDirectory = useTypeFolders ? await rootDirectory.getDirectoryHandle(folderKey, {create: true}) : rootDirectory;
        folders.set(folderKey, targetDirectory);
        usedNamesByFolder.set(folderKey, new Set());
      }
      const response = await fetch(spreadsheet.download_url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`No se pudo descargar ${spreadsheet.file_name}.`);
      const handle = await targetDirectory.getFileHandle(uniqueFileName(spreadsheet.file_name, usedNamesByFolder.get(folderKey)), {create: true});
      const writable = await handle.createWritable();
      await writable.write(await response.blob());
      await writable.close();
    }
  }

  function renderDownloadReady(id, data) {
    const documents = data.items.filter((item) => item.status === 'downloaded' && item.download_url);
    const spreadsheets = data.spreadsheets || [];
    const links = [];
    if (documents.length && window.showDirectoryPicker) links.push(`<button class="button button-primary" id="save-to-folder" type="button">Elegir carpeta y guardar PDFs y Excel</button>`);
    if (data.zip_available) links.push(`<a class="button button-secondary" href="/api/downloads/${id}/file">Descargar ZIP</a>`);
    setDownloadBusy(false);
    if (!links.length) { setStatus(data.error || 'No se pudo preparar ningún documento.', 'error'); return; }
    statusBox.innerHTML = `<span class="status-message"><span>Descarga lista · ${documents.length} PDF${documents.length === 1 ? '' : 's'} en ${escapeHtml(data.folder_name)}. El servidor los eliminará al entregarlos.</span><span class="status-actions">${links.join('')}</span></span>`;
    statusBox.className = 'notice-module grid-span-12 success';
    statusBox.setAttribute('aria-busy', 'false');
    document.getElementById('save-to-folder')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = 'Guardando PDFs y Excel…';
      try {
        await saveDocumentsToFolder(documents, spreadsheets, data.folder_name);
        button.textContent = 'PDFs y Excel guardados';
        setStatus(`Se han guardado ${documents.length} PDF${documents.length === 1 ? '' : 's'} y ${spreadsheets.length} Excel en ${data.folder_name}.`, 'success');
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Elegir carpeta y guardar PDFs y Excel';
        if (error.name === 'AbortError') return;
        setStatus(error.message, 'error');
      }
    });
  }

  async function pollDownload(id) {
    try {
      const data = await getJson(`/api/downloads/${id}`);
      if (data.status === 'queued' || data.status === 'running') {
        setStatus(data.status === 'queued' ? 'Descarga en cola…' : 'Preparando documentos para descargar…', '', true);
        window.setTimeout(() => pollDownload(id), 1500);
        return;
      }
      if (data.status === 'completed') renderDownloadReady(id, data);
      else { setDownloadBusy(false); setStatus(data.error || 'No se pudo preparar ningún documento.', 'error'); }
    } catch (error) {
      setDownloadBusy(false);
      setStatus(error.message, 'error');
    }
  }

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const types = [...document.querySelectorAll('.invoice-type:checked')].map((input) => input.value);
    if (!types.length) { setStatus('Selecciona al menos un tipo de factura.', 'error'); return; }
    const folders = [...document.querySelectorAll('.mailbox-folder:checked')].map((input) => input.value);
    if (!folders.length) { setStatus('Selecciona al menos una carpeta de origen.', 'error'); return; }
    setSearchBusy(true);
    results.hidden = true;
    body.innerHTML = '';
    setEmptyState(false);
    setStatus('Conectando con el buzón y poniendo la búsqueda en cola…', '', true);
    try {
      const data = await getJson('/api/searches', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({mailbox_id: mailboxSelect.value, start_date: document.getElementById('start-date').value, end_date: document.getElementById('end-date').value, invoice_types: types, folders})});
      currentSearch = data.id;
      pollSearch(currentSearch);
    } catch (error) {
      setSearchBusy(false);
      setStatus(error.message, 'error');
    }
  });

  selectAll?.addEventListener('click', () => {
    const candidates = [...document.querySelectorAll('.candidate')];
    const shouldCheck = candidates.some((input) => !input.checked);
    candidates.forEach((input) => { input.checked = shouldCheck; });
  });

  downloadButton?.addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('.candidate:checked')].map((input) => Number(input.value));
    if (!ids.length) { setStatus('Selecciona al menos una coincidencia para descargar.', 'error'); return; }
    setDownloadBusy(true);
    setStatus('Iniciando descarga y preparando documentos…', '', true);
    try {
      const data = await getJson(`/api/searches/${currentSearch}/download`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({candidate_ids: ids})});
      pollDownload(data.id);
    } catch (error) {
      setDownloadBusy(false);
      setStatus(error.message, 'error');
    }
  });

  mailboxSelect?.addEventListener('change', loadFolders);
  folderFilter?.addEventListener('input', filterFolders);
  setDefaults();
  loadFolders();
})();

