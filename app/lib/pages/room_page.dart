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
      resizeToAvoidBottomInset: false,
      body: Stack(
        children: [
          // Video layer with touch-to-click
          Positioned.fill(
            child: _VideoLayer(),
          ),

          // Overlay UI
          Positioned.fill(
            child: _OverlayLayer(),
          ),

          // Bottom panels (keyboard / quick actions)
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: _BottomPanels(),
          ),
        ],
      ),
    );
  }
}

class _VideoLayer extends StatefulWidget {
  @override
  State<_VideoLayer> createState() => _VideoLayerState();
}

class _VideoLayerState extends State<_VideoLayer> {
  Offset _lastFocalPoint = Offset.zero;

  @override
  Widget build(BuildContext context) {
    final controller = Get.find<RoomController>();
    debugPrint('[SUTANDO] _VideoLayer build');
    return LayoutBuilder(
      builder: (context, constraints) {
        final viewSize = Size(constraints.maxWidth, constraints.maxHeight);
        return Container(
          color: const Color(0xFF0A0A12),
          child: Obx(() {
            final track = controller.remoteVideoTrack.value;
            final inputReady = controller.isInputReady.value;
            debugPrint('[SUTANDO] Video Obx: track=${track?.sid ?? "null"}, inputReady=$inputReady');
            if (track != null) {
              return GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapUp: (details) {
                  if (inputReady) {
                    controller.onVideoTap(details.localPosition, viewSize);
                  } else {
                    controller.toggleOverlay();
                  }
                },
                onDoubleTapDown: inputReady
                    ? (details) {
                        controller.onVideoDoubleTap(
                            details.localPosition, viewSize);
                      }
                    : null,
                onLongPressStart: inputReady
                    ? (details) {
                        controller.onVideoLongPress(
                            details.localPosition, viewSize);
                      }
                    : null,
                onScaleStart: (details) {
                  _lastFocalPoint = details.focalPoint;
                },
                onScaleUpdate: (details) {
                  // Two-finger swipe to toggle control bar
                  if (details.pointerCount == 2 && details.scale == 1.0) {
                    final delta = details.focalPoint - _lastFocalPoint;
                    if (delta.dy.abs() > 50) {
                      if (delta.dy > 0) {
                        // Swipe down → show control bar
                        controller.showControlBar.value = true;
                      } else {
                        // Swipe up → hide control bar
                        controller.showControlBar.value = false;
                      }
                      _lastFocalPoint = details.focalPoint;
                    }
                  }
                  // Single-finger vertical drag for scroll (when input ready)
                  else if (inputReady && details.pointerCount == 1) {
                    final delta = details.focalPoint - _lastFocalPoint;
                    if (delta.dy.abs() > 2) {
                      controller.onScroll(delta.dy);
                    }
                    _lastFocalPoint = details.focalPoint;
                  }
                },
                child: VideoTrackRenderer(
                  track,
                  fit: VideoViewFit.contain,
                ),
              );
            }
            return GestureDetector(
              onTap: controller.toggleOverlay,
              child: const Center(
                child: Text(
                  'Waiting for PC screen...',
                  style: TextStyle(color: Color(0xFF555555)),
                ),
              ),
            );
          }),
        );
      },
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

      // In both portrait and landscape, overlay should NOT block video touches.
      // No background Container — touches pass through empty areas to the video.
      return SafeArea(
        child: _buildContent(isLandscape),
      );
    });
  }

  Widget _buildContent(bool isLandscape) {
    return Column(
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
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  _buildStatusBadge(),
                  // Show/Hide button in landscape mode, positioned below status badge
                  if (isLandscape) ...[
                    const SizedBox(height: 8),
                    Obx(() {
                      final showBar = controller.showControlBar.value;
                      return GestureDetector(
                        onTap: controller.toggleControlBar,
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                          decoration: BoxDecoration(
                            color: const Color(0xFF2A2A3E).withValues(alpha: 0.9),
                            borderRadius: BorderRadius.circular(20),
                            border: Border.all(
                              color: const Color(0xFF4A4A6E),
                            ),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                showBar ? Icons.keyboard_arrow_down : Icons.keyboard_arrow_up,
                                color: Colors.white70,
                                size: 18,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                showBar ? 'Hide' : 'Show',
                                style: const TextStyle(
                                  color: Colors.white70,
                                  fontSize: 12,
                                ),
                              ),
                            ],
                          ),
                        ),
                      );
                    }),
                  ],
                ],
              ),
            ],
          ),
        ),
        const Spacer(),
        if (!isLandscape)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: _buildParticipantsText(),
          ),
        // Floating hint button for control bar toggle (portrait only)
        if (!isLandscape)
          Obx(() {
            final showBar = controller.showControlBar.value;
            return Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Center(
                child: GestureDetector(
                  onTap: controller.toggleControlBar,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFF2A2A3E).withValues(alpha: 0.9),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                        color: const Color(0xFF4A4A6E),
                      ),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          showBar ? Icons.keyboard_arrow_down : Icons.keyboard_arrow_up,
                          color: Colors.white70,
                          size: 18,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          showBar ? 'Hide' : 'Show',
                          style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            );
          }),
        Padding(
          padding: isLandscape
              ? const EdgeInsets.all(16)
              : const EdgeInsets.fromLTRB(24, 0, 24, 24),
          child: _buildControlBar(),
        ),
      ],
    );
  }

  Widget _buildStatusBadge() {
    return Obx(() {
      final connected = controller.isConnected.value;
      final inputReady = controller.isInputReady.value;
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: BoxDecoration(
          color: const Color(0xFF1A1A2E),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: connected
                ? const Color(0xFF2A6E2A)
                : const Color(0xFF2A2A4E),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              controller.statusText.value,
              style: TextStyle(
                fontSize: 13,
                color: connected
                    ? const Color(0xFF6FBF6F)
                    : const Color(0xFFC0C0D0),
              ),
            ),
            if (inputReady) ...[
              const SizedBox(width: 6),
              Container(
                width: 6,
                height: 6,
                decoration: const BoxDecoration(
                  color: Color(0xFF4CAF50),
                  shape: BoxShape.circle,
                ),
              ),
            ],
          ],
        ),
      );
    });
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
    return Obx(() {
      final inputReady = controller.isInputReady.value;
      final showBar = controller.showControlBar.value;

      return AnimatedSlide(
        offset: showBar ? Offset.zero : const Offset(0, 1),
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeInOut,
        child: AnimatedOpacity(
          opacity: showBar ? 1.0 : 0.0,
          duration: const Duration(milliseconds: 250),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (inputReady) ...[
                _buildIconButton(
                  icon: Icons.keyboard,
                  label: 'Keyboard',
                  isActive: controller.showKeyboard.value,
                  onTap: controller.toggleKeyboard,
                ),
                const SizedBox(width: 12),
                _buildIconButton(
                  icon: Icons.tune,
                  label: 'Controls',
                  isActive: controller.showQuickActions.value,
                  onTap: controller.toggleQuickActions,
                ),
                const SizedBox(width: 12),
              ],
              Expanded(
                child: ElevatedButton(
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
                ),
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
                    'Leave',
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    });
  }

  Widget _buildIconButton({
    required IconData icon,
    required String label,
    required bool isActive,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: isActive ? const Color(0xFF2A5A8A) : const Color(0xFF2A2A3E),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: isActive ? const Color(0xFF4A8ACA) : const Color(0xFF3A3A5E),
          ),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: Colors.white, size: 20),
            const SizedBox(height: 2),
            Text(
              label,
              style: const TextStyle(color: Colors.white70, fontSize: 10),
            ),
          ],
        ),
      ),
    );
  }
}

