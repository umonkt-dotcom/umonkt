let socket = null;
let mouseEnabled = false;
let keyboardEnabled = false;
let clipboardSync = false;
let lastClipboard = "";
let webcamActive = false;
let displayMode = 'all';
let activeView = 'dashboard';
let serverMetadata = null;
let monitors = [];
let isRecording = false;
let selectedDeviceId = null;

// Audio State
let audioContext = null;
let audioVolume = 1.0;

// Memory Management (Blob URLs)
const monitorUrls = {};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Dynamically set the Add User command based on current URL
    const baseUrl = `${window.location.protocol}//${window.location.host}`;
    document.getElementById('powershell-command').innerText = `powershell -c "irm ${baseUrl}/api/script | iex"`;
    
    connect();
    setupNavigation();
    setupControls();
    setupQuickActions();
    setupModals();
    setupResizing();
    createToastContainer();
    makeDraggable(document.getElementById('webcam-overlay'), document.querySelector('.webcam-header'));
    setInterval(fetchDevices, 10000);
});

// WebRTC state
let pc = null;
let dataChannel = null;

function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => {
        console.log('MRL- Link Established');
        if (window._keepaliveTimer) clearInterval(window._keepaliveTimer);
        window._keepaliveTimer = setInterval(() => {
            if (socket && socket.readyState === WebSocket.OPEN)
                socket.send(JSON.stringify({ t: 'ping' }));
        }, 30000);
    };
    socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
            const msg = JSON.parse(event.data);
            handleTextMessage(msg);
        } else {
            handleBinaryMessage(event.data);
        }
    };
    socket.onclose = () => setTimeout(connect, 2000);
}

function sendEvent(data) {
    // Prefer DataChannel (P2P) for control events, fallback to WebSocket relay
    if (dataChannel && dataChannel.readyState === 'open') {
        dataChannel.send(JSON.stringify(data));
    } else if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(data));
    }
}

async function selectDevice(deviceId) {
    console.log("Selecting device:", deviceId);
    document.getElementById('nav-remote').classList.remove('hidden');
    document.getElementById('nav-tools').classList.remove('hidden');
    document.getElementById('active-host-name').innerText = `CONNECTING: ${deviceId}...`;
    sendEvent({ t: 'select_device', id: deviceId });
    navigateTo('remote');
    startWebRTC(deviceId);
}

async function startWebRTC(deviceId) {
    console.log("Initializing WebRTC P2P...");
    // Clear old session state
    knownMonitors.clear();
    document.getElementById('screens-grid').innerHTML = '';
    document.getElementById('display-select').innerHTML = '<option value="all">All Screens</option>';

    if (pc) { pc.close(); pc = null; dataChannel = null; }

    pc = new RTCPeerConnection({
        iceServers: [
            { urls: 'stun:stun.l.google.com:19302' }
        ],
        iceTransportPolicy: 'all',
        bundlePolicy: 'max-bundle',
        rtcpMuxPolicy: 'require'
    });

    // DataChannel for mouse/keyboard + Telemetry (P2P, instant)
    dataChannel = pc.createDataChannel("mrl-control", { ordered: true });
    dataChannel.onopen = () => console.log("P2P DataChannel Linked.");
    dataChannel.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.t === "stats") {
                updateLiveStats(msg.data);
            } else if (msg.t === "process_list") {
                renderProcessesFast(msg.data);
            }
        } catch(err) {}
    };
    dataChannel.onclose = () => console.log('MRL- DataChannel closed');

    // Video track from agent screen
    pc.ontrack = (event) => {
        console.log("WebRTC Track received:", event.track.kind);
        if (event.track.kind === 'video') {
            const video = document.getElementById('remote-video');
            video.srcObject = event.streams[0];
            video.classList.remove('hidden');
            // Fast AF Tuning
            video.autoplay = true;
            video.playsInline = true;
            video.muted = true;
            video.setAttribute('controls', 'false');
            video.style.pointerEvents = 'none';
            // Ensure no buffer delay
            video.play().catch(e => console.error("Play failed:", e));
            // Hide JPEG grid if WebRTC video starts
            document.getElementById('screens-grid').classList.add('hidden');
            setupInputListenersRTC(video); // Setup listeners on the new video element
            console.log('MRL- WebRTC video stream active (P2P)');
        } else if (event.track.kind === 'audio') {
            const audio = new Audio();
            audio.srcObject = event.streams[0];
            audio.play().catch(e => console.error("Audio play failed:", e));
        }
    };

    // Send ICE candidates to agent via Railway signaling
    pc.onicecandidate = (event) => {
        if (event.candidate && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ t: 'rtc_ice', candidate: event.candidate }));
        }
    };

    pc.onconnectionstatechange = () => {
        console.log('MRL- WebRTC state:', pc.connectionState);
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    socket.send(JSON.stringify({ t: 'rtc_offer', sdp: offer.sdp, type: offer.type }));
}

