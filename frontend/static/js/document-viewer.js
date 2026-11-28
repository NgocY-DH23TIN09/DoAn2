const documentViewer = document.getElementById("documentViewer");
const viewerOriginal = document.getElementById("documentViewerOriginal");
const viewerText = document.getElementById("documentViewerText");
const viewerOriginalButton = document.getElementById("documentViewerOriginalButton");
const viewerTextButton = document.getElementById("documentViewerTextButton");
const viewerDownload = document.getElementById("documentViewerDownload");
const viewerMedia = document.getElementById("documentViewerMedia");
const viewerStatus = document.getElementById("documentViewerStatus");

const inlineDocumentTypes = {
    "application/pdf": [0x25, 0x50, 0x44, 0x46, 0x2d],
    "image/jpeg": [0xff, 0xd8, 0xff],
    "image/png": [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
};
const wordDocumentType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const previewMimesByExtension = {
    ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".docx": wordDocumentType
};

let documentViewerState;

function openDocumentViewer(doc, trigger) {
    if (documentViewer.open) return;
    clearDocumentViewer();
    const filename = doc.file_name || doc.file_path || "";
    const extension = filename.toLowerCase().match(/\.[a-z0-9]+$/)?.[0];
    const mime = doc.file_mime_type || previewMimesByExtension[extension];
    const isWord = mime === wordDocumentType;
    const supportsPreview = Boolean(doc.file_path && (Object.hasOwn(inlineDocumentTypes, mime) || isWord));
    const title = documentDisplayName(doc);
    documentViewerState = { doc, title, trigger, mime, isWord, controller: new AbortController() };
    document.getElementById("documentViewerTitle").textContent = title;
    document.getElementById("documentViewerExtracted").textContent = doc.content?.trim()
        || "Tài liệu này chưa có nội dung chữ trích xuất.";
    document.getElementById("documentViewerModes").hidden = !supportsPreview;
    viewerOriginalButton.textContent = isWord ? "Bố cục Word" : "Bản gốc";
    viewerDownload.hidden = !doc.file_path;
    viewerOriginal.scrollTop = 0;
    viewerText.scrollTop = 0;
    selectDocumentViewerMode(supportsPreview);
    documentViewer.showModal();
}

function selectDocumentViewerMode(showOriginal) {
    viewerOriginal.hidden = !showOriginal;
    viewerText.hidden = showOriginal;
    viewerOriginalButton.setAttribute("aria-pressed", String(showOriginal));
    viewerTextButton.setAttribute("aria-pressed", String(!showOriginal));
    if (showOriginal && !documentViewerState.loadingStarted) {
        loadDocumentPreview(documentViewerState);
    }
}

async function loadDocumentPreview(state) {
    state.loadingStarted = true;
    viewerStatus.textContent = "Đang tải bản xem trước…";
    viewerStatus.hidden = false;
    const action = state.isWord ? "word-preview" : "file";
    try {
        const response = await fetch(`/api/documents/${state.doc.id}/${action}`, {
            headers: getAuthHeaders(),
            signal: state.controller.signal
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || "Không tải được bản xem trước.");
        }
        const preview = state.isWord
            ? renderWordPreview((await response.json()).blocks)
            : await renderInlineDocument(await response.blob(), state);
        if (state.controller.signal.aborted) return;
        viewerMedia.replaceChildren(preview);
        viewerMedia.hidden = false;
        viewerStatus.textContent = "";
        viewerStatus.hidden = true;
    } catch (error) {
        if (state.controller.signal.aborted) return;
        viewerStatus.textContent = `${error.message} Bạn có thể chuyển sang Nội dung trích xuất hoặc tải bản gốc.`;
    }
}

async function renderInlineDocument(blob, state) {
    const { mime } = state;
    const signature = inlineDocumentTypes[mime];
    const prefix = new Uint8Array(await blob.slice(0, signature.length).arrayBuffer());
    if (!signature.every((byte, index) => byte === prefix[index])) {
        throw new Error("Tệp không đúng định dạng để xem trước.");
    }
    if (state.controller.signal.aborted) return;
    state.objectUrl = URL.createObjectURL(new Blob([blob], { type: mime }));
    if (mime === "application/pdf") {
        const frame = document.createElement("iframe");
        frame.className = "document-viewer-frame";
        frame.title = `Bản PDF của ${state.title}`;
        frame.src = state.objectUrl;
        return frame;
    }
    const image = document.createElement("img");
    image.className = "mx-auto block max-w-full p-4";
    image.alt = state.title;
    image.src = state.objectUrl;
    return image;
}

function renderWordPreview(blocks) {
    if (!blocks.length) throw new Error("Tệp Word không có đoạn văn hoặc bảng có thể hiển thị.");
    const article = document.createElement("article");
    article.className = "mx-auto max-w-4xl space-y-3 p-5 text-sm leading-7 [overflow-wrap:anywhere] sm:px-8";
    for (const block of blocks) {
        if (block.type === "table") {
            const table = document.createElement("table");
            table.className = "w-full border-collapse whitespace-pre-wrap";
            const body = table.createTBody();
            for (const cells of block.rows) {
                const row = body.insertRow();
                for (const text of cells) {
                    const cell = row.insertCell();
                    cell.className = "border border-slate-300 px-3 py-1 align-top";
                    cell.textContent = text;
                }
            }
            article.appendChild(table);
            continue;
        }
        const heading = block.type === "heading";
        const paragraph = document.createElement(heading ? "h3" : "p");
        paragraph.className = heading ? "whitespace-pre-wrap text-base font-bold" : "whitespace-pre-wrap";
        paragraph.style.textAlign = block.alignment;
        paragraph.textContent = block.text;
        article.appendChild(paragraph);
    }
    return article;
}

function clearDocumentViewer() {
    if (!documentViewerState) return;
    documentViewerState.controller.abort();
    if (documentViewerState.objectUrl) URL.revokeObjectURL(documentViewerState.objectUrl);
    viewerMedia.replaceChildren();
    viewerMedia.hidden = true;
    documentViewerState = undefined;
}

viewerOriginalButton.addEventListener("click", () => selectDocumentViewerMode(true));
viewerTextButton.addEventListener("click", () => selectDocumentViewerMode(false));
viewerDownload.addEventListener("click", () => downloadDocumentFile(documentViewerState.doc.id));
documentViewer.addEventListener("close", () => {
    if (documentViewer.open) return;
    const trigger = documentViewerState?.trigger;
    clearDocumentViewer();
    trigger?.focus();
});
