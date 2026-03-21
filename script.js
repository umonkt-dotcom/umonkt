let socket = null;
let pc = null;
let dataChannel = null;
let selectedDeviceId = null;
let activeView = 'dashboard';
let mouseEnabled = false;
let keyboardEnabled = false;

// App Initialization
document.addEventListener('DOMContentLoaded', () => {
    connect();
    setInterval(fetchDevices, 5000);
});

function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    
    socket.onopen = () => {
        console.log('MRL- Link Established');
        document.getElementById('header-status-dot').className = 'dot green';
        document.getElementById('header-status-text').innerText = 'MRL- ONLINE';
        fetchDevices();
    };

    socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };

    socket.onclose = () => {
        document.getElementById('header-status-dot').className = 'dot gray';
        document.getElementById('header-status-text').innerText = 'MRL- RECONNECTING...';
        setTimeout(connect, 2000);
    };
}

function handleMessage(msg) {
    if (msg.t === 'rtc_offer') handleRtcOffer(msg);
    else if (msg.t === 'rtc_ice') pc?.addIceCandidate(new RTCIceCandidate(msg.candidate));
    else if (msg.t === 'monitors') populateDisplaySelect(msg.data);
    else if (msg.t === 'stats') updateStats(msg.data);
    else if (msg.t === 'process_list') renderProcesses(msg.data);
}

// Navigation & UI
function navigateTo(viewName) {
    activeView = viewName;
    
    // Toggle Pages
    document.querySelectorAll('.page-view').forEach(v => v.classList.remove('active'));
    document.getElementById(`view-${viewName}`).classList.add('active');
    
    // Update Nav Sidebar
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (navItem) navItem.classList.add('active');

    // Toggle Right Control Panel
    const rightPanel = document.getElementById('right-control-panel');
    if (viewName === 'remote') {
        rightPanel.classList.remove('hidden');
    } else {
        rightPanel.classList.add('hidden');
        if (pc) disconnectSession();
    }
}

function toggleModal(id, show) {
    document.getElementById(id).classList.toggle('hidden', !show);
}

// Device Management
async function fetchDevices() {
    try {
        const res = await fetch('/api/devices');
        const devices = await res.json();
        renderDeviceGrid(devices);
        renderActiveSidebar(devices);
    } catch (e) { console.error("Fetch devices failed", e); }
}

function renderDeviceGrid(devices) {
    const grid = document.getElementById('device-grid');
    grid.innerHTML = devices.map(d => {
        const specs = d.specs || {};
        const online = d.status === 'Active';
        return `
            <div class="device-card" onclick="${online ? `selectDevice('${d.hostname}')` : ''}" style="opacity: ${online ? 1 : 0.5}">
                <div style="display:flex; justify-content:space-between; align-items:center">
                    <div style="font-weight:700; color:white">${specs.name || 'Agent'}</div>
                    <div class="dot ${online ? 'green' : 'gray'}"></div>
                </div>
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:10px;">${d.hostname}</div>
                <div style="display:flex; gap:8px; flex-wrap:wrap">
                    <div class="spec-mini"><i class="fas fa-microchip"></i> ${specs.cpu || '--'}</div>
                    <div class="spec-mini"><i class="fas fa-memory"></i> ${specs.ram || '--'}</div>
                    <div class="spec-mini"><i class="fas fa-desktop"></i> ${specs.monitors?.length || 1} Screens</div>
                </div>
            </div>
        `;
    }).join('');
}

function renderActiveSidebar(devices) {
    const container = document.getElementById('dynamic-remote-links');
    container.innerHTML = devices.filter(d => d.status === 'Active').map(d => `
        <div class="nav-item" onclick="selectDevice('${d.hostname}')">
            <i class="fas fa-desktop" style="color:var(--pro-green)"></i> ${d.specs?.name || d.hostname}
        </div>
    `).join('');
}

// Session Logic
async function selectDevice(deviceId) {
    selectedDeviceId = deviceId;
    document.getElementById('active-host-name').innerText = `SESSION: ${deviceId}`;
    navigateTo('remote');
    startWebRTC(deviceId);
}