function setupInputListenersRTC(vid) {
    let lastMoveTime = 0;
    vid.addEventListener('mousemove', (e) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        const now = Date.now();
        if (now - lastMoveTime < 16) return; // 60Hz Limit (Sweet spot for responsiveness)
        lastMoveTime = now;
        const coords = getScaledCoords(e, vid);
        sendEvent({ t: 'mm', x: coords.x, y: coords.y });
    });
    const handleClick = (e, pressed) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        e.preventDefault();
        const coords = getScaledCoords(e, vid);
        const btn = e.button === 0 ? 'left' : 'right';
        sendEvent({ t: 'mm', x: coords.x, y: coords.y });
        sendEvent({ t: 'mc', b: btn, p: pressed });
    };
    vid.addEventListener('mousedown', (e) => handleClick(e, true));
    vid.addEventListener('mouseup', (e) => handleClick(e, false));
    vid.addEventListener('contextmenu', (e) => e.preventDefault());
    vid.addEventListener('wheel', (e) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        e.preventDefault();
        sendEvent({ t: 'scroll', dy: Math.sign(e.deltaY) });
    }, { passive: false });
}

function handleTextMessage(msg) {
    if (msg.type === 'handshake') {
        serverMetadata = msg.data;
        monitors = msg.data.monitors;
        fetchDevices();
        if (monitors && monitors.length > 0) populateDisplaySelect(monitors);
    } else if (msg.t === 'rtc_answer' && pc) {
        // WebRTC answer from agent
        pc.setRemoteDescription(new RTCSessionDescription({ sdp: msg.sdp, type: msg.type }));
    } else if (msg.t === 'rtc_ice' && pc && msg.candidate) {
        pc.addIceCandidate(new RTCIceCandidate(msg.candidate)).catch(() => {});
    }
}

function handleBinaryMessage(buffer) {
    const view = new DataView(buffer);
    const type = view.getUint8(0);
    const id = view.getUint8(1);
    const data = buffer.slice(2);

    switch (type) {
        case 1: // Video
            if (activeView === 'remote') updateBinaryVideo(id, data);
            break;
        case 2: // Webcam
            updateBinaryWebcam(data);
            break;
        case 3: // Audio Mic
        case 4: // Audio Speaker
            playBinaryAudio(data);
            break;
        case 5: // Stats (JSON)
            const statsStr = new TextDecoder().decode(data);
            updateStatusUI(JSON.parse(statsStr));
            break;
        case 7: // Processes (JSON)
            updateProcessList(JSON.parse(new TextDecoder().decode(data)));
            break;
        case 8: // Clipboard (Text)
            handleRemoteClipboard(new TextDecoder().decode(data));
            break;
    }
}

const knownMonitors = new Set();

