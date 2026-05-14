document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileReady = document.getElementById('file-ready');
    const filenameDisplay = document.getElementById('filename');
    const startBtn = document.getElementById('start-btn');
    const uploadSection = document.getElementById('upload-section');
    const editorView = document.getElementById('editor-view');
    const englishTextArea = document.getElementById('english-text-area');
    const bengaliTextArea = document.getElementById('bengali-text-area');
    const translateBtn = document.getElementById('translate-btn');
    const generateBtn = document.getElementById('generate-btn');
    const replaceHindiBtn = document.getElementById('replace-hindi-btn');
    const aiEngine = document.getElementById('ai-engine');
    const pdfLayout = document.getElementById('pdf-layout');
    const loader = document.getElementById('loader');
    const loaderMsg = document.getElementById('loader-msg');

    // Modal Elements
    const pdfModal = document.getElementById('pdf-modal');
    const pdfFrame = document.getElementById('pdf-frame');
    const closeModal = document.getElementById('close-modal');
    const finalDownloadBtn = document.getElementById('download-btn');
    const refreshPreview = document.getElementById('refresh-preview');

    let selectedFile = null;
    let uploadedFilename = null;
    let previewUrl = null;

    // --- Upload Logic ---

    dropZone.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });

    // Premium Drag & Drop Animations
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('active');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('active');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('active');
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    function handleFile(file) {
        if (file.type !== 'application/pdf') {
            alert('Please upload a PDF file.');
            return;
        }
        selectedFile = file;
        filenameDisplay.textContent = file.name;
        fileReady.classList.remove('hidden');
        
        // Success animation
        fileReady.classList.add('scale-in');
    }

    startBtn.addEventListener('click', async () => {
        if (!selectedFile) return;

        showLoader('Extracting English & Filtering Hindi...');
        
        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const text = await response.text();
                if (text.includes('<html>')) {
                    throw new Error(`Server Error (${response.status}): The server returned an HTML page instead of data. This often means the app crashed or the route is missing.`);
                }
                const errData = JSON.parse(text);
                throw new Error(errData.error || 'Upload failed');
            }

            const data = await response.json();
            if (data.error) {
                const msg = data.traceback ? `${data.error}\n\nTraceback:\n${data.traceback}` : data.error;
                throw new Error(msg);
            }

            englishTextArea.value = data.english_text;
            uploadedFilename = data.filename || selectedFile.name;
            uploadSection.classList.add('hidden');
            editorView.classList.remove('hidden');
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            hideLoader();
        }
    });

    // --- Translation Logic ---

    translateBtn.addEventListener('click', async () => {
        const text = englishTextArea.value.trim();
        
        if (!text) {
            alert('English text is empty.');
            return;
        }

        showLoader('Groq is translating to Bengali...');

        try {
            bengaliTextArea.value = '';
            let startIndex = 0;
            let done = false;
            let translatedParts = [];

            while (!done) {
                const response = await fetch('/translate_chunk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text, start_index: startIndex })
                });

                if (!response.ok) {
                    throw new Error(await parseErrorResponse(response, 'Translation failed'));
                }

                const data = await response.json();
                if (data.error) {
                    const msg = data.traceback ? `${data.error}\n\nTraceback:\n${data.traceback}` : data.error;
                    throw new Error(msg);
                }

                if (data.bengali_text) {
                    translatedParts.push(data.bengali_text);
                    bengaliTextArea.value = translatedParts.join('\n\n');
                }

                startIndex = data.next_index;
                done = data.done;
                loaderMsg.textContent = `Groq is translating to Bengali... ${Math.min(startIndex, data.total_blocks)} / ${data.total_blocks}`;
            }
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            hideLoader();
        }
    });

    // --- PDF Preview & Generation Logic ---

    async function handlePdfAction(preview = false) {
        const engText = englishTextArea.value.trim();
        const benText = bengaliTextArea.value.trim();
        const layout = pdfLayout.value;

        if (!benText) {
            alert('No translated text found. Please translate first.');
            return;
        }

        showLoader(preview ? 'Crafting your Preview...' : 'Generating Final PDF...');

        try {
            const response = await fetch('/generate_pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    english_text: engText, 
                    bengali_text: benText,
                    layout: layout,
                    preview: preview 
                })
            });

            if (!response.ok) {
                let errMsg = 'Failed to generate PDF';
                try {
                    const errData = await response.json();
                    errMsg = errData.error || errMsg;
                    if (errData.traceback) errMsg += '\n\n' + errData.traceback;
                } catch (e) {
                    const text = await response.text();
                    errMsg = text || errMsg;
                }
                throw new Error(errMsg);
            }

            const blob = await response.blob();
            
            // Clean up old URL
            if (previewUrl) window.URL.revokeObjectURL(previewUrl);
            previewUrl = window.URL.createObjectURL(blob);

            if (preview) {
                pdfFrame.src = previewUrl;
                pdfModal.classList.remove('hidden');
            } else {
                const a = document.createElement('a');
                a.href = previewUrl;
                a.download = 'Translated_Bengali_Result.pdf';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            }
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            hideLoader();
        }
    }

    generateBtn.addEventListener('click', () => handlePdfAction(true));
    refreshPreview.addEventListener('click', () => handlePdfAction(true));
    finalDownloadBtn.addEventListener('click', () => handlePdfAction(false));

    replaceHindiBtn.addEventListener('click', async () => {
        if (!uploadedFilename) {
            alert('Please upload and process a PDF first.');
            return;
        }

        showLoader('Replacing Hindi with Bengali in the original PDF...');

        try {
            const response = await fetch('/replace_hindi_pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: uploadedFilename })
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || 'Hindi replacement failed');
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'Hindi_Replaced_With_Bengali.pdf';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            hideLoader();
        }
    });
    
    // Auto-refresh preview when layout changes
    pdfLayout.addEventListener('change', () => {
        if (!pdfModal.classList.contains('hidden')) {
            handlePdfAction(true);
        }
    });

    // --- Modal Logic ---

    closeModal.addEventListener('click', () => {
        pdfModal.classList.add('hidden');
        pdfFrame.src = '';
    });

    // Close modal on escape
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            pdfModal.classList.add('hidden');
            pdfFrame.src = '';
        }
    });

    // --- Utilities ---
    
    window.clearAll = () => {
        // Reset Text
        englishTextArea.value = '';
        bengaliTextArea.value = '';
        
        // Reset File State
        selectedFile = null;
        uploadedFilename = null;
        fileInput.value = '';
        filenameDisplay.textContent = 'document_name.pdf';
        
        // UI Navigation
        editorView.classList.add('hidden');
        fileReady.classList.add('hidden');
        uploadSection.classList.remove('hidden');

        // Close modal if open
        pdfModal.classList.add('hidden');

        // Clean up preview
        if (previewUrl) window.URL.revokeObjectURL(previewUrl);
        previewUrl = null;
        pdfFrame.src = '';
    };

    function showLoader(msg) {
        loaderMsg.textContent = msg;
        loader.classList.remove('hidden');
        loader.classList.add('fade-in');
    }

    function hideLoader() {
        loader.classList.remove('fade-in');
        loader.classList.add('hidden');
    }

    async function parseErrorResponse(response, fallbackMessage) {
        const errorText = await response.text();
        if (errorText.toLowerCase().includes('<html')) {
            return `Server Error (${response.status}) from ${response.url}: Render returned an HTML error page. The request likely timed out, crashed, or the deployed start command is not using the current Procfile.`;
        }
        try {
            const errData = JSON.parse(errorText);
            return errData.error || fallbackMessage;
        } catch (e) {
            return errorText || fallbackMessage;
        }
    }

    window.copyText = (areaId, btn) => {
        const area = document.getElementById(areaId);
        const text = area.value;
        if (!text) return;

        // Modern async Clipboard API (replaces deprecated execCommand)
        navigator.clipboard.writeText(text).then(() => {
            if (!btn) return;
            const originalText = btn.textContent;
            btn.textContent = 'Copied!';
            btn.style.color = 'var(--acc-cyan)';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.style.color = '';
            }, 2000);
        }).catch(() => {
            // Fallback for insecure contexts (HTTP)
            area.select();
            document.execCommand('copy');
        });
    };
});
