const token = localStorage.getItem("access_token");
document.documentElement.dataset.theme = localStorage.getItem("ui_theme") || "light";

const byId = id => document.getElementById(id);
const googleApi = "/api/v1/google";
const connectButton = byId("connectGoogle");
const disconnectButton = byId("disconnectGoogle");
const configPanel = byId("googleConfig");
const configForm = byId("googleConfigForm");
const configStatus = byId("configurationStatus");
const moreDrive = byId("moreDrive");
let nextDrivePage = null;

async function api(path, options = {}) {
    const response = await fetch(`${googleApi}${path}`, {
        ...options,
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
    });
    if (response.status === 401) {
        localStorage.removeItem("access_token");
        window.location.replace("/login");
    }
    const data = await response.json();
    if (!response.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : "Không xử lý được yêu cầu.");
    }
    return data;
}

function setNotice(message) {
    const notice = byId("notice");
    notice.hidden = !message;
    notice.textContent = message;
}

function showCallbackResult() {
    const result = new URLSearchParams(window.location.search).get("google");
    if (!result) return;
    const messages = new Map([
        ["connected", "Đã liên kết tài khoản Google."],
        ["cancelled", "Bạn đã dừng cấp quyền Google."],
        ["expired", "Phiên cấp quyền đã hết hạn. Hãy chọn Kết nối Google để bắt đầu lại."],
        ["permissions", "Hãy kết nối lại và cấp đủ quyền Google Lịch, Google Drive và email tài khoản."],
        ["error", "Không thể hoàn tất kết nối. Hãy kiểm tra cấu hình OAuth và thử lại."]
    ]);
    setNotice(messages.get(result));
    window.history.replaceState(null, "", "/google-workspace");
}

async function loadStatus() {
    const statusText = byId("connectionStatus");
    try {
        const status = await api("/status");
        connectButton.hidden = !status.configured;
        connectButton.textContent = status.connected ? "Kết nối lại" : "Kết nối Google";
        disconnectButton.hidden = !status.connected;
        if (status.can_configure && configPanel.hidden && !status.configured) {
            configPanel.open = true;
        }
        configPanel.hidden = !status.can_configure;
        byId("oauthRedirectUri").value = status.setup_redirect_uri || "";
        byId("googleServices").hidden = !status.connected;
        if (status.connected) {
            statusText.textContent = `Đang dùng ${status.email}`;
            await Promise.all([loadCalendar(), loadDrive()]);
        } else {
            statusText.textContent = status.message || "Kết nối tài khoản để dùng Google Lịch và Drive.";
            if (!status.configured && !status.can_configure) {
                statusText.textContent += " Liên hệ quản trị viên để nhập cấu hình Google.";
            }
            byId("calendarEvents").replaceChildren();
            byId("driveFiles").replaceChildren();
        }
    } catch (error) {
        statusText.textContent = error.message;
    }
}

function updateConfigMethod() {
    const useJson = configForm.elements.config_method.value === "json";
    const jsonFields = byId("oauthJsonFields");
    const manualFields = byId("oauthManualFields");
    jsonFields.hidden = !useJson;
    jsonFields.disabled = !useJson;
    manualFields.hidden = useJson;
    manualFields.disabled = useJson;
}

configForm.addEventListener("change", event => {
    if (event.target.name === "config_method") updateConfigMethod();
});

byId("copyRedirectUri").addEventListener("click", async () => {
    const field = byId("oauthRedirectUri");
    if (!field.value) return;
    try {
        await navigator.clipboard.writeText(field.value);
        configStatus.textContent = "Đã sao chép Redirect URI.";
    } catch {
        field.focus();
        field.select();
        configStatus.textContent = "Nhấn Ctrl+C để sao chép Redirect URI.";
    }
});

configForm.addEventListener("submit", async event => {
    event.preventDefault();
    const submitButton = byId("saveGoogleConfig");
    submitButton.disabled = true;
    configStatus.textContent = "Đang lưu cấu hình…";
    try {
        await api("/config", { method: "PUT", body: new FormData(configForm) });
        configForm.reset();
        updateConfigMethod();
        configStatus.textContent = "Đã lưu cấu hình Google.";
        configPanel.open = false;
        setNotice("Đã lưu cấu hình. Chọn Kết nối Google để cấp quyền.");
        await loadStatus();
        if (!connectButton.hidden) connectButton.focus();
    } catch (error) {
        configStatus.textContent = error.message;
    } finally {
        submitButton.disabled = false;
    }
});

