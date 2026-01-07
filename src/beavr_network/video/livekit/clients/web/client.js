/**
 * Simplified LiveKit Web Client
 *
 * Auto-connects and displays the simulation stream.
 * No UI controls, just the video feed.
 */

const { Room, RoomEvent, ConnectionState } = LivekitClient;

const video = document.getElementById('video');
let room = null;
let livekitUrl = null;

async function init() {
    try {
        const resp = await fetch('/info');
        const info = await resp.json();
        livekitUrl = info.livekit_url;
        await connect();
    } catch (err) {
        console.error('Failed to initialize stream', err);
    }
}

async function connect() {
    if (room) return;

    try {
        const tokenResp = await fetch('/token');
        const token = await tokenResp.text();

        room = new Room();

        room.on(RoomEvent.TrackSubscribed, (track) => {
            if (track.kind === 'video') {
                track.attach(video);
                console.log('Stream attached');
            }
        });

        room.on(RoomEvent.Disconnected, () => {
            console.log('Disconnected');
            room = null;
            // Attempt to reconnect after a delay
            setTimeout(init, 5000);
        });

        await room.connect(livekitUrl, token);
        console.log('Connected to simulation room');

    } catch (err) {
        console.error('Connection failed', err);
        room = null;
        setTimeout(init, 5000);
    }
}

// Start immediately
init();