class _BottomPanels extends GetView<RoomController> {
  @override
  Widget build(BuildContext context) {
    return Obx(() {
      if (controller.showKeyboard.value) {
        return _KeyboardPanel();
      }
      if (controller.showQuickActions.value) {
        return _QuickActionsPanel();
      }
      return const SizedBox.shrink();
    });
  }
}

class _KeyboardPanel extends GetView<RoomController> {
  final TextEditingController _textController = TextEditingController();

  @override
  Widget build(BuildContext context) {
    return Container(
      color: const Color(0xFF1A1A2E),
      padding: EdgeInsets.only(
        left: 12,
        right: 12,
        top: 8,
        bottom: MediaQuery.of(context).padding.bottom + 8,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Header with close button
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                'Keyboard',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70, size: 20),
                onPressed: () => controller.showKeyboard.value = false,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // Text input row
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _textController,
                  autofocus: true,
                  style: const TextStyle(color: Colors.white, fontSize: 16),
                  decoration: InputDecoration(
                    hintText: 'Type text...',
                    hintStyle: const TextStyle(color: Color(0xFF666666)),
                    filled: true,
                    fillColor: const Color(0xFF0A0A12),
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 12),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: BorderSide.none,
                    ),
                  ),
                  onSubmitted: (text) {
                    if (text.isNotEmpty) {
                      controller.sendText(text);
                    }
                    controller.sendKey('enter');
                  },
                ),
              ),
              const SizedBox(width: 8),
              _sendButton('Send', () {
                final text = _textController.text;
                if (text.isNotEmpty) {
                  controller.sendText(text);
                  _textController.clear();
                }
              }),
            ],
          ),
          const SizedBox(height: 8),
          // Quick key row
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _keyChip('Esc', () => controller.sendKey('escape')),
                _keyChip('Tab', () => controller.sendKey('tab')),
                _keyChip('Enter', () => controller.sendKey('enter')),
                _keyChip('Space', () => controller.sendKey('space')),
                _keyChip('Del', () => controller.sendKey('delete')),
                _keyChip('⌘C', () => controller.sendKey('c', modifiers: ['command'])),
                _keyChip('⌘V', () => controller.sendKey('v', modifiers: ['command'])),
                _keyChip('⌘Z', () => controller.sendKey('z', modifiers: ['command'])),
                _keyChip('⌘A', () => controller.sendKey('a', modifiers: ['command'])),
                _keyChip('⌘S', () => controller.sendKey('s', modifiers: ['command'])),
                _keyChip('⌘W', () => controller.sendKey('w', modifiers: ['command'])),
                _keyChip('⌘F', () => controller.sendKey('f', modifiers: ['command'])),
              ],
            ),
          ),
          const SizedBox(height: 6),
          // Arrow keys row
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              _arrowButton(Icons.arrow_back, () => controller.sendKey('left')),
              const SizedBox(width: 4),
              Column(
                children: [
                  _arrowButton(Icons.arrow_upward, () => controller.sendKey('up')),
                  const SizedBox(height: 4),
                  _arrowButton(Icons.arrow_downward, () => controller.sendKey('down')),
                ],
              ),
              const SizedBox(width: 4),
              _arrowButton(Icons.arrow_forward, () => controller.sendKey('right')),
            ],
          ),
        ],
      ),
    );
  }

  Widget _sendButton(String label, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: const Color(0xFF2A5A8A),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(
          label,
          style: const TextStyle(
              color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
        ),
      ),
    );
  }

  Widget _keyChip(String label, VoidCallback onTap) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: const Color(0xFF2A2A3E),
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: const Color(0xFF3A3A5E)),
          ),
          child: Text(
            label,
            style: const TextStyle(color: Colors.white70, fontSize: 12),
          ),
        ),
      ),
    );
  }

  Widget _arrowButton(IconData icon, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 44,
        height: 36,
        decoration: BoxDecoration(
          color: const Color(0xFF2A2A3E),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xFF3A3A5E)),
        ),
        child: Icon(icon, color: Colors.white70, size: 18),
      ),
    );
  }
}

