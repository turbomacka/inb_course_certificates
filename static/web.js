/* Webbversionen: mallarna är gemensamma och sparas på servern. Intygen sparas
   bara i webbläsarens IndexedDB, på den här datorn; servern fyller i mallen och
   gör PDF:er (se web_mode.py) men sparar aldrig intygen. */
const Web = (() => {
    const DB_NAME = 'certifikatgenerator';
    const DB_VERSION = 1;
    const CHUNK = 5; // namn per förfrågan till servern
    const DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    let dbPromise = null;

    // --- IndexedDB -------------------------------------------------------

    function promisify(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    function openDb() {
        if (!dbPromise) {
            dbPromise = new Promise((resolve, reject) => {
                if (!window.indexedDB) {
                    reject(new Error('Webbläsaren kan inte spara data lokalt (IndexedDB saknas eller är avstängt).'));
                    return;
                }
                const request = indexedDB.open(DB_NAME, DB_VERSION);
                request.onupgradeneeded = () => {
                    const db = request.result;
                    db.createObjectStore('batches', { keyPath: 'id', autoIncrement: true });
                    const certs = db.createObjectStore('certificates', { keyPath: 'id', autoIncrement: true });
                    certs.createIndex('batchId', 'batchId');
                };
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(new Error('Kunde inte öppna webbläsarens lagring. I privat läge går det ibland inte att spara.'));
            });
        }
        return dbPromise;
    }

    async function getAll(store) {
        const db = await openDb();
        return promisify(db.transaction(store).objectStore(store).getAll());
    }

    async function get(store, id) {
        const db = await openDb();
        return promisify(db.transaction(store).objectStore(store).get(id));
    }

    /* Kör fn(transaction) och väntar tills allt är sparat. Om fn returnerar
       IDB-förfrågningar returneras deras resultat (t.ex. nya id:n). */
    async function write(stores, fn) {
        const db = await openDb();
        const tx = db.transaction(stores, 'readwrite');
        const out = fn(tx);
        await new Promise((resolve, reject) => {
            tx.oncomplete = resolve;
            tx.onerror = tx.onabort = () => {
                const error = tx.error;
                reject(error && error.name === 'QuotaExceededError'
                    ? new Error('Webbläsarens lagring är full. Ta bort gamla intyg och försök igen.')
                    : new Error('Kunde inte spara i webbläsaren.'));
            };
        });
        if (Array.isArray(out)) return out.map(r => r.result);
        return out instanceof IDBRequest ? out.result : out;
    }

    let persistRequested = false;
    function requestPersistence() {
        // Be webbläsaren att inte rensa lagringen automatiskt när disken blir full.
        if (persistRequested || !navigator.storage || !navigator.storage.persist) return;
        persistRequested = true;
        navigator.storage.persist().catch(() => {});
    }

    function now() {
        const d = new Date();
        const pad = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    // --- server-API ------------------------------------------------------

    async function api(path, formData = null, method = formData ? 'POST' : 'GET') {
        let response;
        try {
            response = await fetch(path, { method, body: formData, credentials: 'same-origin' });
        } catch (error) {
            throw new Error('Kunde inte nå servern. Kontrollera anslutningen och försök igen.');
        }
        if (response.status === 401) {
            location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
            throw new Error('Du är inte inloggad.');
        }
        if (!response.ok) {
            let message = `Servern svarade med ett fel (${response.status}).`;
            try {
                const data = await response.json();
                if (data.error) message = data.error;
            } catch (error) { /* inte JSON */ }
            throw new Error(message);
        }
        return response;
    }

    async function previewPdf(templateId, name, datum) {
        const form = new FormData();
        form.append('template_id', templateId);
        form.append('name', name);
        form.append('datum', datum);
        return (await api('/api/preview', form)).blob();
    }

    // --- gemensamma mallar (sparas på servern) ---------------------------

    async function listTemplates() {
        return (await (await api('/api/templates')).json()).templates;
    }

    async function addTemplate(file, name) {
        const form = new FormData();
        form.append('docx_file', file, file.name);
        form.append('name', name);
        return (await api('/api/templates', form)).json();
    }

    /* PDF av mallen; servern skapar den en gång och sparar den med mallen. */
    const templatePdfs = new Map();
    async function templatePdf(id) {
        if (!templatePdfs.has(id)) {
            const pending = api(`/api/templates/${id}/preview`).then(r => r.blob());
            templatePdfs.set(id, pending);
            pending.catch(() => templatePdfs.delete(id));
        }
        return templatePdfs.get(id);
    }

    function templateDocxUrl(id) {
        return `/api/templates/${id}/docx`;
    }

    async function deleteTemplate(id) {
        await api(`/api/templates/${id}`, null, 'DELETE');
        templatePdfs.delete(id);
    }

    // --- intyg -----------------------------------------------------------

    /* Skapar intyg i omgångar. onProgress(klara, totalt) anropas efter varje omgång.
       Returnerar { batchId, created, error } – error sätts om en omgång misslyckades. */
    async function generate(template, kurskod, datum, names, onProgress) {
        let batchId = null;
        let created = 0;
        try {
            for (let i = 0; i < names.length; i += CHUNK) {
                const part = names.slice(i, i + CHUNK);
                const form = new FormData();
                form.append('template_id', template.id);
                form.append('datum', datum);
                form.append('names', JSON.stringify(part));
                const zip = await JSZip.loadAsync(await (await api('/api/generate', form)).blob());
                const certs = [];
                for (let j = 0; j < part.length; j++) {
                    certs.push({
                        studentName: part[j],
                        docx: new Blob([await zip.file(`${j}.docx`).async('arraybuffer')], { type: DOCX_TYPE }),
                        pdf: new Blob([await zip.file(`${j}.pdf`).async('arraybuffer')], { type: 'application/pdf' }),
                    });
                }
                if (batchId === null) {
                    batchId = await write('batches', tx => tx.objectStore('batches').add({
                        kurskod, datum, templateName: template.name, createdAt: now(),
                    }));
                }
                const createdAt = now();
                await write('certificates', tx => certs.map(c => tx.objectStore('certificates').add({
                    ...c, batchId, kurskod, datum, templateName: template.name, createdAt,
                })));
                created += certs.length;
                requestPersistence();
                if (onProgress) onProgress(created, names.length);
            }
            return { batchId, created, error: null };
        } catch (error) {
            await deleteEmptyBatches().catch(() => {});
            return { batchId: created ? batchId : null, created, error };
        }
    }

    async function listCertificates(batchId = null, query = '') {
        const q = query.trim().toLowerCase();
        const certs = (await getAll('certificates')).filter(c =>
            (!batchId || c.batchId === batchId) &&
            (!q || c.studentName.toLowerCase().includes(q) || (c.kurskod || '').toLowerCase().includes(q)));
        return certs.sort((a, b) => b.batchId - a.batchId || a.id - b.id);
    }

    async function listBatches() {
        const [batches, certs] = await Promise.all([getAll('batches'), getAll('certificates')]);
        const counts = new Map();
        certs.forEach(c => counts.set(c.batchId, (counts.get(c.batchId) || 0) + 1));
        return batches.filter(b => counts.has(b.id))
            .map(b => ({ ...b, count: counts.get(b.id) }))
            .sort((a, b) => b.id - a.id);
    }

    async function getCertificates(ids) {
        const certs = await Promise.all(ids.map(id => get('certificates', id)));
        return certs.filter(Boolean).sort((a, b) => b.batchId - a.batchId || a.id - b.id);
    }

    async function deleteEmptyBatches() {
        const [batches, certs] = await Promise.all([getAll('batches'), getAll('certificates')]);
        const used = new Set(certs.map(c => c.batchId));
        const empty = batches.filter(b => !used.has(b.id));
        if (empty.length) await write('batches', tx => empty.forEach(b => tx.objectStore('batches').delete(b.id)));
    }

    async function deleteCertificates(ids) {
        await write('certificates', tx => ids.forEach(id => tx.objectStore('certificates').delete(id)));
        await deleteEmptyBatches();
        return ids.length;
    }

    // --- filnamn och nedladdning (samma regler som app.py) ---------------

    function safeFilenamePart(value) {
        value = String(value).replace(/[\\/:*?"<>|\x00-\x1f]/g, '').trim().replace(/^\.+|\.+$/g, '');
        return value.replace(/\s+/g, ' ') || 'okänd';
    }

    function certificateFilename(cert, ext) {
        return ['Certifikat', cert.kurskod, cert.studentName, cert.datum]
            .filter(Boolean).map(safeFilenamePart).join('_') + '.' + ext;
    }

    function download(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    async function downloadCertificates(certs, fmt) {
        if (certs.length === 1) {
            download(certs[0][fmt], certificateFilename(certs[0], fmt));
            return;
        }
        const zip = new JSZip();
        const used = new Set();
        for (const cert of certs) {
            let name = certificateFilename(cert, fmt);
            const stem = name.slice(0, -fmt.length - 1);
            for (let n = 2; used.has(name); n++) name = `${stem} (${n}).${fmt}`;
            used.add(name);
            zip.file(name, cert[fmt]);
        }
        const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
        download(blob, `intyg_${fmt}_${now().slice(0, 10)}.zip`);
    }

    // --- meddelanden -----------------------------------------------------

    function showAlert(message, category = 'success') {
        const alert = document.createElement('div');
        alert.className = `alert alert-${category} alert-dismissible fade show`;
        alert.setAttribute('role', 'alert');
        alert.textContent = message;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.dataset.bsDismiss = 'alert';
        close.setAttribute('aria-label', 'Stäng');
        alert.append(close);
        document.getElementById('alerts').append(alert);
    }

    /* Meddelande som visas på nästa sida (som flash() på servern). */
    function flashNext(message, category = 'success') {
        try { sessionStorage.setItem('flash', JSON.stringify({ message, category })); } catch (error) { /* ignoreras */ }
    }

    function showFlashed() {
        try {
            const flashed = JSON.parse(sessionStorage.getItem('flash') || 'null');
            sessionStorage.removeItem('flash');
            if (flashed) showAlert(flashed.message, flashed.category);
        } catch (error) { /* ignoreras */ }
    }

    /* Objekt-URL:er för förhandsvisning; släpps när listan ritas om. */
    const objectUrls = [];
    function objectUrl(blob) {
        const url = URL.createObjectURL(blob);
        objectUrls.push(url);
        return url;
    }
    function releaseObjectUrls() {
        objectUrls.splice(0).forEach(url => URL.revokeObjectURL(url));
    }

    function el(tag, attrs = {}, ...children) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs)) {
            if (key === 'class') node.className = value;
            else if (key === 'dataset') Object.assign(node.dataset, value);
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value);
        }
        node.append(...children);
        return node;
    }

    document.addEventListener('DOMContentLoaded', showFlashed);

    return {
        openDb, listTemplates, addTemplate, templatePdf, templateDocxUrl, deleteTemplate,
        previewPdf, generate, listCertificates, listBatches, getCertificates, deleteCertificates,
        certificateFilename, safeFilenamePart, download, downloadCertificates,
        showAlert, flashNext, objectUrl, releaseObjectUrls, el,
    };
})();
