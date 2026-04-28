import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:livekit_client/livekit_client.dart';
import '../controllers/room_controller.dart';

class RoomPage extends GetView<RoomController> {
  const RoomPage({super.key});

  @override
  Widget build(BuildContext context) {
    debugPrint('[SUTANDO] RoomPage build');
    return Scaffold(
      body: GestureDetector(
        onTap: controller.toggleOverlay,
        child: Stack(
          children: [
            // Video layer — completely static tree, never rebuilt by orientation
            Positioned.fill(
              child: _VideoLayer(),
            ),

            // Overlay UI — only this reacts to showOverlay/isLandscape
            Positioned.fill(
              child: _OverlayLayer(),
            ),
          ],
        ),
      ),
    );
  }
}

class _VideoLayer extends GetView<RoomController> {
  @override
  Widget build(BuildContext context) {
    debugPrint('[SUTANDO] _VideoLayer build');
    return Container(
      color: const Color(0xFF0A0A12),
      child: Obx(() {
        final track = controller.remoteVideoTrack.value;
        debugPrint('[SUTANDO] Video Obx: track=${track?.sid ?? "null"}');
        if (track != null) {
          return VideoTrackRenderer(
            track,
            fit: VideoViewFit.contain,
          );
        }
        return const Center(
          child: Text(
            'Waiting for PC screen...',
            style: TextStyle(color: Color(0xFF555555)),
          ),
        );
      }),
    );
  }
}

class _OverlayLayer extends GetView<RoomController> {
  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (!controller.showOverlay.value) {
        return const SizedBox.shrink();
      }
      final isLandscape = controller.isLandscape.value;
      return Container(
        color: isLandscape
            ? Colors.black.withValues(alpha: 0.3)
            : Colors.transparent,
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(12),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    if (!isLandscape)
                      const Text(
                        'Sutando Remote',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w500,
                          color: Color(0xFFE0E0F0),
                        ),
                      ),
                    if (isLandscape) _buildParticipantsText(),
                    _buildStatusBadge(),
                  ],
                ),
              ),
              const Spacer(),
              if (!isLandscape)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: _buildParticipantsText(),
                ),
              Padding(
                padding: isLandscape
                    ? const EdgeInsets.all(16)
                    : const EdgeInsets.fromLTRB(24, 0, 24, 24),
                child: _buildControlBar(),
              ),
            ],
          ),
        ),
      );
    });
  }

  Widget _buildStatusBadge() {
    return Obx(() => Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: BoxDecoration(
            color: const Color(0xFF1A1A2E),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: controller.isConnected.value
                  ? const Color(0xFF2A6E2A)
                  : const Color(0xFF2A2A4E),
            ),
          ),
          child: Text(
            controller.statusText.value,
            style: TextStyle(
              fontSize: 13,
              color: controller.isConnected.value
                  ? const Color(0xFF6FBF6F)
                  : const Color(0xFFC0C0D0),
            ),
          ),
        ));
  }

  Widget _buildParticipantsText() {
    return Obx(() {
      final p = controller.participants;
      if (p.isEmpty) {
        return const Text(
          'Waiting for other participants...',
          style: TextStyle(fontSize: 12, color: Color(0xFF888888)),
        );
      }
      return Text(
        'In room: ${p.join(', ')}',
        style: const TextStyle(fontSize: 12, color: Color(0xFF888888)),
      );
    });
  }

  Widget _buildControlBar() {
    return Row(
      children: [
        Expanded(
          child: Obx(() => ElevatedButton(
                onPressed: controller.toggleMute,
                style: ElevatedButton.styleFrom(
                  backgroundColor: controller.isMuted.value
                      ? const Color(0xFFB91C1C)
                      : const Color(0xFF444444),
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                child: Text(
                  controller.isMuted.value ? 'Unmute' : 'Mute',
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                    color: Colors.white,
                  ),
                ),
              )),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ElevatedButton(
            onPressed: controller.disconnect,
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFDC2626),
              padding: const EdgeInsets.symmetric(vertical: 14),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
              ),
            ),
            child: const Text(
              'Disconnect',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: Colors.white,
              ),
            ),
          ),
        ),
      ],
    );
  }
}
