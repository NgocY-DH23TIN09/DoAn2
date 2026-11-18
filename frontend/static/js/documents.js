let allDocuments = [];
let selectedDocId = null;
let currentCategoryFilter = "";

function getAuthHeaders(extra = {}) {
    const token = localStorage.getItem("access_token");
    return { ...extra, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
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
    const searchInput = document.getElementById("searchInput");
    searchInput?.addEventListener("input", renderDocuments);
});

async function loadDocuments() {
    try {
        const response = await fetch("/api/v1/documents", { headers: getAuthHeaders() });
        if (response.status === 401) {
            localStorage.removeItem("access_token");
            window.location.href = "/login";
            return;
        }
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể tải danh sách tài liệu.");
        allDocuments = Array.isArray(data) ? data : [];
        renderDocuments();
    } catch (error) {
        document.getElementById("documentList").textContent = `Lỗi tải dữ liệu: ${error.message}`;
    }
}

function filterByCategory(category) {
    currentCategoryFilter = category;
    renderDocuments();
}

function renderDocuments() {
    const keyword = (document.getElementById("searchInput")?.value || "").toLowerCase().trim();
    const statusFilter = document.getElementById("statusFilter")?.value || "";
    const filtered = allDocuments.filter(doc => {
        const matchesKeyword = !keyword ||
            (doc.title || "").toLowerCase().includes(keyword) ||
            (doc.ai_summary || "").toLowerCase().includes(keyword);
        const matchesCategory = !currentCategoryFilter ||
            (doc.category || "").includes(currentCategoryFilter);
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
                    <span class="text-sm font-semibold text-slate-800 truncate">${escapeHtml(doc.title || "Tài liệu không tên")}</span>
                    <span class="text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0 ${badgeClass}">${badgeText}</span>
                </div>
                <p class="text-xs text-slate-500 truncate">${escapeHtml(doc.ai_summary || "Chưa có tóm tắt AI")}</p>
                <div class="text-[11px] text-slate-400 mt-1.5 flex justify-between">
                    <span>Danh mục: ${escapeHtml(doc.category || "00_TiepNhan")}</span>
                    <span class="flex items-center gap-2">
                        ${doc.file_path ? `<button type="button" onclick="event.stopPropagation(); downloadDocumentFile('${doc.id}')" class="text-indigo-600 hover:underline">Tải tệp</button>` : ""}
                        ID: #${escapeHtml(doc.id)}
                    </span>
                </div>
            </div>`;
    }).join("");
}

async function selectDocument(docId) {
    selectedDocId = docId;
    renderDocuments();
    const doc = allDocuments.find(item => item.id === docId);
    if (!doc) return;

    document.getElementById("emptyDetailState").classList.add("hidden");
    document.getElementById("detailPanel").classList.remove("hidden");
    document.getElementById("detailTitle").textContent = doc.title;
    document.getElementById("detailMeta").textContent =
        `Danh mục hiện tại: ${doc.category || "00_TiepNhan"}`;
    document.getElementById("summaryContent").textContent =
        doc.ai_summary || "Nhấn 'Tạo tóm tắt AI' để tóm tắt nội dung.";
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
    await analyzeCurrentDocument();
}

async function analyzeCurrentDocument() {
    if (!selectedDocId) return;
    const button = document.getElementById("btnAnalyze");
    button.textContent = "Đang phân tích...";
    button.disabled = true;
    try {
        const response = await fetch(`/api/v1/assistant/analyze/${selectedDocId}`, {
            method: "POST",
            headers: getAuthHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Lỗi phân tích AI.");
        document.getElementById("proposedTitleInput").value = data.proposed_name || "";
        document.getElementById("proposedCategoryInput").value = data.proposed_category || "";
        const evidence = data.evidence?.[0]?.quote || "Không có trích đoạn căn cứ";
        document.getElementById("evidenceQuote").textContent = data.history_warning
            ? `${evidence} (${data.history_warning})`
            : evidence;
    } catch (error) {
        document.getElementById("evidenceQuote").textContent = error.message;
    } finally {
        button.textContent = "Phân tích lại";
        button.disabled = false;
    }
}

async function approveProposal() {
    if (!selectedDocId) return;
    const approved_title = document.getElementById("proposedTitleInput").value.trim();
    const category = document.getElementById("proposedCategoryInput").value.trim();
    if (!approved_title || !category) {
        alert("Vui lòng nhập tên tài liệu và danh mục.");
        return;
    }
    try {
        const response = await fetch(`/api/v1/assistant/proposals/${selectedDocId}/approve`, {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ approved_title, category })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể duyệt đề xuất.");
        await loadDocuments();
        await selectDocument(selectedDocId);
    } catch (error) {
        alert(`Lỗi: ${error.message}`);
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
            body: JSON.stringify({ question })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Lỗi trợ lý AI.");
        responseBox.classList.remove("hidden");
        answerText.textContent = data.history_warning
            ? `${data.answer || ""}\n\nCảnh báo: ${data.history_warning}`
            : data.answer || "";
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
}

function closeUploadModal() {
    document.getElementById("uploadModal").classList.add("hidden");
}

async function downloadDocumentFile(documentId) {
    try {
        const response = await fetch(`/api/documents/${documentId}/file`, {
            headers: getAuthHeaders()
        });
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Không thể tải tệp.");
        }
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = `document-${documentId}`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
        alert(error.message || "Không thể tải tệp.");
    }
}

async function uploadFiles() {
    const input = document.getElementById("fileInput");
    const status = document.getElementById("uploadStatus");
    if (!input.files?.length) {
        status.textContent = "Vui lòng chọn ít nhất một tệp.";
        return;
    }
    const button = document.getElementById("btnConfirmUpload");
    button.disabled = true;
    let uploaded = 0;
    const errors = [];
    for (const file of input.files) {
        const formData = new FormData();
        formData.append("file", file);
        try {
            const response = await fetch("/api/v1/documents/upload", {
                method: "POST",
                headers: getAuthHeaders(),
                body: formData
            });
            if (response.ok) {
                uploaded += 1;
            } else {
                const data = await response.json();
                errors.push(`${file.name}: ${data.detail || "Tải lên thất bại"}`);
            }
        } catch {
            errors.push(`${file.name}: Lỗi kết nối máy chủ.`);
        }
    }
    status.textContent = `Đã tải lên ${uploaded}/${input.files.length} tệp.` +
        (errors.length ? ` ${errors.join(" | ")}` : "");
    button.disabled = false;
    input.value = "";
    await loadDocuments();
}