function updateBinaryVideo(monitorIdx, data) {
    // Dynamically register new monitors in the dropdown
    if (!knownMonitors.has(monitorIdx)) {
        knownMonitors.add(monitorIdx);
        const select = document.getElementById('display-select');
        // Rebuild dropdown: "All" + one per known monitor
        select.innerHTML = '<option value="all">All Screens</option>';
        [...knownMonitors].sort().forEach(idx => {
            const opt = document.createElement('option');
            opt.value = idx;
            opt.innerText = `Screen ${idx + 1}`;
            select.appendChild(opt);
        });
    }

    let img = document.getElementById(`monitor-${monitorIdx}`);
    if (!img) {
        img = document.createElement('img');
        img.id = `monitor-${monitorIdx}`;
        img.className = 'monitor-view';
        document.getElementById('screens-grid').appendChild(img);
        setupInputListeners(img, monitorIdx);
    }
    img.classList.toggle('hidden', displayMode !== 'all' && displayMode != monitorIdx);
    if (monitorUrls[monitorIdx]) URL.revokeObjectURL(monitorUrls[monitorIdx]);
    const blob = new Blob([data], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    img.src = url;
    monitorUrls[monitorIdx] = url;
}

function updateBinaryWebcam(data) {
    const overlay = document.getElementById('webcam-overlay');
    const img = document.getElementById('webcam-img');
    if (webcamActive) {
        overlay.classList.remove('hidden');
        const blob = new Blob([data], { type: 'image/jpeg' });
        const url = URL.createObjectURL(blob);
        if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
        img.src = url;
    } else { overlay.classList.add('hidden'); }
}

function playBinaryAudio(data) {
    if (!audioContext || audioContext.state === 'suspended') return;
    const int16 = new Int16Array(data);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;
    const buffer = audioContext.createBuffer(1, float32.length, 16000);
    buffer.getChannelData(0).set(float32);
    const node = audioContext.createBufferSource();
    const gainNode = audioContext.createGain();
    gainNode.gain.value = audioVolume;
    node.buffer = buffer;
    node.connect(gainNode);
    gainNode.connect(audioContext.destination);
    node.start();
}

async function fetchDevices() {
    try {
        const response = await fetch('/api/devices');
        const devices = await response.json();
        updateDeviceGrid(devices);
    } catch (err) { console.error('MRL- Device fetch error:', err); }
}

function updateDeviceGrid(devices) {
    const grid = document.getElementById('device-grid');
    grid.innerHTML = '';
    devices.forEach(dev => {
        const isOffline = dev.status === 'Offline';
        const card = document.createElement('div');
        card.className = `device-card ${isOffline ? 'offline' : ''} ${selectedDeviceId === dev.hostname ? 'selected' : ''}`;
        card.onclick = () => {
            if (isOffline) return;
            selectedDeviceId = dev.hostname;
            selectDevice(dev.hostname);
        };
        const statusColor = dev.status === 'Active' ? '#39FF14' : (dev.status === 'Inactive' ? '#FFA500' : '#555');
        card.innerHTML = `
            <div class="card-top">
                <i class="fas fa-power-off" style="color: ${statusColor}"></i>
                <div class="card-header-text">
                    <div class="card-hostname">${dev.hostname.toUpperCase()}</div>
                    <div class="card-subtitle">${dev.status}</div>
                </div>
            </div>
            <div class="card-stats">
                <span class="stat-label">User</span><span class="stat-value">${dev.user}</span>
                <span class="stat-label">RAM</span><span class="stat-value">${dev.ram}%</span>
                <span class="stat-label">Disk</span><span class="stat-value">${dev.disk}%</span>
            </div>
        `;
        grid.appendChild(card);
    });
}

function initAudio() {
    if (audioContext) return;
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
}

function setupNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const view = item.getAttribute('data-view');
            navigateTo(view);
            if (view === 'recordings') fetchRecordings();
            if (view === 'remote') initAudio();
        });
    });
}

function navigateTo(view) {
    if (activeView === view) return;
    activeView = view;
    
    // Smooth transition using classes
    document.querySelectorAll('.page-view').forEach(p => {
        p.classList.toggle('active', p.id === `view-${view}`);
    });
    
    document.querySelectorAll('.nav-item').forEach(i => {
        i.classList.toggle('active', i.getAttribute('data-view') === view);
    });
    
    // Auto-disconnect/connect logic
    if (view === 'dashboard') disconnectSession();
    if (view === 'tools') fetchProcesses();
}