function makeEntry(title, detail, rawUrl) {
    const item = document.createElement("li");
    let url;
    try { url = new URL(rawUrl); } catch { /* The provider may omit a view link. */ }
    const hosts = ["calendar.google.com", "www.google.com", "drive.google.com", "docs.google.com"];
    const clickable = url?.protocol === "https:" && hosts.includes(url.hostname);
    const label = document.createElement(clickable ? "a" : "strong");
    label.textContent = title;
    if (clickable) {
        label.href = url.href;
        label.target = "_blank";
        label.rel = "noopener noreferrer";
    }
    const detailElement = document.createElement("small");
    detailElement.textContent = detail;
    item.append(label, detailElement);
    return item;
}

function eventDate(start) {
    if (start?.dateTime) {
        return new Date(start.dateTime).toLocaleString("vi-VN", {
            timeZone: "Asia/Ho_Chi_Minh", dateStyle: "medium", timeStyle: "short"
        });
    }
    return start?.date ? `${start.date.split("-").reverse().join("/")} · Cả ngày` : "";
}

async function loadCalendar() {
    const button = byId("refreshCalendar");
    const list = byId("calendarEvents");
    const status = byId("calendarStatus");
    button.disabled = true;
    status.textContent = "Đang tải lịch…";
    list.replaceChildren();
    try {
        const { items } = await api("/calendar/events");
        list.append(...items.map(item => makeEntry(item.summary || "Sự kiện", eventDate(item.start), item.htmlLink)));
        status.textContent = items.length ? "" : "Chưa có sự kiện sắp tới.";
    } catch (error) {
        status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

async function loadDrive(pageToken = null) {
    const button = byId("refreshDrive");
    const list = byId("driveFiles");
    const status = byId("driveStatus");
    button.disabled = true;
    moreDrive.disabled = true;
    status.textContent = "Đang tải tệp…";
    if (!pageToken) {
        nextDrivePage = null;
        list.replaceChildren();
    }
    try {
        const query = pageToken ? `?page_token=${encodeURIComponent(pageToken)}` : "";
        const data = await api(`/drive/files${query}`);
        list.append(...data.items.map(file => makeEntry(file.name, file.modifiedTime ? eventDate({ dateTime: file.modifiedTime }) : "", file.webViewLink)));
        nextDrivePage = data.next_page_token;
        status.textContent = list.children.length || nextDrivePage ? "" : "Chưa có tệp được cấp quyền.";
    } catch (error) {
        status.textContent = error.message;
    } finally {
        button.disabled = false;
        moreDrive.disabled = false;
        moreDrive.hidden = !nextDrivePage;
    }
}

connectButton.addEventListener("click", async () => {
    connectButton.disabled = true;
    setNotice("");
    try {
        const data = await api("/connect", { method: "POST" });
        window.location.assign(data.authorization_url);
    } catch (error) {
        setNotice(error.message);
        connectButton.disabled = false;
    }
});

disconnectButton.addEventListener("click", async () => {
    disconnectButton.disabled = true;
    try {
        await api("/connection", { method: "DELETE" });
        setNotice("Đã ngắt liên kết Google trong NexusOffice.");
        await loadStatus();
    } catch (error) {
        setNotice(error.message);
    } finally {
        disconnectButton.disabled = false;
    }
});

byId("refreshCalendar").addEventListener("click", loadCalendar);
byId("refreshDrive").addEventListener("click", () => loadDrive());
moreDrive.addEventListener("click", () => loadDrive(nextDrivePage));

byId("uploadDrive").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = byId("uploadDriveButton");
    const status = byId("uploadStatus");
    const file = byId("driveUpload").files[0];
    if (!file || file.size === 0 || file.size > 10 * 1024 * 1024) {
        status.textContent = "Chọn một tệp có nội dung, tối đa 10 MB.";
        return;
    }
    button.disabled = true;
    status.textContent = "Đang tải lên Google Drive…";
    try {
        const data = await api("/drive/files", { method: "POST", body: new FormData(form) });
        form.reset();
        status.textContent = `Đã tải ${data.name || file.name} lên Google Drive.`;
        await loadDrive();
    } catch (error) {
        status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

if (token) {
    showCallbackResult();
    loadStatus();
} else {
    window.location.replace("/login");
}