async function startWebRTC(deviceId) {
    if (pc) pc.close();
    
    pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
    });

    pc.onicecandidate = (e) => {
        if (e.candidate) socket.send(JSON.stringify({ t: 'rtc_ice', candidate: e.candidate }));
    };

    pc.ontrack = (e) => {
        if (e.track.kind === 'video') {
            document.getElementById('remote-video').srcObject = e.streams[0];
        }
    };

    // Data Channel for Controls
    dataChannel = pc.createDataChannel('mrl-control');
    dataChannel.onopen = () => console.log("P2P Control Active");

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    socket.send(JSON.stringify({ t: 'select_device', id: deviceId }));
    socket.send(JSON.stringify({ t: 'rtc_offer', sdp: offer.sdp, type: offer.type }));
    
    setupInputListeners();
}

async function handleRtcOffer(msg) {
    // If agent sends an offer (less common in our current flow but handled)
    if (!pc) return;
    await pc.setRemoteDescription(new RTCSessionDescription(msg));
}

function disconnectSession() {
    if (pc) pc.close();
    pc = null;
    dataChannel = null;
    document.getElementById('remote-video').srcObject = null;
    document.getElementById('active-host-name').innerText = 'ROOT CONSOLE';
}

// Input Handling
function setupInputListeners() {
    const video = document.getElementById('remote-video');
    
    video.onmousemove = (e) => {
        if (!mouseEnabled) return;
        sendControl({ t: 'mm', x: e.offsetX, y: e.offsetY, w: video.clientWidth, h: video.clientHeight });
    };

    video.onmousedown = (e) => {
        if (!mouseEnabled) return;
        sendControl({ t: 'mc', b: e.button === 0 ? 'left' : 'right', p: true });
    };

    video.onmouseup = (e) => {
        if (!mouseEnabled) return;
        sendControl({ t: 'mc', b: e.button === 0 ? 'left' : 'right', p: false });
    };

    window.onkeydown = (e) => {
        if (!keyboardEnabled || activeView !== 'remote') return;
        sendControl({ t: 'kd', k: e.key });
    };

    window.onkeyup = (e) => {
        if (!keyboardEnabled || activeView !== 'remote') return;
        sendControl({ t: 'ku', k: e.key });
    };
}

function sendControl(data) {
    if (dataChannel && dataChannel.readyState === 'open') {
        dataChannel.send(JSON.stringify(data));
    }
}

// Monitor Switching
function populateDisplaySelect(monitors) {
    const select = document.getElementById('display-select');
    select.innerHTML = '<option value="all">All Screens (Combined)</option>';
    monitors.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.index;
        opt.innerText = `Monitor ${m.index + 1} (${m.width}x${m.height})`;
        select.appendChild(opt);
    });

    select.onchange = (e) => {
        socket.send(JSON.stringify({ t: 'select_monitor', index: e.target.value }));
    };
}

// Stats & Telemetry
function updateStats(data) {
    document.getElementById('stat-latency').innerText = `${data.latency || '--'} ms`;
    document.getElementById('stat-bitrate').innerText = `${data.bitrate || '--'} kbps`;
    document.getElementById('stat-fps').innerText = data.fps || '--';
}

// Process Manager
function renderProcesses(procs) {
    const body = document.getElementById('procs-body');
    body.innerHTML = procs.sort((a,b) => b.cpu - a.cpu).map(p => `
        <tr>
            <td>${p.name}</td>
            <td>${p.pid}</td>
            <td>${p.cpu}%</td>
            <td>${p.ram} MB</td>
            <td><button class="btn-kill" onclick="killProcess(${p.pid})">Kill</button></td>
        </tr>
    `).join('');
}

function killProcess(pid) {
    socket.send(JSON.stringify({ t: 'kill_process', pid }));
}

// Control Toggles
document.getElementById('mouse-control').onchange = (e) => mouseEnabled = e.target.checked;
document.getElementById('keyboard-control').onchange = (e) => keyboardEnabled = e.target.checked;
document.getElementById('webcam-control').onchange = (e) => {
    const v = document.getElementById('webcam-video');
    v.classList.toggle('hidden', !e.target.checked);
    socket.send(JSON.stringify({ t: 'toggle_webcam', v: e.target.checked }));
};
