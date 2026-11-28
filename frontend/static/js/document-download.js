async function downloadDocumentFile(documentId) {
    try {
        const response = await fetch(`/api/documents/${documentId}/file`, { headers: getAuthHeaders() });
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Không thể tải tệp.");
        }
        const disposition = response.headers.get("Content-Disposition") || "";
        const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i);
        const plain = disposition.match(/filename="([^"]+)"/i);
        const filename = encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || `document-${documentId}`;
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
        alert(error.message || "Không thể tải tệp.");
    }
}
