function getAuthHeaders(extra = {}) {
    const token = localStorage.getItem("access_token");
    return {
        ...extra,
        ...(token ? { Authorization: `Bearer ${token}` } : {})
    };
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
}

document.addEventListener("DOMContentLoaded", () => {
    if (localStorage.getItem("access_token")) loadDocuments();
});

// ================= QUẢN LÝ VĂN BẢN & TRÌNH KÝ =================
async function loadDocuments() {
    const status = document.getElementById("statusFilter")?.value || "";
    const url = status ? `/api/documents/?status=${status}` : "/api/documents/";

    try {
        const res = await fetch(url, { headers: getAuthHeaders() });
        if (!res.ok) return;
        const docs = await res.json();
        renderDocuments(docs);
    } catch (err) {
        console.error("Lỗi tải văn bản:", err);
    }
}

function renderDocuments(docs) {
    const tbody = document.getElementById("docTableBody");
    if (!tbody) return;
    document.getElementById("totalDocs").innerText = docs.length;
    document.getElementById("pendingDocs").innerText = docs.filter(d => d.status === "pending").length;
    document.getElementById("approvedDocs").innerText = docs.filter(d => d.status === "approved").length;

    if (docs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="text-center py-4 text-slate-500">Chưa có văn bản nào.</td></tr>`;
        return;
    }

    tbody.innerHTML = docs.map(doc => {
        const badgeMap = {
            pending: '<span class="px-2 py-1 text-xs rounded bg-amber-100 text-amber-800 font-medium">Chờ duyệt</span>',
            approved: '<span class="px-2 py-1 text-xs rounded bg-emerald-100 text-emerald-800 font-medium">Đã duyệt</span>',
            rejected: '<span class="px-2 py-1 text-xs rounded bg-rose-100 text-rose-800 font-medium">Từ chối</span>'
        };

        const aiBox = doc.ai_summary
            ? `<div class="text-xs text-slate-600 bg-slate-50 p-2 rounded mt-1 border-l-2 border-indigo-500"><strong>AI Tóm tắt:</strong> ${escapeHtml(doc.ai_summary)}</div>`
            : "";

        const fileLink = doc.file_path
            ? `<button type="button" onclick="downloadDocumentFile('${doc.id}')" class="px-2 py-1 text-xs border rounded hover:bg-slate-100 mr-1">Xem file</button>`
            : "";

        const actionBtns = doc.status === "pending"
            ? `<button class="px-2 py-1 text-xs bg-emerald-600 text-white rounded hover:bg-emerald-700 mr-1" onclick="reviewDoc('${doc.id}', 'approved')">Duyệt</button>
               <button class="px-2 py-1 text-xs border border-rose-300 text-rose-600 rounded hover:bg-rose-50" onclick="reviewDoc('${doc.id}', 'rejected')">Từ chối</button>`
            : "";

        return `
            <tr>
                <td class="px-4 py-3 font-bold text-indigo-600 text-sm">${escapeHtml(doc.doc_number)}</td>
                <td class="px-4 py-3">
                    <div class="font-semibold text-sm">${escapeHtml(doc.title)}</div>
                    ${aiBox}
                </td>
                <td class="px-4 py-3">${badgeMap[doc.status] || escapeHtml(doc.status)}</td>
                <td class="px-4 py-3">${fileLink}${actionBtns}</td>
            </tr>
        `;
    }).join("");
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
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `document-${documentId}`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
        alert(error.message || "Không thể tải tệp.");
    }
}

document.getElementById("createDocForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("submitBtn");
    btn.disabled = true;
    btn.innerText = "Đang xử lý & tóm tắt AI...";

    const formData = new FormData(e.target);
    try {
        const res = await fetch("/api/documents/", {
            method: "POST",
            body: formData,
            headers: getAuthHeaders()
        });
        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || "Có lỗi xảy ra!");
        } else {
            e.target.reset();
            loadDocuments();
        }
    } catch (err) {
        alert("Lỗi kết nối máy chủ.");
    } finally {
        btn.disabled = false;
        btn.innerText = "Gửi & Tóm tắt AI";
    }
});

async function reviewDoc(docId, action) {
    try {
        const res = await fetch(`/api/documents/${docId}/approve?action=${action}`, {
            method: "PUT",
            headers: getAuthHeaders()
        });
        if (res.ok) loadDocuments();
    } catch (err) {
        alert("Không thể cập nhật trạng thái văn bản.");
    }
}

// ================= 1. LỊCH LÀM VIỆC =================
async function loadSchedules() {
    const res = await fetch("/api/v1/schedules/", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const items = await res.json();
    const box = document.getElementById("scheduleList");
    if (!items.length) {
        box.innerHTML = '<p class="text-slate-400">Chưa có lịch làm việc nào.</p>';
        return;
    }
    box.innerHTML = items.map(s => `
        <div class="p-3 border border-slate-200 rounded-lg flex justify-between items-center">
            <div>
                <div class="font-semibold text-slate-800">${escapeHtml(s.title)} <span class="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">${escapeHtml(s.scope)}</span></div>
                <div class="text-xs text-slate-500 mt-1">🕒 ${new Date(s.start_time).toLocaleString("vi-VN")} → ${new Date(s.end_time).toLocaleString("vi-VN")} | 📍 ${escapeHtml(s.location)}</div>
            </div>
            <button onclick="deleteSchedule('${s.id}')" class="text-xs text-rose-600 hover:underline">Xóa</button>
        </div>
    `).join("");
}

document.getElementById("createScheduleForm")?.addEventListener("submit", async e => {
    e.preventDefault();
    const res = await fetch("/api/v1/schedules/", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
            title: document.getElementById("schedTitle").value,
            start_time: new Date(document.getElementById("schedStart").value).toISOString(),
            end_time: new Date(document.getElementById("schedEnd").value).toISOString(),
            scope: document.getElementById("schedScope").value,
            location: document.getElementById("schedLocation").value
        })
    });
    if (res.ok) { e.target.reset(); loadSchedules(); }
    else { const d = await res.json(); alert(d.detail || "Lỗi tạo lịch"); }
});

async function deleteSchedule(id) {
    const res = await fetch(`/api/v1/schedules/${id}`, { method: "DELETE", headers: getAuthHeaders() });
    if (res.ok) loadSchedules();
}

// ================= 2. CHAT & EMAIL NỘI BỘ =================
function loadComms() {
    loadChatMessages();
    loadEmails();
}

async function loadChatMessages() {
    const res = await fetch("/api/v1/comms/chat/general", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const msgs = await res.json();
    const box = document.getElementById("internalChatBox");
    box.innerHTML = msgs.map(m => `
        <div class="bg-white p-2.5 rounded-lg border border-slate-200">
            <div class="flex justify-between text-xs text-slate-500 mb-1">
                <strong class="text-indigo-700">${escapeHtml(m.sender_name)}</strong>
                <span>${new Date(m.created_at).toLocaleTimeString("vi-VN")}</span>
            </div>
            <p class="text-slate-800">${escapeHtml(m.content)}</p>
        </div>
    `).join("") || '<p class="text-xs text-slate-400">Chưa có tin nhắn nào.</p>';
    box.scrollTop = box.scrollHeight;
}

document.getElementById("sendChatForm")?.addEventListener("submit", async e => {
    e.preventDefault();
    const input = document.getElementById("chatMsgInput");
    const res = await fetch("/api/v1/comms/chat", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ channel: "general", content: input.value })
    });
    if (res.ok) { input.value = ""; loadChatMessages(); }
});

async function loadEmails() {
    const res = await fetch("/api/v1/comms/emails?box=inbox", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const emails = await res.json();
    document.getElementById("inboxList").innerHTML = emails.map(m => `
        <div class="p-2.5 border rounded-lg ${m.is_read ? 'bg-white' : 'bg-indigo-50/50 border-indigo-200'}">
            <div class="flex justify-between font-semibold">
                <span>${escapeHtml(m.subject)}</span>
                <span class="text-slate-400">${escapeHtml(m.sender_name)}</span>
            </div>
            <p class="text-slate-600 mt-1">${escapeHtml(m.body)}</p>
        </div>
    `).join("") || '<p class="text-slate-400">Hộp thư đến trống.</p>';
}

document.getElementById("sendEmailForm")?.addEventListener("submit", async e => {
    e.preventDefault();
    const res = await fetch("/api/v1/comms/emails", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
            recipient_email: document.getElementById("emailTo").value,
            subject: document.getElementById("emailSubject").value,
            body: document.getElementById("emailBody").value
        })
    });
    const data = await res.json();
    if (res.ok) { alert(data.message); e.target.reset(); loadEmails(); }
    else { alert(data.detail || "Gửi thư thất bại"); }
});

// ================= 3. QUẢN LÝ NHÂN VIÊN =================

// loadApprovals: Tải danh sách trình ký (gọi khi mở tab workflow)
async function loadApprovals() {
    // Phần trình ký hiển thị inline cùng tab workflow
    // Dữ liệu đã được tải qua loadDocuments() và hiển thị trên bảng danh sách
    // Nếu cần thêm danh sách approval riêng, mở rộng ở đây
}

async function loadEmployees() {
    const res = await fetch("/api/v1/employees/", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const list = await res.json();
    document.getElementById("employeeTableBody").innerHTML = list.map(u => `
        <tr>
            <td class="p-3 font-medium">${escapeHtml(u.full_name)}</td>
            <td class="p-3 text-slate-600">${escapeHtml(u.email)}</td>
            <td class="p-3">${escapeHtml(u.department)}</td>
            <td class="p-3"><span class="px-2 py-0.5 rounded text-xs bg-slate-100 font-semibold">${escapeHtml(u.role)}</span></td>
            <td class="p-3">${u.is_active ? '<span class="text-emerald-600 font-medium">Hoạt động</span>' : '<span class="text-rose-600">Đã khóa</span>'}</td>
        </tr>
    `).join("");
}

document.getElementById("createEmployeeForm")?.addEventListener("submit", async e => {
    e.preventDefault();
    const res = await fetch("/api/v1/employees/", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
            full_name: document.getElementById("empName").value,
            email: document.getElementById("empEmail").value,
            phone: document.getElementById("empPhone").value || null,
            department: document.getElementById("empDept").value,
            role: document.getElementById("empRole").value
        })
    });
    const data = await res.json();
    if (res.ok) { alert("Đã tạo nhân viên (Mật khẩu mặc định: Welcome@123)"); e.target.reset(); loadEmployees(); }
    else { alert(data.detail || "Không thể thêm nhân viên"); }
});

// ================= 4. BÁO CÁO THỐNG KÊ & BIỂU ĐỒ =================
let deptChartInstance = null;
let taskDocChartInstance = null;
let overviewBarChartInstance = null;

async function loadReports() {
    const res = await fetch("/api/v1/reports/overview", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const k = data.kpis;
    const t = data.task_breakdown || { todo: 0, in_progress: 0, done: 0 };

    // 1. Render 4 thẻ KPI
    document.getElementById("reportKpiCards").innerHTML = `
        <div class="bg-white p-4 rounded-xl border shadow-sm"><div class="text-xs text-slate-500">Tổng nhân sự</div><div class="text-2xl font-bold mt-1">${k.total_employees}</div></div>
        <div class="bg-white p-4 rounded-xl border shadow-sm"><div class="text-xs text-slate-500">Tổng văn bản</div><div class="text-2xl font-bold text-indigo-600 mt-1">${k.total_documents}</div></div>
        <div class="bg-white p-4 rounded-xl border shadow-sm"><div class="text-xs text-slate-500">Tỷ lệ hoàn thành việc</div><div class="text-2xl font-bold text-emerald-600 mt-1">${k.task_completion_rate}%</div></div>
        <div class="bg-white p-4 rounded-xl border shadow-sm"><div class="text-xs text-slate-500">Biên bản cuộc họp</div><div class="text-2xl font-bold text-amber-600 mt-1">${k.total_meetings}</div></div>
    `;

    if (typeof Chart === "undefined") return;

    const isDark = document.body.classList.contains("dark-theme");
    const textColor = isDark ? "#cbd5e1" : "#475569";
    const gridColor = isDark ? "rgba(148, 163, 184, 0.15)" : "rgba(148, 163, 184, 0.25)";

    // 2. Biểu đồ Doughnut: Nhân sự theo phòng ban
    const deptCtx = document.getElementById("deptChart")?.getContext("2d");
    if (deptCtx) {
        if (deptChartInstance) deptChartInstance.destroy();
        deptChartInstance = new Chart(deptCtx, {
            type: "doughnut",
            data: {
                labels: data.employees_by_department.map(d => d.department),
                datasets: [{
                    data: data.employees_by_department.map(d => d.count),
                    backgroundColor: ["#6366f1", "#10b981", "#f59e0b", "#ec4899", "#06b6d4", "#8b5cf6"],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "right", labels: { color: textColor, boxWidth: 12 } }
                }
            }
        });
    }

    // 3. Biểu đồ Cột: Tiến độ công việc & Văn bản
    const taskCtx = document.getElementById("taskDocChart")?.getContext("2d");
    if (taskCtx) {
        if (taskDocChartInstance) taskDocChartInstance.destroy();
        taskDocChartInstance = new Chart(taskCtx, {
            type: "bar",
            data: {
                labels: ["Chờ làm (Todo)", "Đang làm", "Hoàn thành", "VB Chờ duyệt", "VB Đã duyệt"],
                datasets: [{
                    label: "Số lượng",
                    data: [t.todo, t.in_progress, t.done, k.pending_documents, k.approved_documents],
                    backgroundColor: ["#94a3b8", "#3b82f6", "#10b981", "#f59e0b", "#6366f1"],
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: textColor }, grid: { display: false } },
                    y: { beginAtZero: true, ticks: { color: textColor, stepSize: 1 }, grid: { color: gridColor } }
                }
            }
        });
    }

    // 4. Biểu đồ Cột ngang: Tổng hợp toàn hệ thống
    const ovCtx = document.getElementById("overviewBarChart")?.getContext("2d");
    if (ovCtx) {
        if (overviewBarChartInstance) overviewBarChartInstance.destroy();
        overviewBarChartInstance = new Chart(ovCtx, {
            type: "bar",
            data: {
                labels: ["Nhân sự", "Công việc", "Văn bản", "Trình ký chờ duyệt", "Biên bản họp", "Lịch làm việc"],
                datasets: [{
                    label: "Tổng số bản ghi",
                    data: [
                        k.total_employees,
                        k.total_tasks,
                        k.total_documents,
                        k.pending_approvals,
                        k.total_meetings,
                        k.total_schedules
                    ],
                    backgroundColor: "rgba(99, 102, 241, 0.85)",
                    borderRadius: 6
                }]
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { beginAtZero: true, ticks: { color: textColor, stepSize: 1 }, grid: { color: gridColor } },
                    y: { ticks: { color: textColor }, grid: { display: false } }
                }
            }
        });
    }
}
// ================= 5. CÀI ĐẶT, GIAO DIỆN SÁNG/TỐI & NGÔN NGỮ =================
let currentTheme = localStorage.getItem("ui_theme") || "light";
let currentLang = localStorage.getItem("ui_lang") || "vi";

const I18N_DICT = {
    vi: {
        pref_title: "Giao diện & Ngôn ngữ",
        pref_desc: "Tùy chỉnh trải nghiệm hiển thị cá nhân của bạn trên hệ thống.",
        theme_label: "Chế độ hiển thị (Sáng / Tối)",
        theme_light: "Chế độ Sáng",
        theme_dark: "Chế độ Tối",
        lang_label: "Ngôn ngữ hiển thị",
        notif_sound: "Âm thanh thông báo tin nhắn & duyệt đơn",
        sys_title: "Cấu Hình Hệ Thống & Phân Quyền",
        sys_desc: "Thiết lập chung áp dụng cho toàn bộ không gian làm việc.",
        org_label: "Tên tổ chức / Doanh nghiệp",
        ai_auto_label: "Tự động tóm tắt văn bản bằng Gemini AI khi tải lên",
        two_step_label: "Bật quy trình trình ký 2 cấp",
        btn_save_cfg: "Lưu toàn bộ cài đặt"
    },
    en: {
        pref_title: "Appearance & Language",
        pref_desc: "Customize your personal display preferences across the workspace.",
        theme_label: "Display Theme (Light / Dark)",
        theme_light: "Light Mode",
        theme_dark: "Dark Mode",
        lang_label: "Display Language",
        notif_sound: "Notification sound for messages & approvals",
        sys_title: "System Configuration & Permissions",
        sys_desc: "Global settings applied across the entire organization.",
        org_label: "Organization / Company Name",
        ai_auto_label: "Auto-summarize uploaded documents with Gemini AI",
        two_step_label: "Enable 2-step approval workflow",
        btn_save_cfg: "Save All Settings"
    }
};

function previewTheme(theme) {
    currentTheme = theme === "dark" ? "dark" : "light";
    localStorage.setItem("ui_theme", currentTheme);
    document.body.classList.toggle("dark-theme", currentTheme === "dark");

    const btnLight = document.getElementById("btnThemeLight");
    const btnDark = document.getElementById("btnThemeDark");
    if (btnLight && btnDark) {
        if (currentTheme === "dark") {
            btnDark.className = "flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border-2 border-indigo-500 bg-indigo-950 text-indigo-300 text-sm font-semibold transition";
            btnLight.className = "flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border border-slate-600 text-slate-300 text-sm font-semibold transition";
        } else {
            btnLight.className = "flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border-2 border-indigo-600 bg-indigo-50 text-indigo-700 text-sm font-semibold transition";
            btnDark.className = "flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border border-slate-200 text-slate-700 text-sm font-semibold hover:bg-slate-50 transition";
        }
    }
}

function applyLanguage(lang) {
    currentLang = lang === "en" ? "en" : "vi";
    localStorage.setItem("ui_lang", currentLang);
    const dict = I18N_DICT[currentLang];
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        if (dict[key]) el.textContent = dict[key];
    });
}

// Khởi tạo giao diện Sáng/Tối & Ngôn ngữ ngay khi mở trang
document.addEventListener("DOMContentLoaded", () => {
    previewTheme(currentTheme);
    applyLanguage(currentLang);
});

async function loadSettings() {
    previewTheme(currentTheme);
    const langSelect = document.getElementById("cfgLanguage");
    if (langSelect) langSelect.value = currentLang;

    const res = await fetch("/api/v1/settings/", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const cfg = await res.json();
    document.getElementById("cfgOrgName").value = cfg.organization_name || "";
    document.getElementById("cfgAiAuto").checked = Boolean(cfg.ai_auto_summarize);
    document.getElementById("cfgTwoStep").checked = Boolean(cfg.require_two_step_approval);
}

async function saveSettings() {
    const selectedLang = document.getElementById("cfgLanguage")?.value || currentLang;
    applyLanguage(selectedLang);

    const res = await fetch("/api/v1/settings/", {
        method: "PUT",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
            organization_name: document.getElementById("cfgOrgName").value,
            language: selectedLang,
            theme: currentTheme,
            ai_auto_summarize: document.getElementById("cfgAiAuto").checked,
            require_two_step_approval: document.getElementById("cfgTwoStep").checked
        })
    });
    const data = await res.json();
    if (res.ok) {
        alert(currentLang === "en" ? "Settings saved successfully!" : (data.message || "Đã lưu cài đặt!"));
    } else {
        alert(currentLang === "en" ? "Personal theme/language saved locally (Admin role required for system config)." : "Đã lưu giao diện & ngôn ngữ cá nhân (Cấu hình hệ thống yêu cầu quyền Admin).");
    }
}