let socket = null;
let pc = null;
let dataChannel = null;
let selectedDeviceId = null;
let activeView = 'dashboard';
let mouseEnabled = true;
let keyboardEnabled = true;
let allDevices = [];
let videoTracksReceived = 0;
let iceCandidateQueue = [];

// App Initialization
document.addEventListener('DOMContentLoaded', () => {
    connect();
    setupDraggable('webcam-overlay');
    setupSliders();
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
    if (msg.t === 'rtc_offer') handleRtcOffer(msg); // Optional fallback if agent offers
    else if (msg.t === 'rtc_answer') handleRtcAnswer(msg);
    else if (msg.t === 'rtc_ice') {
        if (pc && pc.remoteDescription) {
            pc.addIceCandidate(new RTCIceCandidate(msg.candidate)).catch(e => console.error(e));
        } else {
            iceCandidateQueue.push(msg.candidate);
        }
    }
    else if (msg.t === 'monitors') populateDisplaySelect(msg.data);
    else if (msg.t === 'devices') {
        renderDeviceGrid(msg.data);
        renderActiveSidebar(msg.data);
        updateDetailedSpecs(msg.data.find(d => d.hostname === selectedDeviceId));
    }
    else if (msg.t === 'stats') updateStats(msg.data);
    else if (msg.t === 'process_list') renderProcesses(msg.data);
    else if (msg.t === 'specs') {
        const s = msg.data || {};
        document.getElementById('spec-user').innerText = s.user || '--';
        document.getElementById('spec-os').innerText = s.os || '--';
        document.getElementById('spec-cpu').innerText = s.cpu || '--';
        document.getElementById('spec-ram').innerText = s.ram || '--';
        document.getElementById('spec-disk').innerText = s.disk || '--';
        document.getElementById('spec-gpu').innerText = s.gpu || '--';
        if (s.monitors) populateDisplaySelect(s.monitors);
    }
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
        allDevices = await res.json();
        renderDeviceGrid(allDevices);
        renderActiveSidebar(allDevices);
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
                    <div class="spec-mini"><i class="fas fa-desktop"></i> ${specs.monitors ? specs.monitors.length - 1 : 1} Screens</div>
                </div>
            </div>
        `;
    }).join('');
}

function renderActiveSidebar(devices) {
    const container = document.getElementById('dynamic-remote-links');
    container.innerHTML = devices.filter(d => d.status === 'Active').map(d => `
        <div class="nav-item ${selectedDeviceId === d.hostname ? 'active' : ''}" onclick="selectDevice('${d.hostname}')">
            <i class="fas fa-desktop" style="color:var(--pro-green)"></i> ${d.specs?.name || d.hostname}
        </div>
    `).join('');
}

function updateDetailedSpecs(device) {
    if (!device) return;
    document.getElementById('spec-user').innerText = device.specs.user || '--';
    document.getElementById('spec-os').innerText = device.specs.os || '--';
    document.getElementById('spec-cpu').innerText = device.specs.cpu || '--';
    document.getElementById('spec-ram').innerText = device.specs.ram || '--';
    document.getElementById('spec-disk').innerText = device.specs.disk || '--';
    document.getElementById('spec-gpu').innerText = device.specs.gpu || '--';

    const sel = document.getElementById('webcam-select');
    if (device.specs.cameras && device.specs.cameras.length > 0) {
        sel.innerHTML = device.specs.cameras.map((c, i) => `<option value="${i}">${c}</option>`).join('');
    } else {
        sel.innerHTML = `<option value="0">Cam 0 (Default)</option><option value="1">Cam 1</option><option value="2">Cam 2</option>`;
    }
}

// Session Logic
async function selectDevice(deviceId) {
    selectedDeviceId = deviceId;
    document.getElementById('active-host-name').innerText = `SESSION: ${deviceId}`;
    
    // Explicitly link this portal to the target client on the signaling server
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ t: 'select_device', id: deviceId.toLowerCase() }));
    }
    
    // Instant DOM Spec & Display Prep from memory
    const target = allDevices.find(d => d.hostname === deviceId);
    if (target && target.specs) {
        updateDetailedSpecs(target);
        if (target.specs.monitors) populateDisplaySelect(target.specs.monitors);
    }
    
    navigateTo('remote');
    startWebRTC(deviceId);
}

let isConnecting = false;
async function startWebRTC(deviceId) {
    if (isConnecting) return;
    isConnecting = true;

    if (pc) pc.close(); 
    videoTracksReceived = 0;
    iceCandidateQueue = [];
    
    pc = new RTCPeerConnection({
        iceServers: [
            { urls: 'stun:stun.l.google.com:19302' },
            { urls: 'stun:stun1.l.google.com:19302' },
            { urls: 'stun:stun2.l.google.com:19302' },
            { urls: 'stun:stun3.l.google.com:19302' },
            { urls: 'stun:stun4.l.google.com:19302' }
        ]
    });

    pc.onicecandidate = (e) => {
        if (e.candidate) {
            socket.send(JSON.stringify({ t: 'rtc_ice', candidate: e.candidate }));
        }
    };

    pc.ontrack = (e) => {
        if (e.track.kind === 'audio') return;
        
        videoTracksReceived++;
        const video = videoTracksReceived === 1 
            ? document.getElementById('remote-video') 
            : document.getElementById('webcam-video');
            
        if (e.streams && e.streams[0]) {
            video.srcObject = e.streams[0];
        } else {
            if (!video.srcObject) video.srcObject = new MediaStream();
            video.srcObject.addTrack(e.track);
        }
        video.play().catch(err => console.error("Video Play Error:", err));
        isConnecting = false; // Connection successful
    };

    pc.oniceconnectionstatechange = () => {
        console.log("ICE:", pc.iceConnectionState);
        if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
            isConnecting = false;
        }
        if (pc && (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'closed')) {
            isConnecting = false;
            console.log("Device Connection Lost.");
            disconnectSession();
            navigateTo('dashboard');
        }
    };

    // Data Channel for Controls
    dataChannel = pc.createDataChannel('mrl-control');
    dataChannel.onopen = () => console.log("P2P Control Active");

    // CRITICAL FIX: Chrome must proactively bundle recvonly transceivers into the Offer! 
    // Failing to allocate these will result in an empty SDP payload, which universally crashes the Python aiortc backend mapping algorithm.
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    console.log("[SIGNAL] Sending Offer to", deviceId);
    socket.send(JSON.stringify({ t: 'select_device', id: deviceId }));
    socket.send(JSON.stringify({ t: 'rtc_offer', id: deviceId, sdp: pc.localDescription.sdp, type: pc.localDescription.type }));
    
    setupInputListeners();
}

async function handleRtcAnswer(msg) {
    if (!pc) return;
    await pc.setRemoteDescription(new RTCSessionDescription(msg));
    
    // Drain Instance Cache: Match RustDesk/AnyDesk Speed
    while (iceCandidateQueue.length > 0) {
        const cand = iceCandidateQueue.shift();
        pc.addIceCandidate(new RTCIceCandidate(cand)).catch(e => console.error("Cached Candidate Apply Failed", e));
    }
}

async function handleRtcOffer(msg) {
    // Fallback if agent initiates (not used in current flow)
    if (!pc) return;
    await pc.setRemoteDescription(new RTCSessionDescription(msg));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    socket.send(JSON.stringify({ t: 'rtc_answer', sdp: answer.sdp, type: answer.type }));
}

function disconnectSession() {
    if (pc) pc.close();
    pc = null;
    dataChannel = null;
    document.getElementById('remote-video').srcObject = null;
    document.getElementById('active-host-name').innerText = 'ROOT CONSOLE';

    // Aggressively scrub all telemetry and spec ghosts
    document.getElementById('stat-latency').innerText = '-- ms';
    document.getElementById('stat-bitrate').innerText = '-- kbps';
    document.getElementById('stat-fps').innerText = '--';
    document.getElementById('spec-os').innerText = '...';
    document.getElementById('spec-cpu').innerText = '...';
    document.getElementById('spec-ram').innerText = '...';
    document.getElementById('spec-disk').innerText = '...';
    document.getElementById('spec-gpu').innerText = '...';
    document.getElementById('procs-body').innerHTML = '';
    document.getElementById('display-select').innerHTML = '<option value="0">All Screens (Combined)</option>';
    
    // Scrub Webcam Overlay
    const webcamImg = document.getElementById('webcam-feed');
    if (webcamImg) {
        webcamImg.src = '';
        webcamImg.parentElement.style.display = 'none';
        document.getElementById('webcam-toggle').classList.remove('active');
    }
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
    select.innerHTML = monitors.length > 2 ? '<option value="0">Multi-Cam Grid (All Devices)</option>' : '';
    
    monitors.forEach((m, idx) => {
        if (idx === 0) return; // Always skip the 0th mss output for the individual physical screens 
        const opt = document.createElement('option');
        opt.value = idx;
        opt.innerText = `Display ${idx} (${m.width}x${m.height})`;
        if (idx === 1) opt.selected = true;
        select.appendChild(opt);
    });

    select.onchange = (e) => {
        if (dataChannel && dataChannel.readyState === 'open') {
            dataChannel.send(JSON.stringify({ t: 'select_monitor', index: parseInt(e.target.value) }));
        }
    };
}

// Stats & Telemetry
function updateStats(data) {
    document.getElementById('stat-latency').innerText = `${data.latency || '--'} ms`;
    document.getElementById('stat-bitrate').innerText = `${data.bitrate || '--'} kbps`;
    document.getElementById('stat-fps').innerText = data.fps || '--';
    const footer = document.getElementById('footer-stats');
    if (footer) footer.innerText = `SIGNAL: ACTIVE | ${data.latency || '--'}ms`;
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

function switchWebcam(idx) {
    const wcb = document.getElementById('webcam-control');
    if (wcb && wcb.checked) {
        // Force the pipeline to cleanly release and re-bind cv2.VideoCapture
        sendControl({ t: 'select_camera', index: parseInt(idx) });
    }
}

// Control Toggles
document.getElementById('mouse-control').onchange = (e) => mouseEnabled = e.target.checked;
document.getElementById('keyboard-control').onchange = (e) => keyboardEnabled = e.target.checked;
document.getElementById('audio-toggle').onchange = (e) => {
    sendControl({ t: 'toggle_audio', v: e.target.checked });
};
document.getElementById('webcam-control').onchange = (e) => {
    const v = document.getElementById('webcam-overlay');
    v.classList.toggle('hidden', !e.target.checked);
    sendControl({ t: 'toggle_webcam', v: e.target.checked });
};

// Advanced Controls logic
function setupSliders() {
    const fps = document.getElementById('slider-fps');
    const qual = document.getElementById('slider-quality');
    
    fps.oninput = (e) => {
        document.getElementById('val-fps').innerText = e.target.value;
        sendControl({ t: 'set_fps', v: parseInt(e.target.value) });
    };

    qual.oninput = (e) => {
        const v = parseInt(e.target.value);
        let label = 'medium';
        if (v < 30) label = 'low';
        else if (v > 75) label = 'high';
        document.getElementById('val-quality').innerText = label;
        sendControl({ t: 'set_quality', v: v });
    };
}

function setupDraggable(id) {
    const el = document.getElementById(id);
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;

    el.onmousedown = (e) => {
        e = e || window.event;
        e.preventDefault();
        pos3 = e.clientX;
        pos4 = e.clientY;
        document.onmouseup = () => {
            document.onmouseup = null;
            document.onmousemove = null;
        };
        document.onmousemove = (e) => {
            e = e || window.event;
            e.preventDefault();
            pos1 = pos3 - e.clientX;
            pos2 = pos4 - e.clientY;
            pos3 = e.clientX;
            pos4 = e.clientY;
            el.style.top = (el.offsetTop - pos2) + "px";
            el.style.left = (el.offsetLeft - pos1) + "px";
            el.style.bottom = 'auto'; // Break initial bottom-right anchor
            el.style.right = 'auto';
        };
    };
}
