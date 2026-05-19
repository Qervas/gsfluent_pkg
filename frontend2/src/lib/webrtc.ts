/**
 * Browser-side WebRTC client for /v1/render-sessions.
 *
 * Flow (mirrors apps/api/src/gsfluent_api/routes/render_sessions.py):
 *   POST /v1/render-sessions {run_id|model_id} -> session_id + ice_servers
 *   open WS, subscribe to events:render-session:{id} for inbound ICE
 *   createOffer -> setLocalDescription
 *   POST /v1/render-sessions/{id}/offer  -> SDP answer
 *   setRemoteDescription(answer)
 *   onicecandidate -> POST /v1/render-sessions/{id}/candidate
 *   onevent webrtc.candidate -> addIceCandidate
 *   close: DELETE /v1/render-sessions/{id}
 */

import { streamClient, type EventMsg } from "./ws";

export type RenderTarget = { run_id: string } | { model_id: string };

export class RenderSessionClient {
  pc: RTCPeerConnection | null = null;
  sessionId: string | null = null;
  videoTrack: MediaStreamTrack | null = null;
  private unsubEvents: (() => void) | null = null;
  private onTrack: ((track: MediaStreamTrack) => void) | null = null;
  private onState: ((state: RTCPeerConnectionState) => void) | null = null;

  setHandlers(opts: {
    onTrack?: (track: MediaStreamTrack) => void;
    onState?: (state: RTCPeerConnectionState) => void;
  }): void {
    this.onTrack = opts.onTrack ?? null;
    this.onState = opts.onState ?? null;
  }

  async connect(target: RenderTarget): Promise<void> {
    // 1) Create session.
    const createResp = await fetch("/v1/render-sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(target),
    });
    if (!createResp.ok) throw new Error(`create: ${await createResp.text()}`);
    const { session_id, ice_servers } = (await createResp.json()) as {
      session_id: string;
      ice_servers: RTCIceServer[];
    };
    this.sessionId = session_id;

    // 2) Subscribe to events channel for inbound ICE.
    this.unsubEvents = streamClient.subscribe(
      `events:render-session:${session_id}`,
      this.handleEvent,
    );

    // 3) Build peer connection.
    const pc = new RTCPeerConnection({ iceServers: ice_servers });
    this.pc = pc;
    pc.addTransceiver("video", { direction: "recvonly" });

    pc.ontrack = (ev) => {
      this.videoTrack = ev.track;
      this.onTrack?.(ev.track);
    };
    pc.onconnectionstatechange = () => this.onState?.(pc.connectionState);
    pc.onicecandidate = async (ev) => {
      if (!ev.candidate || !this.sessionId) return;
      await fetch(`/v1/render-sessions/${this.sessionId}/candidate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          candidate: ev.candidate.candidate,
          sdpMid: ev.candidate.sdpMid,
          sdpMLineIndex: ev.candidate.sdpMLineIndex,
        }),
      });
    };

    // 4) Offer/answer.
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const offerResp = await fetch(`/v1/render-sessions/${session_id}/offer`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
    });
    if (!offerResp.ok) throw new Error(`offer: ${await offerResp.text()}`);
    const answer = (await offerResp.json()) as RTCSessionDescriptionInit;
    await pc.setRemoteDescription(answer);
  }

  private handleEvent = (e: EventMsg): void => {
    if (e.type === "webrtc.candidate" && this.pc) {
      const cand = e["candidate"] as RTCIceCandidateInit | undefined;
      if (cand) void this.pc.addIceCandidate(cand);
    } else if (e.type === "render-session.state" && e["state"] === "closed") {
      void this.close();
    }
  };

  async sendCamera(pose: { T: number[]; R: number[] }): Promise<void> {
    // Camera control runs over a data channel created server-side in
    // Phase 5.7. For v1 scaffold, no-op — gracefully degrade.
    const dc = (this.pc as unknown as { dataChannels?: RTCDataChannel[] })
      ?.dataChannels?.[0];
    if (dc && dc.readyState === "open") {
      dc.send(JSON.stringify({ type: "setPose", ...pose }));
    }
  }

  async close(): Promise<void> {
    if (this.sessionId) {
      try {
        await fetch(`/v1/render-sessions/${this.sessionId}`, { method: "DELETE" });
      } catch {
        /* ignore */
      }
    }
    this.unsubEvents?.();
    this.unsubEvents = null;
    this.pc?.close();
    this.pc = null;
    this.sessionId = null;
    this.videoTrack = null;
  }
}