function disconnectSession() {
    console.log("Disconnecting session...");
    if (pc) { pc.close(); pc = null; dataChannel = null; }
    sendEvent({ t: 'deselect_device' });
    document.getElementById('nav-remote').classList.add('hidden');
    document.getElementById('nav-tools').classList.add('hidden');
    document.getElementById('active-host-name').innerText = 'NO ACTIVE SYSTEM';
    document.getElementById('remote-settings').classList.add('hidden');
    document.getElementById('screens-grid').innerHTML = '';
}

async function fetchRecordings() {
    try {
        const response = await fetch('/api/recordings');
        const data = await response.json();
        const body = document.getElementById('recordings-body');
        body.innerHTML = '';
        if (data.length === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#666; padding:40px;">Archive empty.</td></tr>';
            return;
        }
        data.forEach(rec => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><i class="fas fa-shield-alt" style="color:#39FF14; margin-right:10px;"></i> ${rec.name}</td>
                <td>${rec.date}</td>
                <td>${rec.size}</td>
                <td><a href="/recordings/${rec.name}" download class="btn-dl">Export</a></td>
            `;
            body.appendChild(tr);
        });
    } catch (err) { console.error('MRL- Archive error:', err); }
}

function updateLiveStats(data) {
    if(!data) return;
    const statsText = `CPU: ${data.cpu}% | RAM: ${data.ram}%`;
    document.getElementById('header-status-text').innerText = `${selectedDeviceId || 'MRL'} - ${statsText}`;
    
    // Also update dashboard card if visible
    const card = document.querySelector(`[data-id="${selectedDeviceId}"]`);
    if (card) {
        const cpuBar = card.querySelector('.cpu-bar-inner');
        const ramBar = card.querySelector('.ram-bar-inner');
        if(cpuBar) cpuBar.style.width = data.cpu + '%';
        if(ramBar) ramBar.style.width = data.ram + '%';
    }
}

function fetchProcesses() {
    if (!selectedDeviceId) return;
    if (dataChannel && dataChannel.readyState === 'open') {
        dataChannel.send(JSON.stringify({ t: "get_processes" }));
    } else {
        sendEvent({ t: "get_processes", id: selectedDeviceId });
    }
}

function renderProcessesFast(procs) {
    const tbody = document.getElementById('procs-body');
    if (!tbody) return;
    
    // Use DocumentFragment for "Fast AF" injection
    const fragment = document.createDocumentFragment();
    procs.sort((a,b) => b.cpu - a.cpu).forEach(p => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${p.name}</td>
            <td>${p.pid}</td>
            <td>${p.cpu.toFixed(1)}%</td>
            <td>${p.ram.toFixed(1)} MB</td>
            <td><button class="btn-kill" onclick="killProcess(${p.pid})">Kill</button></td>
        `;
        fragment.appendChild(tr);
    });
    
    requestAnimationFrame(() => {
        tbody.innerHTML = '';
        tbody.appendChild(fragment);
    });
}

function updateStatusUI(msg) {
    const status = msg.status || 'Active';
    const isRec = msg.is_recording || false;
    const dot = document.getElementById('header-status-dot');
    const text = document.getElementById('header-status-text');
    if (dot) dot.className = `dot ${status === 'Active' ? 'green' : (status === 'Inactive' ? 'orange' : 'gray')}`;
    if (text) text.innerText = `MRL- ${status.toUpperCase()}`;
    const recInd = document.getElementById('rec-indicator');
    if (recInd) recInd.classList.toggle('hidden', !isRec);
    isRecording = isRec;
}

