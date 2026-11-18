const accountTabs = [
    { button: document.getElementById("loginTab"), panel: document.getElementById("loginPanel") },
    { button: document.getElementById("registerTab"), panel: document.getElementById("registerPanel") }
];

const methodTabs = [
    { button: document.getElementById("passwordTab"), panel: document.getElementById("passwordPanel") },
    { button: document.getElementById("qrTab"), panel: document.getElementById("qrPanel") }
];

function activateTab(tabs, selectedTab) {
    tabs.forEach(({ button, panel }) => {
        const active = button === selectedTab.button;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
        panel.hidden = !active;
    });
}

function getErrorMessage(data, fallback) {
    if (typeof data.detail === "string") {
        return data.detail;
    }
    if (Array.isArray(data.detail)) {
        return data.detail.map(error => error.msg).filter(Boolean).join("; ") || fallback;
    }
    return fallback;
}

accountTabs.forEach(({ button }) => {
    button.addEventListener("click", () => activateTab(accountTabs, accountTabs.find(tab => tab.button === button)));
});

methodTabs.forEach(({ button }) => {
    button.addEventListener("click", () => activateTab(methodTabs, methodTabs.find(tab => tab.button === button)));
});

const positionSelect = document.getElementById("position");
const otherPositionGroup = document.getElementById("otherPositionGroup");
const otherPositionInput = document.getElementById("otherPosition");

positionSelect.addEventListener("change", () => {
    const showOtherPosition = positionSelect.value === "Vị trí khác";
    otherPositionGroup.hidden = !showOtherPosition;
    otherPositionInput.required = showOtherPosition;
    if (!showOtherPosition) {
        otherPositionInput.value = "";
    }
});

const loginForm = document.getElementById("loginForm");
loginForm.addEventListener("submit", async event => {
    event.preventDefault();

    const message = document.getElementById("loginMessage");
    const button = document.getElementById("loginButton");
    button.disabled = true;
    button.textContent = "ĐANG ĐĂNG NHẬP...";
    message.textContent = "";

    try {
        const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: document.getElementById("username").value.trim(),
                password: document.getElementById("password").value
            })
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(getErrorMessage(data, "Đăng nhập thất bại"));
        }

        localStorage.setItem("access_token", data.access_token);
        message.style.color = "green";
        message.textContent = "Đăng nhập thành công!";
        window.location.href = "/dashboard";
    } catch (error) {
        message.style.color = "red";
        message.textContent = error.message || "Không thể kết nối máy chủ.";
    } finally {
        button.disabled = false;
        button.textContent = "ĐĂNG NHẬP";
    }
});

const registerForm = document.getElementById("registerForm");
registerForm.addEventListener("submit", async event => {
    event.preventDefault();

    const message = document.getElementById("registerMessage");
    const button = document.getElementById("registerButton");
    const password = document.getElementById("registerPassword").value;
    const confirmPassword = document.getElementById("confirmPassword").value;

    if (password !== confirmPassword) {
        message.style.color = "red";
        message.textContent = "Mật khẩu xác nhận không khớp.";
        return;
    }

    const position = positionSelect.value === "Vị trí khác"
        ? otherPositionInput.value.trim()
        : positionSelect.value;

    button.disabled = true;
    button.textContent = "ĐANG TẠO TÀI KHOẢN...";
    message.textContent = "";

    try {
        const response = await fetch("/api/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                full_name: document.getElementById("fullName").value.trim(),
                phone: document.getElementById("phone").value.trim(),
                email: document.getElementById("email").value.trim(),
                position,
                company: document.getElementById("company").value.trim(),
                password
            })
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(getErrorMessage(data, "Đăng ký thất bại"));
        }

        message.style.color = "green";
        message.textContent = "Tạo tài khoản thành công. Bạn có thể đăng nhập bằng số điện thoại hoặc email.";
        registerForm.reset();
        otherPositionGroup.hidden = true;
        otherPositionInput.required = false;
        activateTab(accountTabs, accountTabs[0]);
        document.getElementById("username").value = data.phone || "";
    } catch (error) {
        message.style.color = "red";
        message.textContent = error.message || "Không thể kết nối máy chủ.";
    } finally {
        button.disabled = false;
        button.textContent = "TẠO TÀI KHOẢN";
    }
});
