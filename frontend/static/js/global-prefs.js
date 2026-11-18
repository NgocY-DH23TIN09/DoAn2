(function () {
    const theme = localStorage.getItem("ui_theme") || "light";
    const lang = localStorage.getItem("ui_lang") || "vi";

    // 1. Tự động chèn CSS Dark Mode phủ toàn bộ thẻ (bao gồm cả thẻ có inline background)
    const style = document.createElement("style");
    style.id = "global-dark-style";
    style.textContent = `
        html.dark-theme, body.dark-theme {
            background-color: #0f172a !important;
            color: #e2e8f0 !important;
        }
        body.dark-theme main,
        body.dark-theme section,
        body.dark-theme article,
        body.dark-theme header,
        body.dark-theme nav,
        body.dark-theme aside,
        body.dark-theme .container,
        body.dark-theme .container-fluid,
        body.dark-theme .card,
        body.dark-theme .card-body,
        body.dark-theme .card-header,
        body.dark-theme .bg-white,
        body.dark-theme .bg-light,
        body.dark-theme .bg-slate-50,
        body.dark-theme .bg-gray-50,
        body.dark-theme .bg-gray-100,
        body.dark-theme [style*="background: white"],
        body.dark-theme [style*="background: #fff"],
        body.dark-theme [style*="background-color: white"],
        body.dark-theme [style*="background-color: #fff"],
        body.dark-theme [style*="background-color: rgb(255, 255, 255)"] {
            background-color: #1e293b !important;
            color: #f1f5f9 !important;
            border-color: #334155 !important;
        }
        body.dark-theme main {
            background-color: #0f172a !important;
        }
        body.dark-theme .bg-primary {
            background-color: #1e293b !important;
            border-bottom: 1px solid #334155 !important;
        }
        body.dark-theme h1, body.dark-theme h2, body.dark-theme h3,
        body.dark-theme h4, body.dark-theme h5, body.dark-theme label,
        body.dark-theme th, body.dark-theme strong, body.dark-theme p,
        body.dark-theme span:not(.badge) {
            color: #f1f5f9 !important;
        }
        body.dark-theme input,
        body.dark-theme select,
        body.dark-theme textarea,
        body.dark-theme .form-control,
        body.dark-theme .form-select {
            background-color: #0f172a !important;
            color: #f8fafc !important;
            border-color: #475569 !important;
        }
        body.dark-theme table, body.dark-theme thead, body.dark-theme tbody,
        body.dark-theme tr, body.dark-theme td, body.dark-theme th {
            background-color: #1e293b !important;
            color: #e2e8f0 !important;
            border-color: #334155 !important;
        }
        body.dark-theme thead th {
            background-color: #0f172a !important;
        }
    `;
    document.head.appendChild(style);

    if (theme === "dark") {
        document.documentElement.classList.add("dark-theme");
        document.addEventListener("DOMContentLoaded", () => {
            document.body.classList.add("dark-theme");
        });
    }

    // 2. Từ điển dịch tự động toàn bộ các trang (Việt -> Anh)
    const GLOBAL_TRANSLATIONS = {
        "VĂN PHÒNG SỐ": "DIGITAL OFFICE",
        "Tổng quan": "Dashboard",
        "Tổng quan (Dashboard)": "Dashboard",
        "Tài liệu": "Documents",
        "Tài liệu & Wiki AI": "Documents & AI Wiki",
        "Phòng họp": "Meetings",
        "Phòng họp thông minh": "Smart Meetings",
        "Công việc": "Tasks",
        "Quản lý Công việc": "Task Management",
        "Lịch làm việc": "Work Schedule",
        "Chat & Email nội bộ": "Internal Chat & Email",
        "Trình ký & Phê duyệt": "Approvals & E-Sign",
        "Quản lý nhân viên": "Employee Management",
        "Báo cáo thống kê": "Reports & Analytics",
        "Trợ lý AI": "AI Assistant",
        "Cài đặt & Phân quyền": "Settings & Permissions",
        "Đăng xuất": "Logout",
        "Giao Công Việc Mới": "Assign New Task",
        "Tên công việc *": "Task Title *",
        "Mô tả chi tiết": "Detailed Description",
        "Người thực hiện *": "Assignee *",
        "Mức độ ưu tiên": "Priority Level",
        "Trung bình": "Medium",
        "Cao": "High",
        "Thấp": "Low",
        "Hạn hoàn thành": "Due Date",
        "Giao việc": "Assign Task",
        "Danh Sách Công Việc": "Task List",
        "Tất cả tiến độ": "All Statuses",
        "Người nhận": "Assignee",
        "Ưu tiên": "Priority",
        "Tiến độ": "Progress",
        "Chưa có công việc nào.": "No tasks available.",
        "Chức năng chính": "Main Features",
        "Công việc ưu tiên (AI Đề xuất)": "Priority Tasks (AI Suggested)",
        "Lịch họp hôm nay": "Today's Meetings",
        "Tải lên tài liệu mới": "Upload New Document",
        "Tạo cuộc họp mới": "Create New Meeting"
    };

    function translatePageToEnglish() {
        if (lang !== "en") return;
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
        let node;
        while ((node = walker.nextNode())) {
            const raw = node.nodeValue.trim();
            if (raw && GLOBAL_TRANSLATIONS[raw]) {
                node.nodeValue = node.nodeValue.replace(raw, GLOBAL_TRANSLATIONS[raw]);
            }
        }
        document.querySelectorAll("input[placeholder], textarea[placeholder]").forEach(el => {
            if (el.placeholder.includes("Hỏi AI")) el.placeholder = "Ask AI about documents...";
            if (el.placeholder.includes("Tổng hợp báo cáo")) el.placeholder = "e.g., Weekly summary report...";
        });
    }

    document.addEventListener("DOMContentLoaded", translatePageToEnglish);
    // Theo dõi các bảng dữ liệu tải bất đồng bộ sau khi mở trang
    window.addEventListener("load", () => setTimeout(translatePageToEnglish, 400));
})();