/**
 * Universal AI Video Production Studio - Client Interaction Script
 */

document.addEventListener("DOMContentLoaded", () => {
  // Initialize language preference
  const savedLang = localStorage.getItem("studio_lang") || "ar";
  setLanguage(savedLang);

  // Initialize theme
  const savedTheme = localStorage.getItem("studio_theme") || "dark";
  document.documentElement.setAttribute("data-theme", savedTheme);
});

function setLanguage(lang) {
  document.documentElement.setAttribute("lang", lang);
  document.documentElement.setAttribute("dir", lang === "ar" ? "rtl" : "ltr");
  localStorage.setItem("studio_lang", lang);

  const langBtn = document.getElementById("lang-toggle-btn");
  if (langBtn) {
    langBtn.textContent = lang === "ar" ? "English" : "العربية";
  }
}

function toggleLanguage() {
  const currentLang = document.documentElement.getAttribute("lang") || "ar";
  const nextLang = currentLang === "ar" ? "en" : "ar";
  setLanguage(nextLang);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("studio_theme", next);
}

/**
 * Copy text to clipboard with feedback
 */
function copyToClipboard(text, btnElement) {
  if (!navigator.clipboard) {
    // Fallback for older browsers
    const textArea = document.createElement("textarea");
    textArea.value = text;
    document.body.appendChild(textArea);
    textArea.select();
    try {
      document.execCommand("copy");
      showToast("تم النسخ بنجاح! / Copied to clipboard!", "success");
    } catch (err) {
      showToast("فشل النسخ / Copy failed", "error");
    }
    document.body.removeChild(textArea);
    return;
  }

  navigator.clipboard.writeText(text).then(() => {
    if (btnElement) {
      const origText = btnElement.innerHTML;
      btnElement.classList.add("copied");
      btnElement.innerHTML = "✓ تم النسخ";
      setTimeout(() => {
        btnElement.classList.remove("copied");
        btnElement.innerHTML = origText;
      }, 2000);
    }
    showToast("تم نسخ النص إلى الحافظة جاهزاً للصق في Google Flow!", "success");
  }).catch((err) => {
    console.error("Clipboard write error:", err);
    showToast("تعذر نسخ النص", "error");
  });
}

/**
 * Toast Notification System
 */
function showToast(message, type = "info") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const icon = type === "success" ? "✓" : (type === "error" ? "⚠" : "ℹ");
  toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 300);
  }, 3500);
}
