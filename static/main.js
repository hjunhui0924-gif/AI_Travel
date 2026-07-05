const STORAGE_THREAD_KEY = "ai_agent_current_thread";
const STORAGE_RECENT_COLLAPSED_KEY = "ai_agent_recent_collapsed";
const STORAGE_SEARCH_TOGGLE_KEY = "ai_agent_search_enabled";

let sessionMetas = {};
let currentThreadId = localStorage.getItem(STORAGE_THREAD_KEY) || null;
let recentCollapsed = localStorage.getItem(STORAGE_RECENT_COLLAPSED_KEY) === "1";
let searchEnabled = localStorage.getItem(STORAGE_SEARCH_TOGGLE_KEY) === "1";
let selectedFiles = [];
let activeDrawerSources = [];
let lastRequestPayload = null;
let currentUser = null;
let authMode = "login";
let guestMode = false;
let accountMenuOpen = false;

const toggleSidebarBtn = document.getElementById("toggle-sidebar-btn");
const mobileOverlay = document.getElementById("mobile-overlay");
const chatContainer = document.getElementById("chat-container");
const sessionListEl = document.getElementById("session-list");
const messageInput = document.getElementById("message-input");
const fileInput = document.getElementById("file-input");
const fileChipList = document.getElementById("file-chip-list");
const sendButton = document.getElementById("send-button");
const newChatBtn = document.getElementById("new-chat-btn");
const recentToggle = document.getElementById("recent-toggle");
const searchToggle = document.getElementById("search-toggle");
const searchDrawer = document.getElementById("search-drawer");
const searchDrawerOverlay = document.getElementById("search-drawer-overlay");
const searchDrawerBody = document.getElementById("search-drawer-body");
const searchDrawerClose = document.getElementById("search-drawer-close");
const travelHero = document.getElementById("travel-hero");
const scrollBottomBtn = document.getElementById("scroll-bottom-btn");

const accountPanel = document.getElementById("account-panel");
const accountCard = document.getElementById("account-card");
const accountAvatar = document.getElementById("account-avatar");
const accountName = document.getElementById("account-name");
const accountSubtitle = document.getElementById("account-subtitle");
const accountMenu = document.getElementById("account-menu");
const accountMenuStatus = document.getElementById("account-menu-status");
const accountMenuHint = document.getElementById("account-menu-hint");
const loginTriggerBtn = document.getElementById("login-trigger-btn");
const registerTriggerBtn = document.getElementById("register-trigger-btn");
const logoutBtn = document.getElementById("logout-btn");

const authModal = document.getElementById("auth-modal");
const authModalOverlay = document.getElementById("auth-modal-overlay");
const authModalTitle = document.getElementById("auth-modal-title");
const authCloseBtn = document.getElementById("auth-close-btn");
const authForm = document.getElementById("auth-form");
const authModeInput = document.getElementById("auth-mode");
const authUsernameInput = document.getElementById("auth-username");
const authDisplayNameInput = document.getElementById("auth-display-name");
const authPasswordInput = document.getElementById("auth-password");
const displayNameField = document.getElementById("display-name-field");
const authFeedback = document.getElementById("auth-feedback");
const authSubmitBtn = document.getElementById("auth-submit-btn");
const authSwitchCopy = document.getElementById("auth-switch-copy");
const authSwitchBtn = document.getElementById("auth-switch-btn");

marked.setOptions({ breaks: true, gfm: true });

function isMobile() {
    return window.innerWidth <= 860;
}

function escapeHtml(value) {
    return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll("\"", "&quot;")
        .replaceAll("'", "&#39;");
}