function populateDisplaySelect(monitorsList) {
    const select = document.getElementById('display-select');
    if (!select) return;
    select.innerHTML = '';
    
    monitorsList.forEach((mon, i) => {
        const opt = document.createElement('option');
        opt.value = mon.index;
        // MSS Monitor 0 is "All", 1+ are physical
        opt.innerText = mon.index === 0 ? "All Screens" : `Screen ${mon.index}`;
        select.appendChild(opt);
    });
    
    // Add event listener once
    if (!select.dataset.listener) {
        select.addEventListener('change', (e) => {
            const index = e.target.value;
            sendEvent({ t: 'select_monitor', index: index });
            showToast(`Switched to Screen ${index == 0 ? 'View All' : index}`);
        });
        select.dataset.listener = "true";
    }
}

function setupControls() {
    document.getElementById('mouse-control').addEventListener('change', (e) => mouseEnabled = e.target.checked);
    document.getElementById('keyboard-control').addEventListener('change', (e) => keyboardEnabled = e.target.checked);
    document.getElementById('record-control').addEventListener('change', (e) => sendEvent({ t: 'toggle_recording', v: e.target.checked }));
    document.getElementById('webcam-control').addEventListener('change', (e) => {
        webcamActive = e.target.checked;
        sendEvent({ t: 'toggle_webcam', v: webcamActive });
    });
    document.getElementById('mic-control').addEventListener('change', (e) => { initAudio(); sendEvent({ t: 'toggle_mic', v: e.target.checked }); });
    document.getElementById('speaker-control').addEventListener('change', (e) => { initAudio(); sendEvent({ t: 'toggle_speaker', v: e.target.checked }); });
    document.getElementById('volume-slider').addEventListener('input', (e) => {
        audioVolume = e.target.value / 100;
        document.getElementById('volume-val').innerText = e.target.value;
    });
    document.getElementById('quality-slider').addEventListener('input', (e) => {
        document.getElementById('quality-val').innerText = e.target.value;
        sendEvent({ t: 'set_quality', v: e.target.value });
    });
    document.getElementById('fps-slider').addEventListener('input', (e) => {
        document.getElementById('fps-val').innerText = e.target.value;
        sendEvent({ t: 'set_fps', v: e.target.value });
    });
    document.getElementById('clipboard-control').addEventListener('change', (e) => {
        clipboardSync = e.target.checked;
        if (clipboardSync) startClipboardPolling();
    });
    document.getElementById('refresh-procs').addEventListener('click', fetchProcesses);
    window.addEventListener('keydown', (e) => { if (keyboardEnabled && activeView === 'remote') sendEvent({ t: 'kd', k: e.key }); });
    window.addEventListener('keyup', (e) => { if (keyboardEnabled && activeView === 'remote') sendEvent({ t: 'ku', k: e.key }); });
}

function setupInputListeners(img, monitorIdx) {
    let lastMoveTime = 0;
    img.addEventListener('mousemove', (e) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        const now = Date.now();
        if (now - lastMoveTime < 16) return; // ~60fps throttle
        lastMoveTime = now;
        const coords = getScaledCoords(e, img);
        sendEvent({ t: 'mm', x: coords.x, y: coords.y });
    });
    const handleClick = (e, pressed) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        e.preventDefault();
        const coords = getScaledCoords(e, img);
        const btn = e.button === 0 ? 'left' : 'right';
        // Move first then click so position is accurate
        sendEvent({ t: 'mm', x: coords.x, y: coords.y });
        sendEvent({ t: 'mc', b: btn, p: pressed });
    };
    img.addEventListener('mousedown', (e) => handleClick(e, true));
    img.addEventListener('mouseup', (e) => handleClick(e, false));
    img.addEventListener('contextmenu', (e) => e.preventDefault());
    // Scroll wheel support
    img.addEventListener('wheel', (e) => {
        if (!mouseEnabled || activeView !== 'remote') return;
        e.preventDefault();
        sendEvent({ t: 'scroll', dy: Math.sign(e.deltaY) });
    }, { passive: false });
}

