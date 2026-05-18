/**
 * Simplified LiveKit Web Client
 *
 * Auto-connects and displays the simulation stream.
 * No UI controls, just the video feed.
 */

const { Room, RoomEvent } = LivekitClient;

const video = document.getElementById('video');
let room = null;
let livekitUrl = null;
let targetTrackName = null;
let subscriptionPoll = null;
let reconnectTimer = null;

function publicationName(publication) {
    return publication?.trackName || publication?.name || publication?.track?.name || null;
}

function shouldUsePublication(publication) {
    const name = publicationName(publication);
    return !targetTrackName || name === targetTrackName;
}

function attachTrack(track, publication) {
    if (track.kind !== 'video' || !shouldUsePublication(publication)) return;
    video.replaceChildren();
    track.attach(video);
    console.log(`Stream attached (${publicationName(publication) || 'unnamed'})`);
}

function subscribeIfTarget(publication) {
    if (publication?.kind && publication.kind !== 'video') return;

    if (!shouldUsePublication(publication)) {
        const name = publicationName(publication);
        console.log(`Skipping video track ${name}; waiting for ${targetTrackName}`);
        return;
    }

    if (publication.track) {
        attachTrack(publication.track, publication);
    } else if (publication.setSubscribed) {
        publication.setSubscribed(true);
    }
}

function scanForTargetTrack() {
    if (!room) return;

    room.remoteParticipants.forEach((participant) => {
        participant.trackPublications.forEach((publication) => {
            subscribeIfTarget(publication);
        });
    });
}

function startSubscriptionPoll() {
    stopSubscriptionPoll();
    subscriptionPoll = setInterval(scanForTargetTrack, 1000);
}

function stopSubscriptionPoll() {
    if (subscriptionPoll) {
        clearInterval(subscriptionPoll);
        subscriptionPoll = null;
    }
}

function scheduleReconnect() {
    if (reconnectTimer) return;

    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        init();
    }, 5000);
}

async function init() {
    try {
        const resp = await fetch('/info');
        const info = await resp.json();
        livekitUrl = info.livekit_url;
        targetTrackName = info.track_name || null;
        await connect();
    } catch (err) {
        console.error('Failed to initialize stream', err);
        scheduleReconnect();
    }
}

async function connect() {
    if (room) return;

    try {
        const tokenResp = await fetch('/token');
        const token = await tokenResp.text();

        room = new Room();

        room.on(RoomEvent.TrackSubscribed, (track, publication) => {
            attachTrack(track, publication);
        });

        room.on(RoomEvent.TrackPublished, (publication) => {
            if (publication.kind === 'video') subscribeIfTarget(publication);
        });

        room.on(RoomEvent.Disconnected, () => {
            console.log('Disconnected');
            room = null;
            video.removeAttribute('src');
            video.load();
            stopSubscriptionPoll();
            scheduleReconnect();
        });

        await room.connect(livekitUrl, token, { autoSubscribe: false });
        console.log('Connected to LiveKit room');
        scanForTargetTrack();
        startSubscriptionPoll();

    } catch (err) {
        console.error('Connection failed', err);
        stopSubscriptionPoll();
        if (room) {
            room.disconnect();
        }
        room = null;
        scheduleReconnect();
    }
}

// Start immediately
init();