function compactMarkdown(text) {
    return String(text || "").replace(/\r\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function updateSidebarState(open) {
    document.body.classList.toggle("sidebar-open", open);
    document.body.classList.toggle("sidebar-collapsed", !open);
}

function saveCurrentThread() {
    localStorage.setItem(STORAGE_THREAD_KEY, currentThreadId || "");
}

function clearCurrentThread() {
    currentThreadId = null;
    localStorage.removeItem(STORAGE_THREAD_KEY);
}

function isGuestThreadId(threadId) {
    return /^guest_[0-9a-f]{16,64}$/i.test(String(threadId || ""));
}

function createGuestThreadId() {
    const array = new Uint8Array(12);
    window.crypto.getRandomValues(array);
    return `guest_${Array.from(array).map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function updateSearchToggleState() {
    searchToggle.classList.toggle("active", searchEnabled);
    localStorage.setItem(STORAGE_SEARCH_TOGGLE_KEY, searchEnabled ? "1" : "0");
}

function setSendingState(sending) {
    const canUseComposer = Boolean(currentUser || guestMode);
    messageInput.disabled = sending || !canUseComposer;
    sendButton.disabled = sending || !canUseComposer;
    searchToggle.disabled = sending || !canUseComposer;
    newChatBtn.disabled = sending;
    fileInput.disabled = sending || !canUseComposer;
    sendButton.classList.toggle("is-loading", sending);
}

function autoResizeTextarea() {
    messageInput.style.height = "auto";
    messageInput.style.height = `${Math.min(messageInput.scrollHeight, 180)}px`;
}

function maybeScrollToBottom(force = false, smooth = false) {
    if (!force) return;
    const previousBehavior = chatContainer.style.scrollBehavior;
    chatContainer.style.scrollBehavior = smooth ? "smooth" : "auto";
    chatContainer.scrollTop = chatContainer.scrollHeight;
    if (previousBehavior) {
        chatContainer.style.scrollBehavior = previousBehavior;
    } else {
        chatContainer.style.removeProperty("scroll-behavior");
    }
}

function updateScrollBottomButton() {
    const remaining = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight;
    scrollBottomBtn.classList.toggle("visible", remaining > 180);
}

function renderImageList(imageUrls) {
    if (!imageUrls || imageUrls.length === 0) return "";
    return `<div class="message-image-strip">${imageUrls.map((url) => `<img class="history-image" src="${escapeHtml(url)}" alt="uploaded image">`).join("")}</div>`;
}

function openSearchDrawer(sources) {
    activeDrawerSources = sources || [];
    searchDrawerBody.innerHTML = activeDrawerSources.map((item, index) => `
        <a class="drawer-result-card" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
            <div class="drawer-result-meta">
                <span class="drawer-result-origin">${escapeHtml(item.title || "搜索结果")}</span>
                ${item.source_date ? `<span class="drawer-result-date">${escapeHtml(item.source_date)}</span>` : ""}
                <span class="drawer-result-index">${index + 1}</span>
            </div>
            <div class="drawer-result-title">${escapeHtml(item.title || item.url)}</div>
            ${item.summary ? `<div class="drawer-result-summary">${escapeHtml(item.summary)}</div>` : ""}
            <div class="drawer-result-url">${escapeHtml(item.url)}</div>
        </a>
    `).join("");
    document.body.classList.add("drawer-open");
    searchDrawer.setAttribute("aria-hidden", "false");
}

function closeSearchDrawer() {
    document.body.classList.remove("drawer-open");
    searchDrawer.setAttribute("aria-hidden", "true");
}

function renderSearchSummary(sources) {
    if (!sources || sources.length === 0) return "";
    return `
        <button class="search-summary-button" type="button" data-open-search-results="1">
            <span class="search-summary-icon">⌕</span>
            <span>搜索到 ${sources.length} 个网页</span>
        </button>
    `;
}

function dedupeSources(sources) {
    const seen = new Set();
    return (sources || []).filter((item) => {
        const key = `${item?.url || ""}__${item?.title || ""}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function shouldDisplayActivity(item) {
    return Boolean(String(item?.title || "").trim());
}

function renderAnswerActions() {
    return `
        <div class="answer-actions">
            <button class="answer-action-btn" type="button" data-action="copy" title="复制">
                <svg stroke="currentColor" fill="none" stroke-width="2" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" height="18" width="18" aria-hidden="true">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
            </button>
            <button class="answer-action-btn" type="button" data-action="regenerate" title="重新生成">
                <svg stroke="currentColor" fill="none" stroke-width="2" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" height="18" width="18" aria-hidden="true">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <polyline points="1 20 1 14 7 14"></polyline>
                    <path d="M3.51 9a9 9 0 0 1 14.13-3.36L23 10"></path>
                    <path d="M20.49 15a9 9 0 0 1-14.13 3.36L1 14"></path>
                </svg>
            </button>
        </div>
    `;
}

function renderUserActions() {
    return `
        <div class="answer-actions user-actions">
            <button class="answer-action-btn" type="button" data-user-action="copy-user" title="复制">
                <svg stroke="currentColor" fill="none" stroke-width="2" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" height="18" width="18" aria-hidden="true">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
            </button>
            <button class="answer-action-btn" type="button" data-user-action="edit-user" title="修改">
                <svg stroke="currentColor" fill="none" stroke-width="2" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" height="18" width="18" aria-hidden="true">
                    <path d="M12 20h9"></path>
                    <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"></path>
                </svg>
            </button>
        </div>
    `;
}

function renderFileSources(attachments) {
    const textFiles = (attachments || []).filter((item) => item.modality === "text");
    if (textFiles.length === 0) return "";
    return `<div class="file-source-note">来源文件：${textFiles.map((item) => escapeHtml(item.name)).join("、")}</div>`;
}

function renderEmptyState(message = "") {
    chatContainer.innerHTML = `
        <div class="empty-state">
            <div class="empty-card">
                <img src="/static/travel-mark.png" alt="AI Travel Agent">
                <div class="eyebrow">AI Agent Workspace</div>
                <h2>${escapeHtml(message || "登录后即可开始专属会话与历史记录隔离")}</h2>
                <p>${escapeHtml(currentUser ? "上传文件或图片后直接提问，系统会仅展示当前账号自己的会话记录。" : "请先登录或注册账号，之后每位用户都只会看到自己的聊天记录。")}</p>
            </div>
        </div>
    `;
}

function moveSessionToTop(threadId) {
    if (!threadId || !sessionMetas[threadId]) return;
    const session = sessionMetas[threadId];
    const reordered = { [threadId]: session };
    Object.keys(sessionMetas).forEach((id) => {
        if (id !== threadId) reordered[id] = sessionMetas[id];
    });
    sessionMetas = reordered;
}

function renderFileChips() {
    fileChipList.innerHTML = "";
    if (selectedFiles.length === 0) {
        fileChipList.classList.remove("show");
        return;
    }
    fileChipList.classList.add("show");
    selectedFiles.forEach((file, index) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "file-chip";
        chip.dataset.fileIndex = String(index);
        chip.innerHTML = `<span>${escapeHtml(file.name)}</span><span class="chip-close">×</span>`;
        fileChipList.appendChild(chip);
    });
}

function syncFileInput() {
    const transfer = new DataTransfer();
    selectedFiles.forEach((file) => transfer.items.add(file));
    fileInput.files = transfer.files;
}

function removeSelectedFile(index) {
    selectedFiles = selectedFiles.filter((_, itemIndex) => itemIndex !== index);
    syncFileInput();
    renderFileChips();
}

function renderAttachmentList(attachments) {
    if (!attachments || attachments.length === 0) return "";
    return `<div class="message-attachments">${attachments.map((item) => `<span class="attachment-pill">${escapeHtml(item.name || item)}</span>`).join("")}</div>`;
}

function renderStepPanel(stepState) {
    if (!stepState) return "";
    const seconds = stepState.startedAt ? Math.max(1, Math.round((Date.now() - stepState.startedAt) / 1000)) : 1;
    const title = stepState.completed ? `已思考（用时 ${seconds} 秒）` : `思考中（已用时 ${seconds} 秒）`;
    return `
        <details class="thinking-panel ${stepState.completed ? "completed" : "running"}" ${stepState.collapsed ? "" : "open"}>
            <summary class="thinking-header">
                <div class="thinking-header-left">
                    <span class="thinking-orbit">◌</span>
                    <span>${title}</span>
                </div>
            </summary>
            <div class="thinking-steps"></div>
        </details>
    `;
}

function buildStepCardHtml(item, index) {
    return `
        <div class="thinking-step-card" style="animation-delay:${index * 45}ms">
            <div class="thinking-step-title">${escapeHtml(item.title)}</div>
            ${item.detail ? `<div class="thinking-step-detail">${escapeHtml(item.detail)}</div>` : ""}
        </div>
    `;
}

function renderSessionList() {
    sessionListEl.innerHTML = "";
    Object.keys(sessionMetas).forEach((id) => {
        const item = document.createElement("div");
        item.className = `session-item ${id === currentThreadId ? "active" : ""}`;
        item.innerHTML = `
            <div class="session-main" data-session-id="${id}">
                <span class="session-chat-icon" aria-hidden="true"></span>
                <span class="session-title">${escapeHtml(sessionMetas[id].title || "新对话")}</span>
            </div>
            <button class="delete-session-btn" type="button" data-delete-id="${id}" title="删除会话">
                <svg stroke="currentColor" fill="none" stroke-width="2" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" height="15" width="15" aria-hidden="true">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
            </button>
        `;
        sessionListEl.appendChild(item);
    });
    sessionListEl.classList.toggle("hidden", recentCollapsed);
    recentToggle.classList.toggle("collapsed", recentCollapsed);
    localStorage.setItem(STORAGE_RECENT_COLLAPSED_KEY, recentCollapsed ? "1" : "0");
}

function appendMessageUI(role, content, attachments = [], imageUrls = [], isSearching = false, autoScroll = true, sources = [], stepState = null, textSourceAttachments = []) {
    const emptyState = chatContainer.querySelector(".empty-state");
    if (emptyState) emptyState.remove();
    if (travelHero) travelHero.classList.add("compressed");

    const isBot = role === "bot" || role === "assistant";
    const wrapper = document.createElement("div");
    wrapper.className = `message-wrapper ${isBot ? "bot" : "user"}`;
    const safeContent = String(content || "");
    const textHtml = isBot
        ? marked.parse(compactMarkdown(safeContent || "正在思考并整理答案..."))
        : `<div class="plain-user-text">${escapeHtml(safeContent || "已上传文件或图片")}</div>`;

    wrapper.innerHTML = `
        <div class="message-content">
            <div class="avatar ${isBot ? "bot-avatar" : "user-avatar"}">${isBot ? "AI" : "U"}</div>
            <div class="message-stack">
                <div class="text">
                    ${isSearching && isBot ? '<div class="search-indicator">联网搜索已开启</div>' : ""}
                    ${renderAttachmentList(attachments)}
                    ${renderImageList(imageUrls)}
                    ${isBot ? renderStepPanel(stepState) : ""}
                    <div class="assistant-text-block">${textHtml}</div>
                    ${isBot && sources.length > 0 ? renderSearchSummary(sources) : ""}
                    ${isBot ? renderFileSources(textSourceAttachments) : ""}
                </div>
                <div class="message-footer-actions">
                    ${isBot ? renderAnswerActions() : renderUserActions()}
                </div>
            </div>
        </div>
    `;

    wrapper.__sources = sources;
    wrapper.__assistantText = safeContent;
    wrapper.__lastRequestPayload = lastRequestPayload;
    wrapper.__stepState = stepState;
    wrapper.__userText = !isBot ? safeContent : "";
    chatContainer.appendChild(wrapper);
    if (autoScroll) maybeScrollToBottom(true);
    updateScrollBottomButton();
    return wrapper;
}

function createAssistantMessageFrame(searchState) {
    const stepState = {
        activities: [],
        startedAt: Date.now(),
        completed: false,
        collapsed: false,
        lastRenderedCount: 0,
    };
    const wrapper = appendMessageUI("bot", buildStreamingPlaceholder(searchState), [], [], searchState, true, [], stepState, []);
    return {
        wrapper,
        textContainer: wrapper.querySelector(".assistant-text-block"),
        stepsContainer: wrapper.querySelector(".thinking-steps"),
        stepState,
        sources: [],
        textSourceAttachments: [],
        fullText: "",
    };
}

function renderAssistantFrame(frame, searchState) {
    const textRoot = frame.wrapper.querySelector(".text");
    const thinkingPanel = frame.wrapper.querySelector(".thinking-panel");
    if (thinkingPanel) {
        thinkingPanel.classList.toggle("completed", frame.stepState.completed);
        const titleNode = thinkingPanel.querySelector(".thinking-header-left span:last-child");
        const seconds = frame.stepState.startedAt ? Math.max(1, Math.round((Date.now() - frame.stepState.startedAt) / 1000)) : 1;
        if (titleNode) {
            titleNode.textContent = frame.stepState.completed ? `已思考（用时 ${seconds} 秒）` : `思考中（已用时 ${seconds} 秒）`;
        }
        if (frame.stepsContainer && frame.stepState.activities.length > frame.stepState.lastRenderedCount) {
            for (let i = frame.stepState.lastRenderedCount; i < frame.stepState.activities.length; i += 1) {
                frame.stepsContainer.insertAdjacentHTML("beforeend", buildStepCardHtml(frame.stepState.activities[i], i));
            }
            frame.stepState.lastRenderedCount = frame.stepState.activities.length;
        }
    }

    frame.textContainer.innerHTML = marked.parse(compactMarkdown(frame.fullText || buildStreamingPlaceholder(searchState)));

    frame.sources = dedupeSources(frame.sources);
    textRoot.querySelectorAll(".search-summary-button").forEach((node) => node.remove());
    if (frame.sources.length > 0) {
        frame.textContainer.insertAdjacentHTML("afterend", renderSearchSummary(frame.sources));
    }

    textRoot.querySelectorAll(".file-source-note").forEach((node) => node.remove());
    if (frame.textSourceAttachments.length > 0) {
        textRoot.insertAdjacentHTML("beforeend", renderFileSources(frame.textSourceAttachments));
    }

    frame.wrapper.__sources = frame.sources;
    frame.wrapper.__assistantText = frame.fullText;
    frame.wrapper.__lastRequestPayload = lastRequestPayload;
    frame.wrapper.__stepState = frame.stepState;
    maybeScrollToBottom();
    updateScrollBottomButton();
}

function setComposerEnabled(enabled) {
    messageInput.disabled = !enabled;
    sendButton.disabled = !enabled;
    newChatBtn.disabled = false;
    searchToggle.disabled = !enabled;
    fileInput.disabled = !enabled;
}

function setAccountMenuOpen(open) {
    accountMenuOpen = Boolean(open);
    accountPanel.classList.toggle("menu-open", accountMenuOpen);
    accountCard.setAttribute("aria-expanded", accountMenuOpen ? "true" : "false");
    accountMenu.setAttribute("aria-hidden", accountMenuOpen ? "false" : "true");
}

function closeAccountMenu() {
    setAccountMenuOpen(false);
}

function setAuthMode(mode) {
    authMode = mode;
    authModeInput.value = mode;
    const isRegister = mode === "register";
    authModalTitle.textContent = isRegister ? "注册账号" : "登录账号";
    authSubmitBtn.textContent = isRegister ? "注册并登录" : "登录";
    authSwitchCopy.textContent = isRegister ? "已经有账号？" : "还没有账号？";
    authSwitchBtn.textContent = isRegister ? "去登录" : "去注册";
    displayNameField.classList.toggle("hidden", !isRegister);
    authDisplayNameInput.disabled = !isRegister;
    authFeedback.classList.add("hidden");
    authFeedback.classList.remove("success");
    authFeedback.textContent = "";
    authPasswordInput.autocomplete = isRegister ? "new-password" : "current-password";
}

function openAuthModal(mode = "login") {
    closeAccountMenu();
    setAuthMode(mode);
    authModal.classList.remove("hidden");
    authModalOverlay.classList.remove("hidden");
    authModal.setAttribute("aria-hidden", "false");
    authUsernameInput.focus();
}

function closeAuthModal(force = false) {
    if (!force && !currentUser) return;
    authModal.classList.add("hidden");
    authModalOverlay.classList.add("hidden");
    authModal.setAttribute("aria-hidden", "true");
}

function enterGuestMode() {
    guestMode = true;
    closeAccountMenu();
    closeAccountMenu();
    currentUser = null;
    closeAccountMenu();
    clearCurrentThread();
    currentThreadId = createGuestThreadId();
    saveCurrentThread();
    sessionMetas = {};
    selectedFiles = [];
    renderFileChips();
    renderSessionList();
    updateAccountPanel();
    renderEmptyState("游客模式已开启，聊天记录仅在本次网页会话中保留");
    closeAuthModal(true);
}

function currentGuestLabel() {
    if (!guestMode || !isGuestThreadId(currentThreadId)) return "";
    const guestId = String(currentThreadId || "");
    const suffix = guestId.includes("_") ? guestId.split("_").pop() : guestId;
    return suffix.slice(-6);
}

function showAuthFeedback(message, success = false) {
    authFeedback.textContent = message;
    authFeedback.classList.remove("hidden");
    authFeedback.classList.toggle("success", success);
}

function syncAccountMenuMeta(statusText, hintText) {
    if (!accountMenu) return;

    let statusNode = document.getElementById("account-menu-status");
    let hintNode = document.getElementById("account-menu-hint");

    if (!statusNode || !hintNode) {
        const header = document.createElement("div");
        header.className = "account-menu-header";

        statusNode = document.createElement("div");
        statusNode.className = "account-menu-status";
        statusNode.id = "account-menu-status";

        hintNode = document.createElement("div");
        hintNode.className = "account-menu-hint";
        hintNode.id = "account-menu-hint";

        header.appendChild(statusNode);
        header.appendChild(hintNode);
        accountMenu.insertBefore(header, accountMenu.firstChild);
    }

    statusNode.textContent = statusText;
    hintNode.textContent = hintText;
}

function updateAccountPanel() {
    if (currentUser) {
        guestMode = false;
        accountCard.classList.remove("is-guest");
        accountAvatar.textContent = currentUser.avatar_label || "U";
        accountName.textContent = currentUser.display_name || currentUser.username || "已登录";
        accountSubtitle.textContent = currentUser.username || "";
        syncAccountMenuMeta(
            currentUser.display_name || currentUser.username || "已登录",
            currentUser.username || "当前账号已启用独立会话"
        );
        loginTriggerBtn.classList.add("hidden");
        registerTriggerBtn.classList.add("hidden");
        logoutBtn.classList.remove("hidden");
        setComposerEnabled(true);
    } else {
        accountCard.classList.add("is-guest");
        accountAvatar.textContent = guestMode ? "T" : "G";
        accountName.textContent = guestMode ? `游客 ${currentGuestLabel()}` : "未登录";
        accountSubtitle.textContent = guestMode ? "关闭网页后自动清除本次会话" : "登录后可隔离聊天记录";
        syncAccountMenuMeta(
            guestMode ? `游客 ${currentGuestLabel()}` : "未登录",
            guestMode ? "关闭网页后自动清除本次会话" : "登录后可隔离聊天记录"
        );
        loginTriggerBtn.classList.remove("hidden");
        registerTriggerBtn.classList.remove("hidden");
        logoutBtn.classList.add("hidden");
        setComposerEnabled(guestMode);
    }
}

async function fetchCurrentUser() {
    const response = await fetch("/auth/me");
    const data = await response.json();
    currentUser = data.authenticated ? data.user : null;
    updateAccountPanel();
    return currentUser;
}

async function createThreadOnServer() {
    const response = await fetch("/threads", { method: "POST" });
    const data = await response.json();
    if (data.status !== "success" || !data.thread?.thread_id) {
        throw new Error(data.message || "创建会话失败。");
    }
    const thread = data.thread;
    sessionMetas[thread.thread_id] = { title: thread.title || "新对话" };
    currentThreadId = thread.thread_id;
    saveCurrentThread();
    renderSessionList();
    renderEmptyState("开始你的专属对话");
    return thread.thread_id;
}

async function createNewSession() {
    if (!currentUser && guestMode) {
        currentThreadId = createGuestThreadId();
        saveCurrentThread();
        renderEmptyState("新的游客会话已创建");
        if (isMobile()) updateSidebarState(false);
        return;
    }
    if (!currentUser) {
        openAuthModal("login");
        return;
    }
    try {
        await createThreadOnServer();
        if (isMobile()) updateSidebarState(false);
    } catch (error) {
        console.error(error);
        appendMessageUI("bot", `创建新会话失败：${error.message}`);
    }
}

async function switchSession(id) {
    if (currentThreadId === id) return;
    currentThreadId = id;
    saveCurrentThread();
    renderSessionList();
    await loadAndRenderHistory(id);
    if (isMobile()) updateSidebarState(false);
}

async function deleteSession(id) {
    if (!confirm("确定要删除这个会话的全部记录吗？")) return;
    try {
        const response = await fetch(`/history/${id}`, { method: "DELETE" });
        const data = await response.json();
        if (data.status !== "success") {
            throw new Error(data.message || "删除失败。");
        }
    } catch (error) {
        console.error(error);
        return;
    }

    delete sessionMetas[id];
    if (currentThreadId === id) {
        clearCurrentThread();
    }

    const ids = Object.keys(sessionMetas);
    if (ids.length === 0) {
        renderSessionList();
        renderEmptyState("暂无会话，点击左侧新建对话开始");
        return;
    }

    currentThreadId = ids[0];
    saveCurrentThread();
    renderSessionList();
    await loadAndRenderHistory(currentThreadId);
}

async function loadAndRenderHistory(id) {
    chatContainer.innerHTML = "";
    appendMessageUI("bot", "正在加载历史对话中...");
    try {
        const response = await fetch(`/history/${id}`);
        const data = await response.json();
        chatContainer.innerHTML = "";

        if (data.status !== "success") {
            throw new Error(data.message || "加载失败");
        }

        if (Array.isArray(data.messages) && data.messages.length > 0) {
            data.messages.forEach((msg) => {
                if (msg.role === "assistant" && Array.isArray(msg.activities) && msg.activities.length > 0) {
                    const stepState = {
                        activities: msg.activities.filter(shouldDisplayActivity),
                        startedAt: Date.now(),
                        completed: true,
                        collapsed: false,
                        lastRenderedCount: 0,
                    };
                    const wrapper = appendMessageUI("bot", msg.content, [], [], false, false, msg.sources || [], stepState, msg.attachments || []);
                    const frame = {
                        wrapper,
                        textContainer: wrapper.querySelector(".assistant-text-block"),
                        stepsContainer: wrapper.querySelector(".thinking-steps"),
                        stepState,
                        sources: msg.sources || [],
                        textSourceAttachments: msg.attachments || [],
                        fullText: msg.content || "",
                    };
                    renderAssistantFrame(frame, false);
                } else {
                    appendMessageUI(
                        msg.role === "user" ? "user" : "bot",
                        msg.content || "",
                        msg.attachments || [],
                        msg.image_urls || [],
                        Boolean(msg.search_enabled)
                    );
                }
            });
            maybeScrollToBottom(true);
        } else {
            renderEmptyState("这个会话还没有消息");
        }
    } catch (error) {
        chatContainer.innerHTML = "";
        appendMessageUI("bot", `加载历史对话失败：${error.message}`);
        console.error(error);
    }
}

async function initializeSessions() {
    if (!currentUser) {
        sessionMetas = {};
        if (!guestMode) {
            clearCurrentThread();
        } else if (!currentThreadId || !isGuestThreadId(currentThreadId)) {
            currentThreadId = createGuestThreadId();
            saveCurrentThread();
        }
        renderSessionList();
        renderEmptyState();
        if (!guestMode) {
            openAuthModal("login");
        }
        return;
    }

    try {
        const response = await fetch("/sessions");
        const data = await response.json();
        sessionMetas = {};

        if (data.status === "success" && Array.isArray(data.sessions)) {
            data.sessions.forEach((session) => {
                if (!session?.thread_id) return;
                sessionMetas[session.thread_id] = { title: session.title || "新对话" };
            });
        }
    } catch (error) {
        console.error("加载会话列表失败:", error);
        sessionMetas = {};
    }

    const ids = Object.keys(sessionMetas);
    if (ids.length === 0) {
        await createThreadOnServer();
        return;
    }

    if (!currentThreadId || !sessionMetas[currentThreadId]) currentThreadId = ids[0];
    saveCurrentThread();
    renderSessionList();
    await loadAndRenderHistory(currentThreadId);
}

function buildStreamingPlaceholder(isSearching) {
    return isSearching
        ? "正在联网搜索并整理结果...\n\n我会先校验结果时效性，再给出结论。"
        : "正在思考并整理答案...\n\n我会先处理上下文，再给出回复。";
}

async function runAssistantResponse(payloadOverride = null, targetWrapper = null) {
    if (!currentUser) {
        if (!guestMode) {
            openAuthModal("login");
            return;
        }
    }

    if (!currentThreadId) {
        if (guestMode && !currentUser) {
            currentThreadId = createGuestThreadId();
            saveCurrentThread();
        } else {
            await createThreadOnServer();
        }
    }

    const sourcePayload = payloadOverride || {
        text: messageInput.value.trim(),
        attachments: [...selectedFiles],
        searchEnabled,
    };
    const text = sourcePayload.text;
    const attachments = sourcePayload.attachments;
    const searchState = sourcePayload.searchEnabled;
    if (!text && attachments.length === 0) return;

    lastRequestPayload = {
        text,
        attachments: attachments.slice(),
        searchEnabled: searchState,
    };

    const formData = new FormData();
    formData.append("message", text);
    formData.append("thread_id", currentThreadId);
    formData.append("search_enabled", searchState ? "true" : "false");
    attachments.forEach((file) => formData.append("files", file));

    if (!payloadOverride) {
        appendMessageUI("user", text || "请结合我上传的文件或图片回答。", attachments.map((file) => ({ name: file.name })), [], searchState);
        messageInput.value = "";
        selectedFiles = [];
        syncFileInput();
        renderFileChips();
        autoResizeTextarea();
    }

    setSendingState(true);

    try {
        moveSessionToTop(currentThreadId);
        renderSessionList();

        const frame = targetWrapper
            ? {
                wrapper: targetWrapper,
                textContainer: targetWrapper.querySelector(".assistant-text-block"),
                stepsContainer: targetWrapper.querySelector(".thinking-steps"),
                stepState: { activities: [], startedAt: Date.now(), completed: false, collapsed: false, lastRenderedCount: 0 },
                sources: [],
                textSourceAttachments: [],
                fullText: "",
            }
            : createAssistantMessageFrame(searchState);

        const timer = setInterval(() => renderAssistantFrame(frame, searchState), 220);
        renderAssistantFrame(frame, searchState);

        const response = await fetch("/chat", { method: "POST", body: formData });
        if (!response.body) throw new Error("响应流为空");

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split("\n\n");
            buffer = events.pop() || "";

            for (const rawEvent of events) {
                const lines = rawEvent.split("\n");
                let eventType = "message";
                let data = "";
                for (const line of lines) {
                    if (line.startsWith("event:")) eventType = line.slice(6).trim();
                    if (line.startsWith("data:")) data += line.slice(5).trim();
                }
                if (!data) continue;

                const payload = JSON.parse(data);
                if (eventType === "activity") {
                    if (shouldDisplayActivity(payload)) frame.stepState.activities.push(payload);
                } else if (eventType === "source") {
                    frame.sources = dedupeSources([...frame.sources, payload]);
                } else if (eventType === "text") {
                    frame.fullText += payload.delta || "";
                } else if (eventType === "done") {
                    frame.stepState.completed = true;
                    if (Array.isArray(payload.activities)) {
                        frame.stepState.activities = payload.activities.filter(shouldDisplayActivity);
                        frame.stepState.lastRenderedCount = 0;
                        if (frame.stepsContainer) frame.stepsContainer.innerHTML = "";
                    }
                    if (Array.isArray(payload.sources)) {
                        frame.sources = dedupeSources(payload.sources);
                    }
                    frame.textSourceAttachments.splice(0, frame.textSourceAttachments.length, ...(payload.attachments || []));
                    if (sessionMetas[currentThreadId] && text.trim()) {
                        sessionMetas[currentThreadId].title = text.slice(0, 18);
                        renderSessionList();
                    }
                } else if (eventType === "error") {
                    frame.fullText += payload.message || "";
                    frame.stepState.completed = true;
                }
                renderAssistantFrame(frame, searchState);
            }
        }

        frame.stepState.completed = true;
        clearInterval(timer);
        renderAssistantFrame(frame, searchState);
    } catch (error) {
        appendMessageUI("bot", `发送失败：${error.message}`);
        console.error(error);
    } finally {
        setSendingState(false);
        messageInput.focus();
    }
}

async function sendMessage(payloadOverride = null) {
    return runAssistantResponse(payloadOverride, null);
}

async function submitAuthForm(event) {
    event.preventDefault();
    const username = authUsernameInput.value.trim();
    const password = authPasswordInput.value;
    const displayName = authDisplayNameInput.value.trim();

    if (!username || !password) {
        showAuthFeedback("请填写用户名和密码。");
        return;
    }

    authSubmitBtn.disabled = true;
    authSubmitBtn.textContent = authMode === "register" ? "提交中..." : "登录中...";

    try {
        const formData = new FormData();
        formData.append("username", username);
        formData.append("password", password);
        if (authMode === "register") formData.append("display_name", displayName);

        const response = await fetch(authMode === "register" ? "/auth/register" : "/auth/login", {
            method: "POST",
            body: formData,
        });
        const data = await response.json();
        if (data.status !== "success") {
            throw new Error(data.message || "认证失败。");
        }

        currentUser = data.user;
        updateAccountPanel();
        closeAuthModal(true);
        authForm.reset();
        authDisplayNameInput.value = "";
        showAuthFeedback(authMode === "register" ? "注册成功，已为你登录。" : "登录成功。", true);
        await initializeSessions();
    } catch (error) {
        showAuthFeedback(error.message || "认证失败。");
    } finally {
        authSubmitBtn.disabled = false;
        authSubmitBtn.textContent = authMode === "register" ? "注册并登录" : "登录";
    }
}

async function handleLogout() {
    try {
        await fetch("/auth/logout", { method: "POST" });
    } catch (error) {
        console.error(error);
    }

    currentUser = null;
    guestMode = false;
    sessionMetas = {};
    selectedFiles = [];
    clearCurrentThread();
    renderFileChips();
    renderSessionList();
    updateAccountPanel();
    renderEmptyState();
    openAuthModal("login");
}

toggleSidebarBtn.addEventListener("click", () => {
    updateSidebarState(document.body.classList.contains("sidebar-collapsed"));
});

mobileOverlay.addEventListener("click", () => {
    if (isMobile()) updateSidebarState(false);
});

recentToggle.addEventListener("click", () => {
    recentCollapsed = !recentCollapsed;
    renderSessionList();
});

newChatBtn.addEventListener("click", () => {
    createNewSession();
});

searchDrawerClose.addEventListener("click", closeSearchDrawer);
searchDrawerOverlay.addEventListener("click", closeSearchDrawer);

accountCard.addEventListener("click", (event) => {
    event.stopPropagation();
    setAccountMenuOpen(!accountMenuOpen);
});

accountMenu.addEventListener("click", (event) => {
    event.stopPropagation();
});

loginTriggerBtn.addEventListener("click", () => openAuthModal("login"));
registerTriggerBtn.addEventListener("click", () => openAuthModal("register"));
logoutBtn.addEventListener("click", handleLogout);
authCloseBtn.addEventListener("click", () => {
    if (!currentUser) {
        enterGuestMode();
        return;
    }
    closeAuthModal();
});
authModalOverlay.addEventListener("click", () => {
    if (!currentUser) {
        enterGuestMode();
        return;
    }
    closeAuthModal();
});
authSwitchBtn.addEventListener("click", () => setAuthMode(authMode === "login" ? "register" : "login"));
authForm.addEventListener("submit", submitAuthForm);

sessionListEl.addEventListener("click", async (event) => {
    const deleteTarget = event.target.closest("[data-delete-id]");
    if (deleteTarget) {
        await deleteSession(deleteTarget.dataset.deleteId);
        return;
    }
    const switchTarget = event.target.closest("[data-session-id]");
    if (switchTarget) {
        await switchSession(switchTarget.dataset.sessionId);
    }
});

chatContainer.addEventListener("click", async (event) => {
    const searchTrigger = event.target.closest("[data-open-search-results]");
    if (searchTrigger) {
        const wrapper = searchTrigger.closest(".message-wrapper");
        if (wrapper) openSearchDrawer(wrapper.__sources || []);
        return;
    }

    const actionButton = event.target.closest("[data-action]");
    if (actionButton) {
        const wrapper = actionButton.closest(".message-wrapper");
        if (!wrapper) return;
        const action = actionButton.dataset.action;
        if (action === "copy") {
            try {
                await navigator.clipboard.writeText(wrapper.__assistantText || "");
                actionButton.classList.add("copied");
                setTimeout(() => actionButton.classList.remove("copied"), 1200);
            } catch (error) {
                console.error(error);
            }
        }
        if (action === "regenerate" && wrapper.__lastRequestPayload) {
            await runAssistantResponse(wrapper.__lastRequestPayload, wrapper);
        }
        return;
    }

    const userActionButton = event.target.closest("[data-user-action]");
    if (userActionButton) {
        const wrapper = userActionButton.closest(".message-wrapper");
        if (!wrapper) return;
        const action = userActionButton.dataset.userAction;
        if (action === "copy-user") {
            try {
                await navigator.clipboard.writeText(wrapper.__userText || "");
            } catch (error) {
                console.error(error);
            }
        }
        if (action === "edit-user") {
            messageInput.value = wrapper.__userText || "";
            autoResizeTextarea();
            messageInput.focus();
        }
    }
});

chatContainer.addEventListener("toggle", (event) => {
    const panel = event.target.closest(".thinking-panel");
    if (!panel) return;
    const wrapper = panel.closest(".message-wrapper");
    if (!wrapper || !wrapper.__stepState) return;
    wrapper.__stepState.collapsed = !panel.open;
}, true);

chatContainer.addEventListener("scroll", updateScrollBottomButton, { passive: true });
scrollBottomBtn.addEventListener("click", () => {
    maybeScrollToBottom(true, true);
    updateScrollBottomButton();
});

fileInput.addEventListener("change", () => {
    const incoming = [...fileInput.files];
    selectedFiles = [...selectedFiles, ...incoming];
    syncFileInput();
    renderFileChips();
});

fileChipList.addEventListener("click", (event) => {
    const target = event.target.closest("[data-file-index]");
    if (!target) return;
    removeSelectedFile(Number(target.dataset.fileIndex));
});

searchToggle.addEventListener("click", () => {
    searchEnabled = !searchEnabled;
    updateSearchToggleState();
});

messageInput.addEventListener("input", autoResizeTextarea);
messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

sendButton.addEventListener("click", () => sendMessage());

document.addEventListener("click", (event) => {
    if (!accountPanel.contains(event.target)) {
        closeAccountMenu();
    }
});

window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeAccountMenu();
    }
});

window.addEventListener("resize", () => {
    closeAccountMenu();
    updateSidebarState(!isMobile());
});

window.addEventListener("beforeunload", () => {
    if (guestMode && currentThreadId && isGuestThreadId(currentThreadId)) {
        fetch(`/history/${currentThreadId}`, { method: "DELETE", keepalive: true }).catch(() => {});
    }
});

updateSidebarState(!isMobile());
updateSearchToggleState();
renderSessionList();
autoResizeTextarea();
updateAccountPanel();
renderEmptyState();
fetchCurrentUser().then(() => initializeSessions()).catch(() => {
    currentUser = null;
    updateAccountPanel();
    renderEmptyState();
    openAuthModal("login");
});
