let allDocuments = [];
let selectedDocId = null;
let currentCategoryFilter = "";
let documentListRequest;
let analysisRequest;
let proposalDocumentId;
let proposalPending = false;
let canReviewDocuments = false;
let canDeleteDocuments = false;
const documentKinds = {
    QuyetDinh: "Quyết định", CongVan: "Công văn", BienBan: "Biên bản", ThongBao: "Thông báo",
    KeHoach: "Kế hoạch", BaoCao: "Báo cáo", BieuMau: "Biểu mẫu", GiaoTrinh: "Giáo trình",
    BaiGiang: "Bài giảng", TaiLieu: "Cần phân loại"
};

function getAuthHeaders(extra = {}) {
    const token = localStorage.getItem("access_token");
    return { ...extra, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
}

function normalizeDocumentSearch(value) {
    return String(value ?? "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/\p{M}/gu, "")
        .replace(/đ/g, "d")
        .replace(/[^\p{L}\p{N}]+/gu, " ")
        .trim();
}

function documentDisplayName(doc) {
    const name = doc.storage_provider === "google_drive" ? doc.file_name || doc.title : doc.title || doc.file_name;
    return name || "Tài liệu không tên";
}

function displayStatus(status) {
    return ({
        pending: "AWAITING_REVIEW",
        approved: "READY",
        rejected: "REJECTED"
    })[status] || status || "AWAITING_REVIEW";
}

document.addEventListener("DOMContentLoaded", () => {
    if (!localStorage.getItem("access_token")) {
        window.location.href = "/login";
        return;
    }

    loadDocuments();
    loadLibraryStorage();
    fetch("/api/auth/me", { headers: getAuthHeaders() })
        .then(response => response.ok ? response.json() : null)
        .then(user => {
            canReviewDocuments = ["admin", "manager"].includes(user?.role) &&
                user.permissions?.includes("documents") && user.permissions?.includes("assistant");
            canDeleteDocuments = user?.role === "admin";
            updateProposalControls();
            updateDeleteDocumentControl();
        }).catch(() => {
            const hint = document.getElementById("deleteDocumentPermissionHint");
            hint.hidden = false;
            hint.textContent = "Không kiểm tra được quyền xóa. Hãy tải lại trang hoặc đăng nhập lại.";
        });
    const searchInput = document.getElementById("searchInput");
    searchInput?.addEventListener("input", renderDocuments);
});

async function loadLibraryStorage(method = "GET") {
    const status = document.getElementById("libraryStorageStatus");
    const activate = document.getElementById("activateLibraryDrive");
    const driveLink = document.getElementById("openLibraryDrive");
    activate.disabled = true;
    if (method === "POST") status.textContent = "Đang tạo thư mục thư viện trên Drive…";
    try {
        const response = await fetch("/api/v1/documents/storage", { method, headers: getAuthHeaders() });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không kiểm tra được kho Google Drive.");
        const counts = Number.isInteger(data.drive_file_count) && Number.isInteger(data.drive_folder_count)
            ? ` Google Drive có ${data.drive_file_count} tệp và ${data.drive_folder_count} thư mục trong thư viện.`
            : data.inventory_error ? ` Không đếm được nội dung Drive: ${data.inventory_error}` : "";
        status.textContent = `${data.message}${counts}`;
        document.getElementById("addDocument").disabled = !data.ready;
        activate.hidden = !data.can_activate;
        document.getElementById("connectLibraryGoogle").hidden = !data.show_google_link;
        driveLink.hidden = !data.folder_url;
        if (data.folder_url) driveLink.href = data.folder_url;
    } catch (error) {
        status.textContent = error.message;
    } finally {
        activate.disabled = false;
    }
}

document.getElementById("activateLibraryDrive").addEventListener("click", () => loadLibraryStorage("POST"));

const refreshDriveNames = document.getElementById("refreshDriveNames");
const driveNameStatus = document.getElementById("driveNameStatus");
refreshDriveNames.addEventListener("click", async () => {
    await loadDocuments({ announceRefresh: true });
    await loadLibraryStorage();
});

async function loadDocuments({ announceRefresh = false } = {}) {
    documentListRequest?.abort();
    const request = new AbortController();
    documentListRequest = request;
    refreshDriveNames.disabled = true;
    driveNameStatus.textContent = announceRefresh ? "Đang quét tệp trên Google Drive…" : "";
    try {
        const response = await fetch("/api/v1/documents?drive_only=true", { headers: getAuthHeaders(), signal: request.signal });
        if (response.status === 401) {
            localStorage.removeItem("access_token");
            window.location.href = "/login";
            return;
        }
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể tải danh sách tài liệu.");
        if (request.signal.aborted) return;
        allDocuments = Array.isArray(data) ? data.map(doc => ({
            ...doc,
            searchFields: [
                doc.title, doc.file_name, doc.doc_number, doc.category, doc.storage_category,
                documentKinds[doc.document_kind], doc.ai_summary, doc.content
            ].map(normalizeDocumentSearch)
        })) : [];
        renderCategoryFilters();
        renderDocuments();
        const activeDocument = allDocuments.find(doc => doc.id === selectedDocId);
        if (activeDocument) renderDocumentFileMetadata(activeDocument);
        const driveDocuments = allDocuments.filter(doc => doc.storage_provider === "google_drive");
        const unverifiedCount = driveDocuments.filter(doc => doc.drive_name_verified === false).length;
        if (unverifiedCount) {
            driveNameStatus.textContent = `${unverifiedCount} tệp đang hiển thị tên đã lưu vì chưa kiểm tra được tên hiện tại trên Drive.`;
        } else if (announceRefresh) {
            driveNameStatus.textContent = "Đã quét lại danh sách tài liệu trên Drive.";
        }
    } catch (error) {
        if (request.signal.aborted) return;
        driveNameStatus.textContent = announceRefresh ? error.message : "";
        document.getElementById("documentList").textContent = `Lỗi tải dữ liệu: ${error.message}`;
    } finally {
        if (documentListRequest === request) refreshDriveNames.disabled = false;
    }
}

function renderCategoryFilters() {
    const list = document.getElementById("categoryFilterList");
    const groups = [...new Set(allDocuments.map(doc => doc.category).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, "vi"));
    if (!groups.includes(currentCategoryFilter)) currentCategoryFilter = "";

    const items = ["", ...groups].map(group => {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cat-btn w-full text-left px-3 py-2 rounded-lg text-sm transition hover:bg-slate-50 break-words";
        button.classList.toggle("active", group === currentCategoryFilter);
        button.setAttribute("aria-pressed", String(group === currentCategoryFilter));
        button.textContent = group || "Tất cả tài liệu";
        button.addEventListener("click", () => {
            currentCategoryFilter = group;
            renderCategoryFilters();
            renderDocuments();
            list.querySelector('[aria-pressed="true"]')?.focus();
        });
        item.appendChild(button);
        return item;
    });
    list.replaceChildren(...items);
}

function renderDocuments() {
    const keyword = normalizeDocumentSearch(document.getElementById("searchInput")?.value);
    const statusFilter = document.getElementById("statusFilter")?.value || "";
    const filtered = allDocuments.filter(doc => {
        const matchesKeyword = !keyword || doc.searchFields.some(field => field.includes(keyword));
        const matchesCategory = !currentCategoryFilter || doc.category === currentCategoryFilter;
        const matchesStatus = !statusFilter || doc.status === statusFilter;
        return matchesKeyword && matchesCategory && matchesStatus;
    });

    document.getElementById("docCount").textContent = filtered.length;
    const container = document.getElementById("documentList");
    if (filtered.length === 0) {
        container.innerHTML = '<p class="p-6 text-xs text-slate-400 text-center">Không tìm thấy tài liệu nào phù hợp.</p>';
        return;
    }

    container.innerHTML = filtered.map(doc => {
        const selected = doc.id === selectedDocId;
        const name = escapeHtml(documentDisplayName(doc));
        const status = doc.status;
        const badgeClass = status === "approved"
            ? "bg-emerald-100 text-emerald-700"
            : status === "rejected"
                ? "bg-rose-100 text-rose-700"
                : "bg-amber-100 text-amber-800";
        const badgeText = status === "approved"
            ? "Đã duyệt"
            : status === "rejected" ? "Từ chối" : "Chờ duyệt";
        return `
            <div onclick="selectDocument('${doc.id}')"
                class="p-4 cursor-pointer transition hover:bg-slate-50 ${selected ? "bg-indigo-50/70 border-l-4 border-indigo-600" : ""}">
                <div class="flex items-center justify-between gap-2 mb-1">
                    <span class="text-sm font-semibold text-slate-800 truncate" title="${name}">${name}</span>
                    <span class="text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ${badgeClass}">${badgeText}</span>
                </div>
                <p class="text-xs text-slate-500 truncate">${escapeHtml(doc.ai_summary || "Chưa có tóm tắt AI")}</p>
                <div class="text-[11px] text-slate-500 mt-1.5 flex flex-wrap items-center justify-between gap-x-2">
                    <span>Danh mục: ${escapeHtml(doc.category || "Chưa có nhóm")}</span>
                    <span class="flex flex-wrap items-center gap-x-2">
                        ${doc.file_path || doc.content?.trim() ? `<button type="button" onclick="event.stopPropagation(); viewLibraryDocument('${doc.id}', this)" aria-label="Xem tài liệu: ${name}" class="rounded-md px-2 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 focus-visible:ring-2 focus-visible:ring-indigo-500">Xem tài liệu</button>` : ""}
                        ${doc.file_path ? `<button type="button" onclick="event.stopPropagation(); downloadDocumentFile('${doc.id}')" class="rounded-md px-2 py-1.5 text-indigo-700 hover:underline focus-visible:ring-2 focus-visible:ring-indigo-500">Tải tệp</button>` : ""}
                        ID: #${escapeHtml(doc.id)}
                    </span>
                </div>
            </div>`;
    }).join("");
}

function viewLibraryDocument(docId, trigger) {
    const doc = allDocuments.find(item => item.id === docId);
    if (doc) openDocumentViewer(doc, trigger);
}

document.getElementById("btnViewDocument").addEventListener("click", event => {
    viewLibraryDocument(selectedDocId, event.currentTarget);
});

document.getElementById("btnDeleteDocument").addEventListener("click", deleteSelectedDocument);

function updateDeleteDocumentControl() {
    const doc = allDocuments.find(item => item.id === selectedDocId);
    const isDriveDocument = doc?.storage_provider === "google_drive";
    const deleteButton = document.getElementById("btnDeleteDocument");
    const permissionHint = document.getElementById("deleteDocumentPermissionHint");
    deleteButton.hidden = !canDeleteDocuments || !isDriveDocument;
    permissionHint.hidden = canDeleteDocuments || !isDriveDocument;
    permissionHint.textContent = isDriveDocument
        ? "Chỉ tài khoản quản trị mới có quyền xóa tài liệu."
        : "";
}

async function deleteSelectedDocument() {
    const doc = allDocuments.find(item => item.id === selectedDocId);
    if (!doc || !canDeleteDocuments || doc.storage_provider !== "google_drive") return;
    const name = documentDisplayName(doc);
    if (!window.confirm(
        `Chuyển riêng tệp "${name}" vào Thùng rác Google Drive và xóa bản ghi này khỏi ứng dụng? Thư mục Drive và các tệp khác sẽ được giữ nguyên.`
    )) return;

    const button = document.getElementById("btnDeleteDocument");
    const status = document.getElementById("deleteDocumentStatus");
    button.disabled = true;
    status.textContent = "Đang xóa tệp trên Drive…";
    try {
        const response = await fetch(`/api/v1/documents/${doc.id}`, {
            method: "DELETE",
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể xóa tài liệu.");

        selectedDocId = null;
        document.getElementById("detailPanel").classList.add("hidden");
        document.getElementById("emptyDetailState").classList.remove("hidden");
        await loadDocuments({ announceRefresh: true });
        await loadLibraryStorage();
        driveNameStatus.textContent = "Đã chuyển tệp vào Thùng rác Google Drive và xóa bản ghi khỏi ứng dụng.";
    } catch (error) {
        status.textContent = error.message;
    } finally {
        button.disabled = false;
        updateDeleteDocumentControl();
    }
}

function renderDocumentFileMetadata(doc) {
    document.getElementById("detailTitle").textContent = documentDisplayName(doc);
    const storage = document.getElementById("detailStorage");
    storage.hidden = doc.storage_provider !== "google_drive";
    storage.textContent = storage.hidden ? "" : `Google Drive / ${doc.storage_category || doc.category} / ${doc.file_name}`;
}

async function selectDocument(docId) {
    analysisRequest?.abort();
    analysisRequest = undefined;
    proposalDocumentId = undefined;
    selectedDocId = docId;
    renderDocuments();
    const doc = allDocuments.find(item => item.id === docId);
    if (!doc) return;
    document.getElementById("askSelectedDocument").disabled = false;
    document.getElementById("proposedTitleInput").value = "";
    document.getElementById("proposedCategoryInput").value = "";
    document.getElementById("evidenceQuote").textContent = "Chưa có đề xuất.";
    document.getElementById("proposalActionStatus").textContent = "";
    document.getElementById("btnAnalyze").textContent = "Phân tích lại";
    document.getElementById("btnAnalyze").disabled = false;
    updateProposalControls();

    document.getElementById("emptyDetailState").classList.add("hidden");
    document.getElementById("detailPanel").classList.remove("hidden");
    renderDocumentFileMetadata(doc);
    document.getElementById("detailMeta").textContent =
        `Danh mục hiện tại: ${doc.category || "Chưa có nhóm"}`;
    document.getElementById("summaryContent").textContent =
        doc.ai_summary || (doc.content ? "Nhấn 'Tạo tóm tắt AI' để tóm tắt nội dung." : "Chưa có nội dung văn bản để tóm tắt.");
    document.getElementById("btnSummarize").hidden = !doc.content;
    document.getElementById("btnViewDocument").hidden = !(doc.file_path || doc.content?.trim());
    const driveFile = doc.storage_provider === "google_drive";
    updateDeleteDocumentControl();
    const extraction = document.getElementById("detailExtraction");
    const extractionNotes = doc.ocr_pages ? [`OCR tiếng Việt đã đọc ${doc.ocr_pages}/${doc.page_count} trang`] : [];
    if (doc.extraction_warnings?.length) extractionNotes.push(...doc.extraction_warnings);
    extraction.hidden = !extractionNotes.length;
    extraction.textContent = extractionNotes.join(" · ");
    document.getElementById("reanalyzeStatus").textContent = "";
    const status = doc.status;
    const badge = document.getElementById("detailBadge");
    badge.textContent = status === "approved"
        ? "Đã duyệt"
        : status === "rejected" ? "Từ chối" : "Chờ duyệt";
    badge.className = status === "approved"
        ? "inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 mb-1"
        : status === "rejected"
            ? "inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 mb-1"
            : "inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 mb-1";
    document.getElementById("btnAnalyze").hidden = driveFile;
    document.getElementById("proposalHeading").textContent = driveFile ? "Kết quả phân loại" : "Đề xuất chuẩn hóa từ AI";
    document.getElementById("proposalTitleLabel").textContent = driveFile ? "Tiêu đề hồ sơ nội bộ" : "Tên tệp chuẩn đề xuất";
    document.getElementById("proposalCategoryLabel").textContent = driveFile ? "Danh mục trong thư viện" : "Thư mục phân loại";
    if (driveFile) {
        document.getElementById("proposedTitleInput").value = doc.title;
        document.getElementById("proposedCategoryInput").value = doc.category || "";
        proposalDocumentId = docId;
        updateProposalControls();
        const kind = documentKinds[doc.document_kind] || "Cần phân loại";
        const source = doc.analysis_source === "gemini" ? "Gemini" : "Quy tắc dự phòng";
        document.getElementById("evidenceQuote").textContent = `${kind} · ${source}. ${doc.analysis_reason || ""}`;
    } else {
        await analyzeCurrentDocument();
    }
}

function updateProposalControls() {
    const doc = allDocuments.find(item => item.id === selectedDocId);
    const mayReview = canReviewDocuments && doc?.status === "pending";
    const approve = document.getElementById("btnApproveProposal");
    approve.hidden = !mayReview;
    approve.disabled = !mayReview || proposalPending || proposalDocumentId !== selectedDocId;
    document.getElementById("btnReanalyzeDrive").hidden = !mayReview || doc?.storage_provider !== "google_drive";
}

async function analyzeCurrentDocument() {
    if (!selectedDocId) return;
    const documentId = selectedDocId;
    analysisRequest?.abort();
    const request = new AbortController();
    analysisRequest = request;
    proposalDocumentId = undefined;
    document.getElementById("proposedTitleInput").value = "";
    document.getElementById("proposedCategoryInput").value = "";
    updateProposalControls();
    const button = document.getElementById("btnAnalyze");
    button.textContent = "Đang phân tích...";
    button.disabled = true;
    try {
        const response = await fetch(`/api/v1/assistant/analyze/${documentId}`, {
            method: "POST",
            headers: getAuthHeaders(),
            signal: request.signal
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Lỗi phân tích AI.");
        if (request.signal.aborted || selectedDocId !== documentId || data.document_id !== documentId) return;
        document.getElementById("proposedTitleInput").value = data.proposed_name || "";
        document.getElementById("proposedCategoryInput").value = data.proposed_category || "";
        proposalDocumentId = documentId;
        updateProposalControls();
        const evidence = data.evidence?.[0]?.quote || "Không có trích đoạn căn cứ";
        document.getElementById("evidenceQuote").textContent = data.history_warning
            ? `${evidence} (${data.history_warning})`
            : evidence;
    } catch (error) {
        if (!request.signal.aborted && selectedDocId === documentId) {
            document.getElementById("evidenceQuote").textContent = error.message;
        }
    } finally {
        if (analysisRequest === request) {
            button.textContent = "Phân tích lại";
            button.disabled = false;
        }
    }
}

async function reanalyzeStoredDocument() {
    const documentId = selectedDocId;
    if (!documentId) return;
    const status = document.getElementById("reanalyzeStatus");
    const button = document.getElementById("btnReanalyzeDrive");
    button.disabled = true;
    status.textContent = "Đang đọc nội dung, tóm tắt và phân loại…";
    try {
        const response = await fetch(`/api/v1/documents/${documentId}/reanalyze`, { method: "POST", headers: getAuthHeaders() });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể đọc lại tệp Drive.");
        await loadDocuments();
        if (selectedDocId === documentId) {
            await selectDocument(documentId);
            status.textContent = data.drive_warning ? `Đã lưu phân tích. ${data.drive_warning}` : "Đã cập nhật nội dung và vị trí lưu trữ trên Drive.";
        }
    } catch (error) {
        if (selectedDocId === documentId) status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

async function approveProposal() {
    const documentId = selectedDocId;
    const status = document.getElementById("proposalActionStatus");
    if (!canReviewDocuments || !documentId || proposalPending || proposalDocumentId !== documentId ||
        document.getElementById("btnApproveProposal").disabled) return;
    const approved_title = document.getElementById("proposedTitleInput").value.trim();
    const category = document.getElementById("proposedCategoryInput").value.trim();
    if (!approved_title || !category) {
        status.textContent = "Vui lòng nhập tên tài liệu và danh mục.";
        return;
    }
    proposalPending = true;
    updateProposalControls();
    status.textContent = "Đang lưu quyết định…";
    try {
        const response = await fetch(`/api/v1/assistant/proposals/${documentId}/approve`, {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ document_id: documentId, approved_title, category })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = typeof data.detail === "string" ? data.detail : "Không thể duyệt đề xuất.";
            throw new Error(`HTTP ${response.status}. ${detail}`);
        }
        await loadDocuments();
        if (selectedDocId === documentId) {
            await selectDocument(documentId);
            if (selectedDocId === documentId) status.textContent = "Đã duyệt và lưu thông tin.";
        }
    } catch (error) {
        if (selectedDocId === documentId) status.textContent = error.message || "Không kết nối được máy chủ.";
    } finally {
        proposalPending = false;
        updateProposalControls();
    }
}

async function summarizeCurrentDocument() {
    if (!selectedDocId) return;
    const button = document.getElementById("btnSummarize");
    const box = document.getElementById("summaryContent");
    button.textContent = "Đang tóm tắt...";
    button.disabled = true;
    try {
        const response = await fetch("/api/v1/assistant/summarize", {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ document_id: selectedDocId })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Lỗi tóm tắt.");
        box.textContent = data.history_warning
            ? `${data.summary}\n\nCảnh báo: ${data.history_warning}`
            : data.summary;
        await loadDocuments();
    } catch (error) {
        box.textContent = `Không thể tóm tắt: ${error.message}`;
    } finally {
        button.textContent = "Tạo tóm tắt AI";
        button.disabled = false;
    }
}

async function askAssistant() {
    const question = document.getElementById("chatQuestion").value.trim();
    if (!question) return;
    const payload = { question };
    if (selectedDocId && document.getElementById("askSelectedDocument").checked) {
        payload.document_ids = [selectedDocId];
    }
    const button = document.getElementById("btnAskAI");
    const responseBox = document.getElementById("chatResponseBox");
    const answerText = document.getElementById("chatAnswerText");
    const sourcesList = document.getElementById("chatSourcesList");
    button.textContent = "Đang tìm nguồn & trả lời...";
    button.disabled = true;
    try {
        const response = await fetch("/api/v1/assistant/ask", {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Lỗi trợ lý AI.");
        responseBox.classList.remove("hidden");
        answerText.textContent = [data.answer, data.scope_warning, data.history_warning].filter(Boolean).join("\n\n");
        sourcesList.innerHTML = data.sources?.length
            ? "<strong>Nguồn trích dẫn:</strong><ul class='list-disc pl-4 mt-1'>" +
                data.sources.map(source =>
                    `<li class="cursor-pointer text-indigo-600 hover:underline" onclick="selectDocument('${source.document_id}')">${escapeHtml(source.title)}</li>`
                ).join("") + "</ul>"
            : "";
    } catch (error) {
        responseBox.classList.remove("hidden");
        answerText.textContent = `Lỗi trợ lý AI: ${error.message}`;
        sourcesList.textContent = "";
    } finally {
        button.textContent = "Hỏi trợ lý AI";
        button.disabled = false;
    }
}

function openUploadModal() {
    document.getElementById("uploadModal").classList.remove("hidden");
    document.getElementById("uploadStatus").textContent = "";
    document.getElementById("uploadResults").replaceChildren();
}

function closeUploadModal() {
    document.getElementById("uploadModal").classList.add("hidden");
}

const uploadDropzone = document.getElementById("uploadDropzone");
uploadDropzone.addEventListener("dragover", event => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    uploadDropzone.classList.add("bg-indigo-50");
});
uploadDropzone.addEventListener("dragleave", () => uploadDropzone.classList.remove("bg-indigo-50"));
uploadDropzone.addEventListener("drop", event => {
    event.preventDefault();
    uploadDropzone.classList.remove("bg-indigo-50");
    if (!event.dataTransfer.files.length || document.getElementById("btnConfirmUpload").disabled) return;
    document.getElementById("fileInput").files = event.dataTransfer.files;
    document.getElementById("uploadStatus").textContent = `Đã chọn ${event.dataTransfer.files.length} tệp.`;
});

async function uploadFiles() {
    const input = document.getElementById("fileInput");
    const status = document.getElementById("uploadStatus");
    if (!input.files?.length) {
        status.textContent = "Vui lòng chọn ít nhất một tệp.";
        return;
    }
    const button = document.getElementById("btnConfirmUpload");
    const results = document.getElementById("uploadResults");
    const files = Array.from(input.files);
    results.replaceChildren();
    button.disabled = true;
    let uploaded = 0;
    const errors = [];
    for (const file of files) {
        status.textContent = `Đang đọc, phân loại và lưu ${file.name}…`;
        const formData = new FormData();
        formData.append("file", file);
        try {
            const response = await fetch("/api/v1/documents/upload", {
                method: "POST",
                headers: getAuthHeaders(),
                body: formData
            });
            if (response.ok) {
                const doc = await response.json();
                const item = document.createElement("li");
                const source = doc.analysis_source === "gemini" ? "Gemini" : "Quy tắc dự phòng";
                item.textContent = `${doc.storage_category} / ${doc.file_name} · ${source}`;
                results.appendChild(item);
                uploaded += 1;
            } else {
                const data = await response.json();
                errors.push(`${file.name}: ${data.detail || "Tải lên thất bại"}`);
            }
        } catch {
            errors.push(`${file.name}: Lỗi kết nối máy chủ.`);
        }
    }
    status.textContent = `Đã lưu ${uploaded}/${files.length} tệp lên Google Drive.` +
        (errors.length ? ` ${errors.join(" | ")}` : "");
    button.disabled = false;
    input.value = "";
    if (uploaded > 0) currentCategoryFilter = "";
    await loadDocuments();
}
