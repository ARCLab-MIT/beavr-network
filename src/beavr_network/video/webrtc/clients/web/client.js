const video = document.getElementById('video');
const statusEl = document.getElementById('status');
const btnConnect = document.getElementById('btn-connect');
const btnDisconnect = document.getElementById('btn-disconnect');
const clientId = 'browser_' + Math.random().toString(36).slice(2, 9);

let pc = null;

function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = `status ${cls}`;
}

async function loadInfo() {
    try {
        const resp = await fetch('/info');
        const info = await resp.json();
        const infoEl = document.querySelector('.info');
        if (info && info.resolution && info.fps && infoEl) {
            infoEl.innerHTML = `<strong>Resolution:</strong> ${info.resolution[0]}x${info.resolution[1]} @ ${info.fps}fps<br><strong>Codec:</strong> H.264 via WebRTC`;
        }
    } catch (err) {
        // ignore
    }
}

async function connect() {
    if (pc) {
        setStatus('Already connected', 'connected');
        return;
    }

    setStatus('Connecting...', 'connecting');

    pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });

    pc.addTransceiver('video', { direction: 'recvonly' });

    pc.ontrack = (e) => {
        video.srcObject = e.streams[0];
        setStatus('Streaming', 'connected');
    };

    pc.onicecandidate = async (e) => {
        if (e.candidate) {
            try {
                await fetch('/ice', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        client_id: clientId,
                        candidate: e.candidate.candidate,
                        sdpMid: e.candidate.sdpMid,
                        sdpMLineIndex: e.candidate.sdpMLineIndex,
                    }),
                });
            } catch (err) {
                console.warn('Failed to send ICE', err);
            }
        }
    };

    pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'connected') {
            setStatus('Streaming', 'connected');
        } else if (pc.connectionState === 'failed') {
            setStatus('Connection failed', 'error');
        } else if (pc.connectionState === 'disconnected') {
            setStatus('Disconnected', 'error');
        }
    };

    // Create offer and send via HTTP
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const resp = await fetch('/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            client_id: clientId,
            type: 'offer',
            sdp: offer.sdp,
        }),
    });

    const answer = await resp.json();
    if (answer.sdp) {
        await pc.setRemoteDescription({
            type: 'answer',
            sdp: answer.sdp,
        });

        if (answer.candidates) {
            for (const c of answer.candidates) {
                try {
                    await pc.addIceCandidate({
                        candidate: c.candidate,
                        sdpMid: c.sdpMid,
                        sdpMLineIndex: c.sdpMLineIndex,
                    });
                } catch (err) {
                    console.warn('Failed to add server ICE', err);
                }
            }
        }
    } else {
        setStatus('No SDP answer', 'error');
    }
}

function disconnect() {
    if (pc) {
        pc.close();
        pc = null;
    }
    video.srcObject = null;
    setStatus('Disconnected', 'disconnected');
}

btnConnect?.addEventListener('click', connect);
btnDisconnect?.addEventListener('click', disconnect);

loadInfo();
