// ui/app.js
document.addEventListener('DOMContentLoaded', () => {
    console.log("Impeccable UI Initialized");

    // State
    let taskData = { tasks: [], config: {} };
    let selectedFile = null;
    let statusInterval = null;
    let activeSessionId = null;
    
    // Helper to get elements safely
    const getEl = (id) => {
        const el = document.getElementById(id);
        if (!el) console.warn(`Element with ID "${id}" not found.`);
        return el;
    };

    // DOM Elements
    const taskListEl = getEl('taskList');
    const template = getEl('taskCardTemplate');
    const themeToggle = getEl('themeToggle');
    const toastContainer = getEl('toastContainer');
    
    // Upload & Process Elements
    const uploadZone = getEl('uploadZone');
    const pdfUpload = getEl('pdfUpload');
    const uploadLabel = getEl('uploadLabel');
    const processConfig = getEl('processConfig');
    const btnStartProcess = getEl('btnStartProcess');
    
    // Session Elements
    const sessionSwitcher = getEl('sessionSwitcher');
    const btnNewSession = getEl('btnNewSession');
    const btnCollapseAll = getEl('btnCollapseAll');
    const newExtractionContainer = getEl('newExtractionContainer');
    
    // Settings Elements
    const btnSettings = getEl('btnSettings');
    const settingsModal = getEl('settingsModal');
    const btnCancelSettings = getEl('btnCancelSettings');
    const btnSaveSettings = getEl('btnSaveSettings');
    const providerSelect = getEl('providerSelect');
    const providerApiKey = getEl('providerApiKey');
    const providerBaseUrl = getEl('providerBaseUrl');
    const providerModel = getEl('providerModelSelect');
    const providerModelSelect = getEl('providerModelSelect');
    const btnFetchModels = getEl('btnFetchModels');
    const modelFetchStatus = getEl('modelFetchStatus');
    const apiKeyGroup = getEl('apiKeyGroup');
    const baseUrlGroup = getEl('baseUrlGroup');
    const azureFields = getEl('azureFields');
    const azureDeploymentName = getEl('azureDeploymentName');
    const azureApiVersion = getEl('azureApiVersion');
    
    // Progress UI
    const progressOverlay = getEl('progressOverlay');
    const progressStepTitle = getEl('progressStepTitle');
    const progressMessage = getEl('progressMessage');
    const progressBarFill = getEl('progressBarFill');
    const progressPercentage = getEl('progressPercentage');
    const logConsole = getEl('logConsole');
    const btnCancelProcess = getEl('btnCancelProcess');
    const btnDeleteSession = getEl('btnDeleteSession');
    
    // Filters
    const fPending = getEl('filterPending');
    const fApproved = getEl('filterApproved');
    const fRejected = getEl('filterRejected');
    const fFlagged = getEl('filterFlagged');
    
    const filters = [fPending, fApproved, fRejected, fFlagged].filter(Boolean);
    filters.forEach(el => {
        el.addEventListener('change', renderTasks);
    });

    // Theme logic
    if (localStorage.getItem('theme') === 'dark' || (!localStorage.getItem('theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        document.documentElement.setAttribute('data-theme', 'dark');
    }
    
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('theme', theme);
        });
    }
    
    // --- Settings Modal Logic ---
    let providerRegistry = {};
    let providerSettingsCache = {};
    let modelFetchTimer = null;

    async function loadProviders() {
        const res = await fetch('/api/providers');
        const data = await res.json();
        providerRegistry = data.providers || {};
        if (providerSelect) {
            providerSelect.innerHTML = '';
            Object.keys(providerRegistry).forEach((key) => {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = key;
                providerSelect.appendChild(opt);
            });
        }
    }

    function updateProviderUI() {
        const provider = providerSelect?.value;
        const reg = providerRegistry[provider] || {};
        const cached = providerSettingsCache[provider] || {};
        if (baseUrlGroup) {
            baseUrlGroup.style.display = reg.show_base_url ? 'block' : 'none';
        }
        if (providerBaseUrl) {
            providerBaseUrl.value = cached.base_url || reg.base_url || '';
        }
        if (azureFields) {
            azureFields.style.display = provider === 'azure' ? 'block' : 'none';
        }
        if (apiKeyGroup) {
            apiKeyGroup.style.display = provider === 'ollama' ? 'none' : 'block';
        }
        if (providerApiKey) {
            if (cached.api_key && cached.api_key !== '***') {
                providerApiKey.value = cached.api_key;
            } else {
                providerApiKey.value = '';
                providerApiKey.placeholder = cached.api_key ? 'Stored (re-enter to change)' : 'sk-...';
            }
        }
        if (providerModel) {
            providerModel.value = cached.model || '';
        }
        if (azureDeploymentName) azureDeploymentName.value = cached.azure_deployment_name || '';
        if (azureApiVersion) azureApiVersion.value = cached.azure_api_version || '';
        if (providerModelSelect) {
            providerModelSelect.innerHTML = '';
        }
        if (modelFetchStatus) {
            modelFetchStatus.textContent = '';
            modelFetchStatus.style.color = 'var(--text-tertiary)';
        }
    }

    async function fetchModels() {
        if (!providerSelect) return;
        const provider = providerSelect.value;
        if (!provider) return;
        if (modelFetchStatus) {
            modelFetchStatus.textContent = 'Fetching...';
            modelFetchStatus.style.color = 'var(--text-secondary)';
        }
        try {
            const payload = {
                api_key: providerApiKey?.value,
                base_url: providerBaseUrl?.value,
                azure_deployment_name: azureDeploymentName?.value,
                azure_api_version: azureApiVersion?.value,
            };
            const res = await fetch(`/api/providers/${provider}/models`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || 'Model discovery failed');
            }
            if (providerModelSelect) {
                providerModelSelect.innerHTML = '';
                (data.models || []).forEach((m) => {
                    const opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    providerModelSelect.appendChild(opt);
                });
                
                // Restore from cache if available, otherwise fallback to first model
                const cachedModel = providerSettingsCache[provider]?.model;
                if (cachedModel && data.models.includes(cachedModel)) {
                    providerModelSelect.value = cachedModel;
                } else if (data.models.length > 0) {
                    providerModelSelect.value = data.models[0];
                }
            }
            if (modelFetchStatus) {
                modelFetchStatus.textContent = `✓ ${data.models.length} models`;
                modelFetchStatus.style.color = 'var(--success)';
            }
        } catch (e) {
            if (modelFetchStatus) {
                modelFetchStatus.textContent = `✗ ${e.message || 'Fetch failed'}`;
                modelFetchStatus.style.color = 'var(--error)';
            }
        }
    }

    function scheduleModelFetch() {
        if (modelFetchTimer) clearTimeout(modelFetchTimer);
        modelFetchTimer = setTimeout(fetchModels, 400);
    }

    if (btnSettings && settingsModal) {
        btnSettings.addEventListener('click', async () => {
            try {
                await loadProviders();
                const res = await fetch('/api/settings');
                const data = await res.json();
                providerSettingsCache = data.providers || {};
                if (providerSelect) providerSelect.value = data.provider || 'openai';
                if (getEl('jiraServerUrl')) getEl('jiraServerUrl').value = data.jira_server_url || '';
                if (getEl('jiraApiToken')) getEl('jiraApiToken').value = data.jira_api_token || '';
                updateProviderUI();
                settingsModal.style.display = 'flex';
                scheduleModelFetch();
            } catch (e) {
                showToast('Failed to load settings', 'error');
            }
        });

        btnCancelSettings.addEventListener('click', () => {
            settingsModal.style.display = 'none';
        });

        btnSaveSettings.addEventListener('click', async () => {
            const payload = {
                provider: providerSelect?.value,
                model: providerModel?.value,
                api_key: providerApiKey?.value,
                base_url: providerBaseUrl?.value,
                azure_deployment_name: azureDeploymentName?.value,
                azure_api_version: azureApiVersion?.value,
                jira_server_url: getEl('jiraServerUrl')?.value,
                jira_api_token: getEl('jiraApiToken')?.value,
            };
            btnSaveSettings.textContent = "Saving...";
            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    const provider = providerSelect?.value;
                    if (provider) {
                        providerSettingsCache[provider] = {
                            model: providerModel?.value || '',
                            api_key: providerApiKey?.value ? '***' : (providerSettingsCache[provider]?.api_key || ''),
                            base_url: providerBaseUrl?.value || '',
                            azure_deployment_name: azureDeploymentName?.value || '',
                            azure_api_version: azureApiVersion?.value || '',
                        };
                    }
                    showToast('Settings saved successfully!', 'success');
                    settingsModal.style.display = 'none';
                } else {
                    showToast('Failed to save settings', 'error');
                }
            } catch (e) {
                showToast('Connection error', 'error');
            } finally {
                btnSaveSettings.textContent = "Save Settings";
            }
        });
    }

    if (providerSelect) providerSelect.addEventListener('change', () => {
        updateProviderUI();
        scheduleModelFetch();
    });
    if (providerModelSelect) providerModelSelect.addEventListener('change', () => {
        if (providerModel) providerModel.value = providerModelSelect.value;
    });
    if (providerApiKey) providerApiKey.addEventListener('input', scheduleModelFetch);
    if (providerBaseUrl) providerBaseUrl.addEventListener('input', scheduleModelFetch);
    if (azureDeploymentName) azureDeploymentName.addEventListener('input', scheduleModelFetch);
    if (azureApiVersion) azureApiVersion.addEventListener('input', scheduleModelFetch);
    if (btnFetchModels) btnFetchModels.addEventListener('click', fetchModels);

    // --- Upload Logic ---
    if (uploadZone && pdfUpload) {
        uploadZone.addEventListener('click', (e) => {
            pdfUpload.click();
        });
        
        pdfUpload.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                selectedFile = e.target.files[0];
                if (uploadLabel) uploadLabel.textContent = `📄 ${selectedFile.name}`;
                if (uploadLabel) uploadLabel.style.color = 'var(--accent-primary)';
                if (processConfig) processConfig.style.display = 'flex';
                if (btnStartProcess) btnStartProcess.disabled = false;
            }
        });

        // Drag & Drop
        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.style.borderColor = 'var(--accent-primary)';
        });
        uploadZone.addEventListener('dragleave', () => {
            uploadZone.style.borderColor = '';
        });
        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.style.borderColor = '';
            if (e.dataTransfer.files.length > 0) {
                selectedFile = e.dataTransfer.files[0];
                if (uploadLabel) uploadLabel.textContent = `📄 ${selectedFile.name}`;
                if (processConfig) processConfig.style.display = 'flex';
                if (btnStartProcess) btnStartProcess.disabled = false;
            }
        });
    }

    if (btnStartProcess) {
        btnStartProcess.addEventListener('click', startExtraction);
    }

    if (btnCancelProcess) {
        btnCancelProcess.addEventListener('click', async () => {
            if (!activeSessionId) return;
            try {
                // 1. Signal backend
                fetch(`/api/cancel/${activeSessionId}`, { method: 'POST' });
                
                // 2. Immediate UI reset (Home Redirect)
                if (statusInterval) clearInterval(statusInterval);
                if (progressOverlay) progressOverlay.style.display = 'none';
                
                showToast('Extraction Stopped', 'success');
                
                // 3. Reset state
                activeSessionId = null;
                localStorage.removeItem('activeSessionId');
                resetToNewSession();
                await loadSessions();
                
            } catch (e) {
                showToast('Failed to stop clean', 'error');
            }
        });
    }

    if (btnDeleteSession) {
        btnDeleteSession.addEventListener('click', async () => {
            const idToDelete = sessionSwitcher ? sessionSwitcher.value : activeSessionId;
            if (!idToDelete) {
                showToast('No session selected', 'warning');
                return;
            }

            if (!confirm('Are you sure you want to delete this session?')) return;

            try {
                const res = await fetch(`/api/sessions/${idToDelete}`, { method: 'DELETE' });
                if (res.ok) {
                    showToast('Session deleted', 'success');
                    activeSessionId = null;
                    localStorage.removeItem('activeSessionId');
                    await loadSessions();
                    resetToNewSession();
                }
            } catch (e) {
                showToast('Failed to delete session', 'error');
            }
        });
    }
    
    // --- Session Logic ---
    async function loadSessions() {
        if (!sessionSwitcher) return;
        try {
            const res = await fetch('/api/sessions');
            const sessions = await res.json();
            
            // Clear but keep first option
            while (sessionSwitcher.options.length > 1) { sessionSwitcher.remove(1); }
            
            if (sessions && Array.isArray(sessions)) {
                sessions.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s.run_id;
                    const dateStr = s.created_at || new Date().toISOString();
                    const d = new Date(dateStr).toLocaleString(undefined, {
                        month: 'short', day: 'numeric', hour: '2-digit', minute:'2-digit'
                    });
                    
                    const label = s.filename || s.run_id;
                    opt.textContent = `${label} (${d})`;
                    sessionSwitcher.appendChild(opt);
                });
            }
            
            if (activeSessionId) sessionSwitcher.value = activeSessionId;
        } catch (e) {
            console.error("Failed to load sessions", e);
        }
    }
    
    function resetToNewSession() {
        activeSessionId = null;
        localStorage.removeItem('activeSessionId');
        taskData = { tasks: [], config: {} };
        if (newExtractionContainer) newExtractionContainer.style.display = 'block';
        if (processConfig) processConfig.style.display = 'flex';
        if (btnStartProcess) btnStartProcess.disabled = true;
        if (uploadLabel) {
            uploadLabel.textContent = 'Click or drop SOW PDF here';
            uploadLabel.style.color = '';
        }
        selectedFile = null;
        updateStats();
        renderTasks();
    }
    
    if (sessionSwitcher) {
        sessionSwitcher.addEventListener('change', (e) => {
            activeSessionId = e.target.value;
            if (activeSessionId) {
                if (newExtractionContainer) newExtractionContainer.style.display = 'none';
                if (uploadLabel) uploadLabel.textContent = `📁 Loading session...`;
                loadData();
            } else {
                resetToNewSession();
            }
        });
    }

    if (btnNewSession) {
        btnNewSession.addEventListener('click', () => {
            if (sessionSwitcher) sessionSwitcher.value = '';
            resetToNewSession();
        });
    }

    if (btnCollapseAll) {
        btnCollapseAll.addEventListener('click', () => {
            const allGroups = document.querySelectorAll('.task-group');
            const allCards = document.querySelectorAll('.task-card');
            
            // Toggle groups (Collapse all)
            allGroups.forEach(group => {
                group.classList.add('collapsed');
                const svg = group.querySelector('.group-chevron');
                if (svg) svg.style.transform = 'rotate(-90deg)';
            });

            // Toggle cards (Collapse all)
            allCards.forEach(card => {
                card.classList.remove('expanded');
                const svg = card.querySelector('.chevron');
                if (svg) svg.style.transform = '';
            });

            showToast('All items collapsed', 'success');
        });
    }

    async function startExtraction() {
        if (!selectedFile) return;
        
        try {
            const formData = new FormData();
            formData.append('file', selectedFile);
            
            showToast('Uploading SOW...', 'success');
            const uploadRes = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });
            const uploadData = await uploadRes.json();
            
            const req = {
                pdf_filename: uploadData.filename,
                llm_mode: getEl('llmMode')?.value || 'api',
                jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
                jira_project_key: getEl('projectKey')?.value || 'PROJ',
                skip_indexing: false,
                max_nodes: parseInt(getEl('maxNodes')?.value || '200', 10)
            };
            
            const processRes = await fetch('/api/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(req)
            });
            
            if (processRes.ok) {
                const data = await processRes.json();
                activeSessionId = data.run_id;
                localStorage.setItem('activeSessionId', activeSessionId);
                
                if (progressOverlay) progressOverlay.style.display = 'flex';
                if (logConsole) logConsole.innerHTML = '';
                
                await loadSessions();
                sessionSwitcher.value = activeSessionId;
                if (newExtractionContainer) newExtractionContainer.style.display = 'none';
                
                startStatusPolling();
            } else {
                const err = await processRes.json();
                showToast(err.detail || 'Failed to start process', 'error');
            }
        } catch (e) {
            showToast('Connection error', 'error');
        }
    }

    function getSessionQuery() {
        return activeSessionId ? `?session_id=${activeSessionId}` : '';
    }

    function startStatusPolling() {
        if (statusInterval) clearInterval(statusInterval);
        statusInterval = setInterval(async () => {
            try {
                const res = await fetch('/api/status' + getSessionQuery());
                const status = await res.json();
                
                if (status.is_running || status.progress >= 1.0) {
                    const runIdLabel = status.run_id ? ` (Run: ${status.run_id})` : '';
                    if (progressStepTitle) {
                        if (status.kind === 'jira_push') {
                            progressStepTitle.textContent = `Jira Push${runIdLabel}`;
                        } else {
                            progressStepTitle.textContent = `Step ${status.current_step}/6${runIdLabel}`;
                        }
                    }
                    if (progressMessage) progressMessage.textContent = status.message;
                    const pct = (status.progress * 100).toFixed(0);
                    if (progressBarFill) progressBarFill.style.width = `${pct}%`;
                    if (progressPercentage) progressPercentage.textContent = `${pct}%`;
                    
                    // Update logs in console
                    if (logConsole && status.logs) {
                        const existingCount = logConsole.children.length;
                        if (status.logs.length > existingCount) {
                            for (let i = existingCount; i < status.logs.length; i++) {
                                const p = document.createElement('p');
                                p.style.margin = '0';
                                p.style.color = 'var(--text-secondary)';
                                p.textContent = status.logs[i];
                                logConsole.appendChild(p);
                            }
                            logConsole.scrollTop = logConsole.scrollHeight;
                        }
                    }
                }

                if (!status.is_running) {
                    localStorage.removeItem('activeSessionId');
                    if (status.error) {
                        clearInterval(statusInterval);
                        if (progressMessage) {
                            progressMessage.textContent = `Error: ${status.error}`;
                            progressMessage.style.color = 'var(--error)';
                        }
                        setTimeout(() => { if (progressOverlay) progressOverlay.style.display = 'none'; }, 5000);
                    } else if (status.progress >= 1.0) {
                        clearInterval(statusInterval);
                        if (progressStepTitle) progressStepTitle.textContent = "Complete!";
                        if (status.kind === 'jira_push') {
                            showToast(status.message || 'Jira push complete', 'success');
                        } else {
                            showToast('Extraction Complete!', 'success');
                        }
                        await loadData();
                        setTimeout(() => { if (progressOverlay) progressOverlay.style.display = 'none'; }, 2000);
                    } else {
                        // System is idle (not running, no progress yet)
                        clearInterval(statusInterval);
                    }
                }
            } catch (e) {
                console.error("Polling error", e);
            }
        }, 1000);
    }

    // Fetch Initial Data
    async function loadData() {
        try {
            const res = await fetch('/api/tasks' + getSessionQuery());
            taskData = await res.json();
            
            // Populate config if available
            if (taskData.config || taskData.env_defaults) {
                const cfg = taskData.config || {};
                const env = taskData.env_defaults || {};
                
                if (getEl('llmMode')) getEl('llmMode').value = cfg.llm_mode || 'api';
                if (getEl('jiraHierarchy')) getEl('jiraHierarchy').value = cfg.jira_hierarchy || 'epic_task';
                if (getEl('maxNodes')) getEl('maxNodes').value = cfg.max_nodes || 200;
                
                // Prioritize session config only if it's not the same as old buggy defaults, 
                // but always prefer environment if it was explicitly changed.
                // Actually, let's just prefer the environment if it exists, as that's what the user expects.
                if (getEl('projectKey')) {
                    getEl('projectKey').value = env.jira_project_key || cfg.jira_project_key || 'PROJ';
                }
                
                // Show the config panel if we have data
                if (processConfig) processConfig.style.display = 'flex';
                // Hide the upload label if we're just loading existing data
                if (uploadLabel && !selectedFile) {
                    uploadLabel.textContent = `📁 Loaded from ${taskData.run_id || 'previous run'}`;
                    uploadLabel.style.color = 'var(--text-secondary)';
                }
            }
            
            updateStats();
            renderTasks();
        } catch (e) {
            showToast('Failed to load data', 'error');
        }
    }

    // Save Task Edits
    async function saveTask(updatedTask) {
        try {
            const res = await fetch('/api/tasks' + getSessionQuery(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedTask)
            });
            if (res.ok) {
                showToast('Task updated successfully', 'success');
                await loadData();
            } else {
                throw new Error();
            }
        } catch (e) {
            showToast('Failed to update task', 'error');
        }
    }

    const btnApproveAll = getEl('btnApproveAll');
    if (btnApproveAll) {
        btnApproveAll.addEventListener('click', async () => {
            try {
                const res = await fetch('/api/tasks/approve_all' + getSessionQuery(), { method: 'POST' });
                const data = await res.json();
                showToast(data.message, 'success');
                await loadData();
            } catch (e) {
                showToast('Failed to approve all', 'error');
            }
        });
    }

    const btnPushJira = getEl('btnPushJira');
    if (btnPushJira) {
        btnPushJira.addEventListener('click', async () => {
            btnPushJira.disabled = true;
            const originalText = btnPushJira.textContent;
            btnPushJira.textContent = '⏳ Pushing...';
            
            const req = {
                jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
                jira_project_key: getEl('projectKey')?.value
            };
            
            try {
                const res = await fetch('/api/push' + getSessionQuery(), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(req)
                });
                const data = await res.json();
                if (data.started && data.run_id) {
                    activeSessionId = data.run_id;
                    if (progressOverlay) progressOverlay.style.display = 'flex';
                    if (logConsole) logConsole.innerHTML = '';
                    showToast(data.message || 'Jira push started', 'success');
                    startStatusPolling();
                } else if (data.success) {
                    showToast('Pushed to Jira Successfully!', 'success');
                    await loadData();
                } else {
                    showToast(data.message || 'Push failed', 'error');
                }
            } catch (e) {
                showToast('Failed to contact server', 'error');
            } finally {
                btnPushJira.disabled = false;
                btnPushJira.textContent = originalText;
            }
        });
    }

    function updateStats() {
        const tasks = taskData.tasks || [];
        const total = tasks.length;
        const approved = tasks.filter(t => t.status === 'APPROVED').length;
        const rejected = tasks.filter(t => t.status === 'REJECTED').length;
        const pushed = tasks.filter(t => t.status === 'PUSHED').length;
        const pending = total - approved - rejected - pushed;

        if (getEl('statsTotal')) getEl('statsTotal').textContent = total;
        if (getEl('statsApproved')) getEl('statsApproved').textContent = approved;
        if (getEl('statsRejected')) getEl('statsRejected').textContent = rejected;
        if (getEl('statsPending')) getEl('statsPending').textContent = pending;
        if (getEl('statsPushed')) getEl('statsPushed').textContent = pushed;
    }

    function shouldShow(t) {
        if (t.status === 'CLOSED' && fPending && !fPending.checked) return false;
        if (t.status === 'APPROVED' && fApproved && !fApproved.checked) return false;
        if (t.status === 'REJECTED' && fRejected && !fRejected.checked) return false;
        if (t.status === 'PUSHED' && fApproved && fPending && !fApproved.checked && !fPending.checked) return false;
        if (fFlagged && fFlagged.checked && (!t.flags || t.flags.length === 0)) return false;
        return true;
    }

    function renderTasks() {
        if (!taskListEl) return;
        taskListEl.innerHTML = '';
        const visibleTasks = (taskData.tasks || []).filter(shouldShow);
        if (getEl('showingCount')) getEl('showingCount').textContent = `Showing ${visibleTasks.length} of ${taskData.tasks.length} tasks`;

        // Group tasks by SOW section
        const groups = {};
        visibleTasks.forEach(t => {
            const section = (t.source_refs && t.source_refs.length > 0)
                ? t.source_refs[0].section_title
                : 'General';
            if (!groups[section]) groups[section] = [];
            groups[section].push(t);
        });

        // Get current hierarchy for display labels
        const hierarchy = getEl('jiraHierarchy')?.value || 'epic_task';
        const containerLabel = hierarchy === 'story_subtask' ? 'Story' : hierarchy === 'epic_task' ? 'Epic' : 'Section';
        const childLabel = hierarchy === 'story_subtask' ? 'Sub-task' : 'Task';

        Object.entries(groups).forEach(([section, tasks]) => {
            // Create section group header
            const groupEl = document.createElement('div');
            groupEl.className = 'task-group';

            const approvedCount = tasks.filter(t => t.status === 'APPROVED' || t.status === 'PUSHED').length;
            const groupHeader = document.createElement('div');
            groupHeader.className = 'task-group-header';
            groupHeader.innerHTML = `
                <div class="task-group-title">
                    <span class="task-group-icon">${hierarchy === 'flat' ? '📋' : '📦'}</span>
                    <span class="task-group-label">${containerLabel}:</span>
                    <span>${section}</span>
                </div>
                <div class="task-group-meta">
                    <span class="task-group-count">${tasks.length} ${childLabel}${tasks.length !== 1 ? 's' : ''}</span>
                    <span class="task-group-approved">${approvedCount}/${tasks.length} approved</span>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="group-chevron">
                        <polyline points="6 9 12 15 18 9"></polyline>
                    </svg>
                </div>
            `;
            
            const groupBody = document.createElement('div');
            groupBody.className = 'task-group-body';
            
            groupHeader.addEventListener('click', () => {
                groupEl.classList.toggle('collapsed');
                const svg = groupHeader.querySelector('.group-chevron');
                if (groupEl.classList.contains('collapsed')) {
                    svg.style.transform = 'rotate(-90deg)';
                } else {
                    svg.style.transform = '';
                }
            });
            
            groupEl.appendChild(groupHeader);
            groupEl.appendChild(groupBody);

            tasks.forEach(t => {
                if (!template) return;
                const clone = template.content.cloneNode(true);
                const card = clone.querySelector('.task-card');
                
                card.querySelector('.task-title-text').textContent = t.title;
                const ind = card.querySelector('.status-indicator');
                ind.classList.add(t.status.toLowerCase());
                
                const badgesEl = card.querySelector('.task-badges');
                (t.flags || []).forEach(f => {
                    const b = document.createElement('span');
                    b.className = 'badge';
                    b.textContent = typeof f === 'object' ? f.value : f;
                    badgesEl.appendChild(b);
                });

                const header = card.querySelector('.task-header');
                header.addEventListener('click', (e) => {
                    if(e.target.tagName !== 'BUTTON' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
                        card.classList.toggle('expanded');
                        const svg = card.querySelector('.chevron');
                        if (card.classList.contains('expanded')) {
                            svg.style.transform = 'rotate(180deg)';
                        } else {
                            svg.style.transform = '';
                        }
                    }
                });

                if (t.source_refs && t.source_refs.length > 0) {
                    const ref = t.source_refs[0];
                    const conf = (t.confidence * 100).toFixed(0);
                    card.querySelector('.source-ref').textContent = `📄 Pages ${ref.page_start}-${ref.page_end} | Confidence: ${conf}%`;
                }

                card.querySelector('.task-edit-title').value = t.title || '';
                card.querySelector('.task-edit-desc').value = t.short_description || '';
                card.querySelector('.task-edit-usecase').value = t.use_case || '';
                card.querySelector('.task-edit-ac').value = (t.acceptance_criteria || []).join('\n');
                card.querySelector('.task-edit-cc').value = (t.considerations_constraints || []).join('\n');
                card.querySelector('.task-edit-del').value = (t.deliverables || []).join('\n');

                const stText = card.querySelector('.task-status-text');
                if(t.status === 'APPROVED') { stText.textContent = `✅ Approved`; stText.style.color = 'var(--success)'; }
                else if(t.status === 'REJECTED') { stText.textContent = `❌ Rejected`; stText.style.color = 'var(--error)'; }
                else if(t.status === 'PUSHED') { stText.textContent = `🚀 Pushed`; stText.style.color = 'var(--accent-primary)'; }

                const getUpdatedData = () => {
                    return {
                        id: t.id,
                        title: card.querySelector('.task-edit-title').value,
                        short_description: card.querySelector('.task-edit-desc').value,
                        use_case: card.querySelector('.task-edit-usecase').value,
                        acceptance_criteria: card.querySelector('.task-edit-ac').value.split('\n').filter(x => x.trim() !== ''),
                        considerations_constraints: card.querySelector('.task-edit-cc').value.split('\n').filter(x => x.trim() !== ''),
                        deliverables: card.querySelector('.task-edit-del').value.split('\n').filter(x => x.trim() !== ''),
                        status: t.status
                    };
                };

                card.querySelector('.btn-save').addEventListener('click', (e) => {
                    e.stopPropagation();
                    saveTask(getUpdatedData());
                });

                const btnApprove = card.querySelector('.btn-approve');
                const btnReject = card.querySelector('.btn-reject');
                
                if (t.status === 'APPROVED' || t.status === 'PUSHED') {
                    btnApprove.style.display = 'none';
                } else {
                    btnApprove.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const dt = getUpdatedData();
                        dt.status = 'APPROVED';
                        saveTask(dt);
                    });
                }

                if (t.status === 'REJECTED' || t.status === 'PUSHED') {
                    btnReject.style.display = 'none';
                } else {
                    btnReject.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const dt = getUpdatedData();
                        dt.status = 'REJECTED';
                        saveTask(dt);
                    });
                }

                groupBody.appendChild(card);
            });

            taskListEl.appendChild(groupEl);
        });
    }

    function showToast(message, type = 'success') {
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `<span>${message}</span>`;
        toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // Visibility listener to resume polling when tab becomes focused
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && activeSessionId) {
            console.log("Tab focused, resuming status sync...");
            startStatusPolling();
        }
    });

    // Auto-resume logic for refreshes or tabbing back into a suspended tab
    async function autoResumeSession() {
        const storedId = localStorage.getItem('activeSessionId');
        if (storedId) {
            console.log("Auto-resuming session:", storedId);
            activeSessionId = storedId;
            if (progressOverlay) progressOverlay.style.display = 'flex';
            if (newExtractionContainer) newExtractionContainer.style.display = 'none';
            startStatusPolling();
        }
    }

    // Initialize
    loadSessions().then(() => {
        autoResumeSession();
        if (!localStorage.getItem('activeSessionId')) {
            loadData();
        }
    });
});