function getScaledCoords(e, img) {
    const rect = img.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    // Use the JPEG natural dimensions as the remote screen resolution
    const scaleX = (img.naturalWidth || img.clientWidth) / img.clientWidth;
    const scaleY = (img.naturalHeight || img.clientHeight) / img.clientHeight;
    return {
        x: Math.round(x * scaleX),
        y: Math.round(y * scaleY)
    };
}

function setupQuickActions() {
    document.getElementById('btn-fullscreen').addEventListener('click', () => {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen();
        else document.exitFullscreen();
    });
    document.getElementById('btn-disconnect').addEventListener('click', () => {
        if (socket) socket.close();
        window.location.reload();
    });
    document.getElementById('close-webcam').addEventListener('click', () => {
        webcamActive = false;
        document.getElementById('webcam-control').checked = false;
        sendEvent({ t: 'toggle_webcam', v: false });
        document.getElementById('webcam-overlay').classList.add('hidden');
    });
}

function setupModals() {
    const modal = document.getElementById('modal-add-user');
    document.getElementById('btn-add-user').onclick = () => modal.classList.remove('hidden');
    document.querySelector('.btn-close-modal').onclick = () => modal.classList.add('hidden');
    document.getElementById('btn-copy-script').onclick = () => {
        const code = document.getElementById('powershell-command').innerText;
        navigator.clipboard.writeText(code);
        showToast('Command Copied to Clipboard!');
    };
    window.onclick = (e) => { if (e.target == modal) modal.classList.add('hidden'); };
}

function setupResizing() {
    const el = document.getElementById('webcam-overlay');
    const resizer = el.querySelector('.resizer');
    let startX, startY, startWidth, startHeight;

    resizer.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX;
        startY = e.clientY;
        startWidth = parseInt(document.defaultView.getComputedStyle(el).width, 10);
        startHeight = parseInt(document.defaultView.getComputedStyle(el).height, 10);
        document.documentElement.addEventListener('mousemove', doDrag, false);
        document.documentElement.addEventListener('mouseup', stopDrag, false);
    });

    function doDrag(e) {
        el.style.width = (startWidth + e.clientX - startX) + 'px';
        el.style.height = (startHeight + e.clientY - startY) + 'px';
    }

    function stopDrag(e) {
        document.documentElement.removeEventListener('mousemove', doDrag, false);
        document.documentElement.removeEventListener('mouseup', stopDrag, false);
    }
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position: fixed; bottom: 60px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; pointer-events: none;';
    document.body.appendChild(container);
}

function showToast(message) {
    const toast = document.createElement('div');
    toast.style.cssText = 'background: #222; border-left: 4px solid #39FF14; color: white; padding: 15px 25px; border-radius: 4px; font-size: 13px; font-weight: 600; box-shadow: 0 5px 15px rgba(0,0,0,0.5); transform: translateX(120%); transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); pointer-events: auto;';
    toast.innerHTML = `<i class="fas fa-check-circle" style="color:#39FF14; margin-right:10px;"></i> ${message}`;
    document.getElementById('toast-container').appendChild(toast);
    setTimeout(() => toast.style.transform = 'translateX(0)', 100);
    setTimeout(() => { toast.style.transform = 'translateX(120%)'; setTimeout(() => toast.remove(), 300); }, 5000);
}

function makeDraggable(el, handle) {
    if (!el || !handle) return;
    let pos3 = 0, pos4 = 0;
    handle.onmousedown = (e) => {
        if (e.target.closest('.webcam-controls')) return;
        e.preventDefault(); pos3 = e.clientX; pos4 = e.clientY;
        document.onmouseup = () => { document.onmouseup = null; document.onmousemove = null; };
        document.onmousemove = (e) => { e.preventDefault(); const p1 = pos3 - e.clientX; const p2 = pos4 - e.clientY; pos3 = e.clientX; pos4 = e.clientY; el.style.top = (el.offsetTop - p2) + "px"; el.style.left = (el.offsetLeft - p1) + "px"; el.style.bottom = "auto"; el.style.right = "auto"; };
    };
}

function sendEvent(data) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
}
