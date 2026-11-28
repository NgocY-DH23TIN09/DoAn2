function getAuthHeaders(extra = {}) {
    const token = localStorage.getItem("access_token");
    return { ...extra, ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
}

async function loadCurrentUser() {
    if (!localStorage.getItem("access_token")) {
        window.location.href = "/login";
        return null;
    }
    try {
        const response = await fetch("/api/auth/me", { headers: getAuthHeaders() });
        if (!response.ok) throw new Error("Unauthorized");
        const user = await response.json();
        const navbar = document.querySelector(".navbar-brand");
        if (navbar) navbar.title = `Đăng nhập với ${user.full_name || user.username}`;
        return user;
    } catch {
        localStorage.removeItem("access_token");
        window.location.href = "/login";
        return null;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const user = await loadCurrentUser();
    if (!user) return;
    await loadAssignees(user);
    await loadTasks();
});

async function loadAssignees(currentUser) {
    try {
        const response = await fetch("/api/tasks/assignees", { headers: getAuthHeaders() });
        const users = await response.json();
        if (!response.ok) throw new Error(users.detail || "Không thể tải danh sách nhân viên.");
        const availableUsers = currentUser.role === "employee"
            ? users.filter(user => user.id === currentUser.id)
            : users;
        const select = document.getElementById("assigneeSelect");
        select.innerHTML = availableUsers.map(user =>
            `<option value="${escapeHtml(user.id)}">${escapeHtml(user.full_name)} (${escapeHtml(user.department)})</option>`
        ).join("");
    } catch (error) {
        document.getElementById("taskTableBody").innerHTML =
            `<tr><td colspan="4" class="text-danger p-3">${escapeHtml(error.message)}</td></tr>`;
    }
}

let taskListRequest;

async function loadTasks() {
    taskListRequest?.abort();
    const request = new AbortController();
    taskListRequest = request;
    const status = document.getElementById("taskStatusFilter").value;
    const url = status ? `/api/tasks/?status=${encodeURIComponent(status)}` : "/api/tasks/";
    try {
        const response = await fetch(url, { headers: getAuthHeaders(), signal: request.signal });
        const tasks = await response.json();
        if (request.signal.aborted) return;
        if (!response.ok) throw new Error(tasks.detail || "Không thể tải danh sách công việc.");

        const tbody = document.getElementById("taskTableBody");
        if (tasks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">Chưa có công việc nào.</td></tr>';
            return;
        }

        const priorityBadge = {
            low: '<span class="badge bg-secondary">Thấp</span>',
            medium: '<span class="badge bg-info text-dark">Trung bình</span>',
            high: '<span class="badge bg-danger">Khẩn cấp</span>'
        };
        tbody.innerHTML = tasks.map(task => {
            const deadline = task.deadline
                ? `<div class="small text-muted">Hạn: ${escapeHtml(new Date(task.deadline).toLocaleString("vi-VN"))}</div>`
                : "";
            return `
                <tr>
                    <td>
                        <div class="fw-semibold">${escapeHtml(task.title)}</div>
                        <div class="small text-muted">${escapeHtml(task.description || "")}</div>
                        ${deadline}
                    </td>
                    <td>${escapeHtml(task.assignee_name)}</td>
                    <td>${priorityBadge[task.priority] || escapeHtml(task.priority)}</td>
                    <td>
                        <select class="form-select form-select-sm" onchange="updateStatus('${task.id}', this.value)">
                            <option value="todo" ${task.status === "todo" ? "selected" : ""}>Cần làm</option>
                            <option value="in_progress" ${task.status === "in_progress" ? "selected" : ""}>Đang làm</option>
                            <option value="done" ${task.status === "done" ? "selected" : ""}>Hoàn thành</option>
                        </select>
                    </td>
                </tr>`;
        }).join("");
    } catch (error) {
        if (request.signal.aborted) return;
        document.getElementById("taskTableBody").innerHTML =
            `<tr><td colspan="4" class="text-danger p-3">${escapeHtml(error.message)}</td></tr>`;
    }
}

document.getElementById("createTaskForm").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    if (submit.disabled) return;
    const deadlineValue = document.getElementById("taskDeadline").value;
    const assigneeId = document.getElementById("assigneeSelect").value;
    if (!assigneeId) {
        alert("Vui lòng chọn người thực hiện.");
        return;
    }

    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
        const response = await fetch("/api/tasks/", {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                title: document.getElementById("taskTitle").value.trim(),
                description: document.getElementById("taskDesc").value.trim(),
                assignee_id: assigneeId,
                priority: document.getElementById("taskPriority").value,
                deadline: deadlineValue ? new Date(deadlineValue).toISOString() : null
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể giao công việc.");
        form.reset();
        await loadTasks();
    } catch (error) {
        alert(error.message || "Lỗi kết nối máy chủ.");
    } finally {
        submit.disabled = false;
        form.removeAttribute("aria-busy");
    }
});

async function updateStatus(taskId, newStatus) {
    try {
        const response = await fetch(`/api/tasks/${taskId}/status`, {
            method: "PATCH",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ status: newStatus })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không thể cập nhật trạng thái.");
        await loadTasks();
    } catch (error) {
        alert(error.message || "Lỗi kết nối máy chủ.");
        await loadTasks();
    }
}

function logout() {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
}