class _QuickActionsPanel extends GetView<RoomController> {
  @override
  Widget build(BuildContext context) {
    return Container(
      color: const Color(0xFF1A1A2E),
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 12,
        bottom: MediaQuery.of(context).padding.bottom + 12,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Header with close button
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                'Quick Controls',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70, size: 20),
                onPressed: () => controller.showQuickActions.value = false,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Volume
          Obx(() => _buildSliderRow(
                icon: Icons.volume_up,
                label: 'Volume',
                value: controller.volumeLevel.value.toDouble(),
                onChanged: (v) => controller.setVolume(v.round()),
              )),
          const SizedBox(height: 8),
          // Brightness
          Obx(() => _buildSliderRow(
                icon: Icons.brightness_6,
                label: 'Brightness',
                value: controller.brightnessLevel.value.toDouble(),
                onChanged: (v) => controller.setBrightness(v.round()),
              )),
          const SizedBox(height: 12),
          // Quick action buttons
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Obx(() {
                // Toggle mute/unmute based on current volume level
                final isMuted = controller.volumeLevel.value == 0;
                return _actionButton(
                  isMuted ? Icons.volume_off : Icons.volume_up,
                  isMuted ? 'Unmute' : 'Mute',
                  () {
                    if (isMuted) {
                      controller.unmuteVolume();
                      controller.volumeLevel.value = 50; // Reset to 50% when unmuting
                    } else {
                      controller.toggleMuteVolume();
                      controller.volumeLevel.value = 0; // Update UI to show muted
                    }
                  },
                );
              }),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSliderRow({
    required IconData icon,
    required String label,
    required double value,
    required ValueChanged<double> onChanged,
  }) {
    return Row(
      children: [
        Icon(icon, color: Colors.white70, size: 20),
        const SizedBox(width: 8),
        SizedBox(
          width: 56,
          child: Text(
            '$label ${value.round()}',
            style: const TextStyle(color: Colors.white70, fontSize: 11),
          ),
        ),
        Expanded(
          child: SliderTheme(
            data: SliderThemeData(
              trackHeight: 4,
              thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8),
              activeTrackColor: const Color(0xFF4A8ACA),
              inactiveTrackColor: const Color(0xFF2A2A3E),
              thumbColor: const Color(0xFF6AB0FF),
              overlayColor: const Color(0xFF4A8ACA).withValues(alpha: 0.2),
            ),
            child: Slider(
              value: value.clamp(0, 100),
              min: 0,
              max: 100,
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }

  Widget _actionButton(IconData icon, String label, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: const Color(0xFF2A2A3E),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: const Color(0xFF3A3A5E)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: Colors.white70, size: 20),
            const SizedBox(height: 4),
            Text(
              label,
              style: const TextStyle(color: Colors.white70, fontSize: 10),
            ),
          ],
        ),
      ),
    );
  }
}
