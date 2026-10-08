/**
 * AcoNuWiFi — Leitor de Senhas Wi-Fi por OCR (Web Client Real)
 * ============================================================
 * Conecta com o Servidor Python (REST API + SQLite).
 * Sem dados fictícios: busca redes reais do SO, grava no SQLite real,
 * usa Tesseract.js para OCR e dispara conexão via backend.
 */

document.addEventListener('DOMContentLoaded', () => {
    // --- ESTADO DO APP ---
    const state = {
        selectedSSID: '',
        capturedPassword: '',
        currentLat: 0,
        currentLon: 0,
        currentStream: null,
        facingMode: 'environment', // câmera traseira
        savedNetworks: []
    };

    // --- ELEMENTOS DO DOM ---
    const permissionModal = document.getElementById('permission-modal');
    const btnGrantPermissions = document.getElementById('btn-grant-permissions');

    const screens = {
        scan: document.getElementById('screen-scan'),
        camera: document.getElementById('screen-camera'),
        confirm: document.getElementById('screen-confirm'),
        history: document.getElementById('screen-history')
    };

    const wifiListEl = document.getElementById('wifi-list');
    const historyListEl = document.getElementById('history-list');
    const historyCountBadge = document.getElementById('history-badge-count');
    const historyTotalCount = document.getElementById('history-total-count');

    const videoFeed = document.getElementById('video-feed');
    const captureCanvas = document.getElementById('capture-canvas');
    const ocrLoader = document.getElementById('ocr-loader');
    const ocrLoaderText = document.getElementById('ocr-loader-text');
    const ocrProgressFill = document.getElementById('ocr-progress-fill');
    const cameraTargetSSID = document.getElementById('camera-target-ssid');

    const confirmSSIDTitle = document.getElementById('confirm-ssid-title');
    const inputPassword = document.getElementById('input-password');
    const locationText = document.getElementById('location-text');

    // --- POPUP MODAL FORÇAR PERMISSÕES NAVEGADOR ---
    async function requestAllBrowserPermissions() {
        showToast('Solicitando Câmera e GPS ao navegador...', 'mdi-shield-sync');

        // 1. Forçar Permissão da Câmera
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
            // Se funcionou, libera o stream temporário
            stream.getTracks().forEach(t => t.stop());
            console.log('Permissão de Câmera Concedida!');
        } catch (err) {
            console.warn('Câmera negada ou bloqueada no HTTP:', err);
        }

        // 2. Forçar Permissão de Geolocalização (GPS)
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    state.currentLat = pos.coords.latitude;
                    state.currentLon = pos.coords.longitude;
                    console.log('Permissão de GPS Concedida:', state.currentLat, state.currentLon);
                },
                (err) => console.warn('GPS não concedido:', err)
            );
        }

        // Ocultar modal com efeito fade-out
        permissionModal.style.opacity = '0';
        setTimeout(() => {
            permissionModal.style.display = 'none';
        }, 300);

        showToast('Permissões ativadas!', 'mdi-check-circle');
    }

    btnGrantPermissions.addEventListener('click', requestAllBrowserPermissions);

    // --- REGEX PARA DETECÇÃO DE SENHAS WI-FI ---
    const PASSWORD_PATTERNS = [
        /senha\s*(?:do\s*)?wi-?fi\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /password\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /senha\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /pass\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /pwr\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /pwd\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /key\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /chave\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /wi-?fi\s*[:=]?\s*["']?([^\s"'\n]+)/i,
        /c[oó]digo\s*[:=]?\s*["']?([^\s"'\n]+)/i
    ];

    function parseWiFiPassword(text) {
        if (!text) return '';
        const cleanText = text.trim();

        for (const rx of PASSWORD_PATTERNS) {
            const match = cleanText.match(rx);
            if (match && match[1]) {
                const pwd = match[1].replace(/[.,;:!?'"]/g, '');
                if (pwd.length >= 4) return pwd;
            }
        }

        // Fallback: sequência alfanumérica >= 8 caracteres
        const tokens = cleanText.match(/[A-Za-z0-9@#$%!&*]{8,}/g);
        if (tokens && tokens.length > 0) {
            return tokens.reduce((a, b) => a.length >= b.length ? a : b);
        }

        // Fallback final: última linha não vazia
        const lines = cleanText.split('\n').map(l => l.trim()).filter(Boolean);
        return lines.length > 0 ? lines[lines.length - 1] : cleanText;
    }

    // --- NAVEGAÇÃO ENTRE TELAS ---
    function navigateTo(screenName) {
        Object.keys(screens).forEach(key => {
            if (key === screenName) {
                screens[key].classList.add('active');
            } else {
                screens[key].classList.remove('active');
            }
        });

        if (screenName !== 'camera') {
            stopCamera();
        }
    }

    // --- TOAST NOTIFICATION ---
    function showToast(message, iconClass = 'mdi-check-circle') {
        const toast = document.getElementById('toast');
        const toastMsg = document.getElementById('toast-msg');
        const toastIcon = document.getElementById('toast-icon');

        toastMsg.textContent = message;
        toastIcon.className = `mdi ${iconClass}`;
        toast.style.display = 'flex';

        setTimeout(() => {
            toast.style.display = 'none';
        }, 3500);
    }

    // --- ESCANEAMENTO WI-FI VIA REST API BACKEND ---
    async function fetchRealWiFiNetworks() {
        wifiListEl.innerHTML = `
            <div style="text-align:center; padding:30px; color:var(--text-muted);">
                <i class="mdi mdi-loading mdi-spin" style="font-size:32px; display:block; margin-bottom:10px;"></i>
                Escaneando redes Wi-Fi reais pelo servidor...
            </div>
        `;

        try {
            const res = await fetch('/api/wifi-scan');
            const data = await res.json();
            
            if (data.success && data.networks && data.networks.length > 0) {
                renderWiFiList(data.networks);
            } else {
                wifiListEl.innerHTML = `
                    <div style="text-align:center; padding:30px 20px; color:var(--text-secondary); background:rgba(255,255,255,0.03); border-radius:14px; border:1px solid var(--border-glass);">
                        <i class="mdi mdi-wifi-off" style="font-size:40px; margin-bottom:10px; color:var(--primary-cyan); display:block;"></i>
                        Nenhuma rede aberta detectada na placa Wi-Fi do servidor.<br>
                        <small style="color:var(--text-muted);">Toque em "Digitar Manualmente" para inserir o nome da sua rede.</small>
                    </div>
                `;
            }
        } catch (err) {
            console.error('Erro no scan API:', err);
            wifiListEl.innerHTML = `
                <div style="text-align:center; padding:20px; color:var(--accent-red);">
                    Falha ao comunicar com o servidor backend.
                </div>
            `;
        }
    }

    function renderWiFiList(networks) {
        wifiListEl.innerHTML = '';
        networks.forEach(net => {
            const card = document.createElement('div');
            card.className = 'wifi-card';
            card.innerHTML = `
                <div class="wifi-icon"><i class="mdi mdi-wifi"></i></div>
                <div class="wifi-info">
                    <div class="wifi-ssid">${net.ssid}</div>
                    <div class="wifi-meta">${net.security || 'WPA2'} • Sinal ${net.signal || 'Bom'}</div>
                </div>
                <div class="wifi-arrow"><i class="mdi mdi-chevron-right"></i></div>
            `;
            card.addEventListener('click', () => selectNetwork(net.ssid));
            wifiListEl.appendChild(card);
        });
    }

    function selectNetwork(ssid) {
        state.selectedSSID = ssid;
        cameraTargetSSID.textContent = ssid;
        navigateTo('camera');
        startCamera();
    }

    // --- CÂMERA & OCR TESSERACT.JS ---
    async function startCamera() {
        try {
            stopCamera();
            const constraints = {
                video: {
                    facingMode: state.facingMode,
                    width: { ideal: 1280 },
                    height: { ideal: 720 }
                }
            };
            state.currentStream = await navigator.mediaDevices.getUserMedia(constraints);
            videoFeed.srcObject = state.currentStream;
        } catch (err) {
            console.error('Erro ao acessar a câmera:', err);
            showToast('Toque no ícone da câmera para enviar foto se a transmissão direta estiver desativada.', 'mdi-information-outline');
        }
    }

    function stopCamera() {
        if (state.currentStream) {
            state.currentStream.getTracks().forEach(track => track.stop());
            state.currentStream = null;
        }
    }

    async function processOCRFromCanvas(canvasSource) {
        ocrLoader.style.display = 'flex';
        ocrLoaderText.textContent = 'Inicializando OCR...';
        ocrProgressFill.style.width = '10%';

        try {
            const worker = await Tesseract.createWorker('por');
            ocrProgressFill.style.width = '40%';
            ocrLoaderText.textContent = 'Lendo texto da imagem...';

            const ret = await worker.recognize(canvasSource);
            ocrProgressFill.style.width = '90%';
            await worker.terminate();

            const recognizedText = ret.data.text;
            console.log('Texto reconhecido pelo Tesseract:', recognizedText);

            const detectedPwd = parseWiFiPassword(recognizedText);
            ocrProgressFill.style.width = '100%';

            setTimeout(() => {
                ocrLoader.style.display = 'none';
                state.capturedPassword = detectedPwd;
                openConfirmScreen();
            }, 400);

        } catch (err) {
            console.error('Erro no OCR:', err);
            ocrLoader.style.display = 'none';
            showToast('Erro ao ler a imagem. Tente novamente.', 'mdi-alert-circle');
        }
    }

    function capturePhoto() {
        if (!videoFeed.videoWidth) {
            document.getElementById('input-file-fallback').click();
            return;
        }

        captureCanvas.width = videoFeed.videoWidth;
        captureCanvas.height = videoFeed.videoHeight;
        const ctx = captureCanvas.getContext('2d');
        ctx.drawImage(videoFeed, 0, 0, captureCanvas.width, captureCanvas.height);

        processOCRFromCanvas(captureCanvas);
    }

    // Upload de Imagem Fallback
    document.getElementById('input-file-fallback').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const img = new Image();
        img.onload = () => {
            captureCanvas.width = img.width;
            captureCanvas.height = img.height;
            const ctx = captureCanvas.getContext('2d');
            ctx.drawImage(img, 0, 0);
            processOCRFromCanvas(captureCanvas);
        };
        img.src = URL.createObjectURL(file);
    });

    // --- CONFIRMAÇÃO & GPS ---
    function openConfirmScreen() {
        confirmSSIDTitle.textContent = state.selectedSSID;
        inputPassword.value = state.capturedPassword;
        fetchGPSLocation();
        navigateTo('confirm');
    }

    function fetchGPSLocation() {
        locationText.textContent = 'Obtendo GPS...';
        if (!navigator.geolocation) {
            locationText.textContent = '📍 GPS indisponível no dispositivo';
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                state.currentLat = pos.coords.latitude;
                state.currentLon = pos.coords.longitude;
                locationText.textContent = `📍 ${state.currentLat.toFixed(4)}, ${state.currentLon.toFixed(4)}`;
            },
            () => {
                locationText.textContent = '📍 GPS não permitido';
            }
        );
    }

    async function connectToWiFi() {
        const ssid = state.selectedSSID;
        const pwd = inputPassword.value.trim();

        if (!ssid || !pwd) {
            showToast('Preencha a senha antes de conectar', 'mdi-alert-circle');
            return;
        }

        showToast('Enviando solicitação ao servidor...', 'mdi-loading mdi-spin');

        try {
            const res = await fetch('/api/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ssid: ssid,
                    password: pwd,
                    lat: state.currentLat,
                    lon: state.currentLon
                })
            });

            const data = await res.json();
            if (data.success) {
                showToast(`✅ ${data.message}`);
                await loadHistoryFromBackend();
                setTimeout(() => {
                    navigateTo('history');
                }, 1400);
            } else {
                showToast(`❌ ${data.message || 'Falha na conexão'}`, 'mdi-alert-circle');
            }

        } catch (err) {
            console.error('Erro na API connect:', err);
            showToast('Erro ao comunicar com o servidor backend', 'mdi-alert-circle');
        }
    }

    // --- HISTÓRICO REAL DO BANCO SQLITE BACKEND ---
    async function loadHistoryFromBackend() {
        try {
            const res = await fetch('/api/history');
            const data = await res.json();

            if (data.success) {
                state.savedNetworks = data.history || [];
                updateHistoryCount();
                renderHistoryList();
            }
        } catch (err) {
            console.error('Erro ao carregar histórico:', err);
        }
    }

    function updateHistoryCount() {
        const count = state.savedNetworks.length;
        historyCountBadge.textContent = count;
        historyCountBadge.style.display = count > 0 ? 'flex' : 'none';
        historyTotalCount.textContent = `${count} salva${count === 1 ? '' : 's'}`;
    }

    function renderHistoryList() {
        historyListEl.innerHTML = '';
        if (state.savedNetworks.length === 0) {
            historyListEl.innerHTML = `
                <div style="text-align:center; padding:40px 20px; color:var(--text-muted);">
                    <i class="mdi mdi-database-off-outline" style="font-size:48px; margin-bottom:12px; display:block;"></i>
                    Nenhuma rede salva no banco de dados SQLite.<br>Escaneie uma senha para salvar!
                </div>
            `;
            return;
        }

        state.savedNetworks.forEach(item => {
            const card = document.createElement('div');
            card.className = 'history-card';
            card.innerHTML = `
                <div class="history-card-top">
                    <span class="history-ssid">${item.ssid}</span>
                    <button class="icon-btn-small btn-del" title="Excluir"><i class="mdi mdi-delete-outline"></i></button>
                </div>
                <div class="history-pwd">
                    <span>${item.password}</span>
                    <button class="btn-copy" title="Copiar Senha"><i class="mdi mdi-content-copy"></i></button>
                </div>
                <div class="history-meta">
                    <span><i class="mdi mdi-clock-outline"></i> ${item.date}</span>
                    ${item.lat ? `<span><i class="mdi mdi-map-marker-outline"></i> ${item.lat.toFixed(2)}, ${item.lon.toFixed(2)}</span>` : ''}
                </div>
            `;

            // Copiar Senha
            card.querySelector('.btn-copy').addEventListener('click', () => {
                navigator.clipboard.writeText(item.password);
                showToast('Senha copiada para a área de transferência!');
            });

            // Deletar do SQLite
            card.querySelector('.btn-del').addEventListener('click', async () => {
                try {
                    const res = await fetch(`/api/history?id=${item.id}`, { method: 'DELETE' });
                    const d = await res.json();
                    if (d.success) {
                        showToast('Rede removida do banco de dados SQLite', 'mdi-delete');
                        await loadHistoryFromBackend();
                    }
                } catch (e) {
                    console.error('Erro ao deletar:', e);
                }
            });

            historyListEl.appendChild(card);
        });
    }

    // --- EVENT LISTENERS ---
    document.getElementById('btn-shutter').addEventListener('click', capturePhoto);

    document.getElementById('btn-toggle-camera').addEventListener('click', () => {
        state.facingMode = state.facingMode === 'environment' ? 'user' : 'environment';
        startCamera();
    });

    document.getElementById('btn-close-camera').addEventListener('click', () => navigateTo('scan'));
    document.getElementById('btn-back-from-history').addEventListener('click', () => navigateTo('scan'));
    document.getElementById('btn-history').addEventListener('click', () => {
        loadHistoryFromBackend();
        navigateTo('history');
    });

    document.getElementById('btn-manual-entry').addEventListener('click', () => {
        state.selectedSSID = 'Rede Manual';
        state.capturedPassword = '';
        openConfirmScreen();
    });

    document.getElementById('btn-connect').addEventListener('click', connectToWiFi);
    document.getElementById('btn-rescan').addEventListener('click', () => {
        navigateTo('camera');
        startCamera();
    });

    document.getElementById('btn-toggle-show-pwd').addEventListener('click', () => {
        const isPassword = inputPassword.type === 'password';
        inputPassword.type = isPassword ? 'text' : 'password';
    });

    document.getElementById('btn-refresh-wifi').addEventListener('click', () => {
        showToast('Atualizando redes Wi-Fi pelo servidor...', 'mdi-sync');
        fetchRealWiFiNetworks();
    });

    // INIT
    fetchRealWiFiNetworks();
    loadHistoryFromBackend();
});
