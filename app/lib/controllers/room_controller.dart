import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';
import 'package:livekit_client/livekit_client.dart';
import '../config/app_config.dart';
import '../services/input_control_service.dart';

class RoomController extends GetxController with WidgetsBindingObserver {
  late Room room;
  late EventsListener<RoomEvent> _listener;

  final isConnected = false.obs;
  final isMuted = false.obs;
  final isLandscape = false.obs;
  final showOverlay = true.obs;
  final statusText = 'Connecting...'.obs;
  final participants = <String>[].obs;
  final remoteVideoTrack = Rxn<VideoTrack>();

  // Input control
  InputControlService? inputControl;
  final isInputReady = false.obs;
  final showKeyboard = false.obs;
  final showQuickActions = false.obs;
  final volumeLevel = 50.obs;
  final brightnessLevel = 50.obs;
  final inputMode = 'touch'.obs; // 'touch' or 'trackpad'
  final showControlBar = true.obs;

  @override
  void onInit() {
    super.onInit();
    WidgetsBinding.instance.addObserver(this);
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.portraitUp,
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    // Check initial orientation
    _checkOrientation();
    final args = Get.arguments as Map<String, dynamic>;
    _initRoom(args['jwt'] as String, args['livekitUrl'] as String);
    _initInputControl(args);
  }

  void _initInputControl(Map<String, dynamic> args) {
    final serverUrl = args['serverUrl'] as String? ?? '';
    final username = args['username'] as String? ?? '';
    final secret = args['secret'] as String? ?? '';

    if (serverUrl.isEmpty || username.isEmpty || secret.isEmpty) {
      debugPrint('[SUTANDO] Input control: missing credentials');
      return;
    }

    // Extract host from serverUrl (HTTPS screen publisher; default port 7081 — see AppConfig).
    final uri = Uri.tryParse(serverUrl);
    if (uri == null || uri.host.isEmpty) {
      debugPrint('[SUTANDO] Input control: invalid serverUrl=$serverUrl');
      return;
    }

    inputControl = InputControlService(
      host: uri.host,
      port: AppConfig.mobileControlPort,
      username: username,
      secret: secret,
    );

    // Fetch screen info in background
    inputControl!.fetchScreenInfo().then((ok) {
      isInputReady.value = ok;
      debugPrint('[SUTANDO] Input control ready=$ok');
    });
  }

  /// Convert touch position on the video widget to PC screen coordinates.
  /// The video uses VideoViewFit.contain, so we need to account for letterboxing.
  Offset? mapToScreenCoords(
      Offset touchPos, Size viewSize) {
    final svc = inputControl;
    if (svc == null || svc.screenWidth == null || svc.screenHeight == null) {
      return null;
    }

    final screenW = svc.screenWidth!.toDouble();
    final screenH = svc.screenHeight!.toDouble();

    // Compute the actual video rect within the view (contain fit)
    final viewAspect = viewSize.width / viewSize.height;
    final screenAspect = screenW / screenH;

    double videoW, videoH, offsetX, offsetY;
    if (viewAspect > screenAspect) {
      // View is wider than video → pillarboxing (black bars on left/right)
      videoH = viewSize.height;
      videoW = videoH * screenAspect;
      offsetX = (viewSize.width - videoW) / 2;
      offsetY = 0;
    } else {
      // View is taller than video → letterboxing (black bars on top/bottom)
      videoW = viewSize.width;
      videoH = videoW / screenAspect;
      offsetX = 0;
      offsetY = (viewSize.height - videoH) / 2;
    }

    // Check if touch is within the video area
    final relX = touchPos.dx - offsetX;
    final relY = touchPos.dy - offsetY;
    if (relX < 0 || relX > videoW || relY < 0 || relY > videoH) {
      return null; // Touch is on the black bars
    }

    // Map to screen coordinates
    final pcX = (relX / videoW) * screenW;
    final pcY = (relY / videoH) * screenH;

    return Offset(pcX, pcY);
  }

  void onVideoTap(Offset touchPos, Size viewSize) {
    final coords = mapToScreenCoords(touchPos, viewSize);
    if (coords == null || inputControl == null) return;
    inputControl!.click(coords.dx.round(), coords.dy.round());
  }

  void onVideoDoubleTap(Offset touchPos, Size viewSize) {
    final coords = mapToScreenCoords(touchPos, viewSize);
    if (coords == null || inputControl == null) return;
    inputControl!
        .click(coords.dx.round(), coords.dy.round(), doubleClick: true);
  }

  void onVideoLongPress(Offset touchPos, Size viewSize) {
    final coords = mapToScreenCoords(touchPos, viewSize);
    if (coords == null || inputControl == null) return;
    inputControl!
        .click(coords.dx.round(), coords.dy.round(), button: 'right');
  }

  void onScroll(double deltaY) {
    if (inputControl == null) return;
    inputControl!.scroll(deltaY: deltaY > 0 ? -3 : 3);
  }

  void sendKey(String key, {List<String>? modifiers}) {
    if (inputControl == null) return;
    inputControl!.key(key, modifiers: modifiers);
  }

  void sendText(String text) {
    if (inputControl == null || text.isEmpty) return;
    inputControl!.type(text);
  }

  void setVolume(int level) {
    if (inputControl == null) return;
    volumeLevel.value = level;
    inputControl!.volume(level: level);
  }

  void toggleMuteVolume() {
    if (inputControl == null) return;
    inputControl!.volume(mute: true);
  }

  void unmuteVolume() {
    if (inputControl == null) return;
    inputControl!.volume(mute: false);
  }

  void setBrightness(int level) {
    if (inputControl == null) return;
    brightnessLevel.value = level;
    inputControl!.brightness(level);
  }

  void toggleKeyboard() {
    showKeyboard.value = !showKeyboard.value;
    if (showKeyboard.value) {
      showQuickActions.value = false;
    }
  }

  void toggleQuickActions() {
    showQuickActions.value = !showQuickActions.value;
    if (showQuickActions.value) {
      showKeyboard.value = false;
    }
  }

  void toggleControlBar() {
    showControlBar.value = !showControlBar.value;
    // Close keyboard/quick actions when hiding control bar
    if (!showControlBar.value) {
      showKeyboard.value = false;
      showQuickActions.value = false;
    }
  }

  void _checkOrientation() {
    final window = WidgetsBinding.instance.platformDispatcher.views.first;
    final size = window.physicalSize / window.devicePixelRatio;
    final landscape = size.width > size.height;
    if (isLandscape.value != landscape) {
      isLandscape.value = landscape;
      // Keep overlay visible in both orientations
      // In landscape, user can tap backdrop to hide; in portrait, always show
      showOverlay.value = true;
      if (landscape) {
        SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
      } else {
        SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
      }
    }
    debugPrint('[SUTANDO] _checkOrientation: landscape=$landscape, size=$size');
  }

  @override
  void didChangeMetrics() {
    // Called when screen dimensions change (orientation, keyboard, etc.)
    _checkOrientation();
  }

  Future<void> _initRoom(String jwt, String livekitUrl) async {
    room = Room(
      roomOptions: RoomOptions(
        adaptiveStream: false,
        dynacast: true,
      ),
    );

    _listener = room.createListener();
    _setupListeners();

    try {
      await room.connect(livekitUrl, jwt);
      debugPrint('[SUTANDO] Room connected');
      await room.localParticipant?.setMicrophoneEnabled(true);

      if (lkPlatformIs(PlatformType.android)) {
        await Hardware.instance.setSpeakerphoneOn(true);
      }

      isConnected.value = true;
      statusText.value = 'Connected';
      _updateParticipants();
    } catch (e) {
      debugPrint('[SUTANDO] Room connect error: $e');
      statusText.value = 'Error: $e';
    }
  }

  void _setupListeners() {
    _listener
      ..on<TrackSubscribedEvent>((event) {
        debugPrint('[SUTANDO] TrackSubscribed: kind=${event.track.kind}, sid=${event.publication.sid}');
        if (event.track is VideoTrack) {
          debugPrint('[SUTANDO] Setting remoteVideoTrack, mediaStream=${(event.track as VideoTrack).mediaStream.id}');
          remoteVideoTrack.value = event.track as VideoTrack;
        }
        _updateParticipants();
      })
      ..on<TrackUnsubscribedEvent>((event) {
        debugPrint('[SUTANDO] TrackUnsubscribed: kind=${event.track.kind}');
        if (event.track is VideoTrack) {
          remoteVideoTrack.value = null;
        }
        _updateParticipants();
      })
      ..on<RoomDisconnectedEvent>((_) {
        debugPrint('[SUTANDO] Room disconnected');
        isConnected.value = false;
        statusText.value = 'Disconnected';
        remoteVideoTrack.value = null;
        participants.clear();
      })
      ..on<ParticipantConnectedEvent>((_) => _updateParticipants())
      ..on<ParticipantDisconnectedEvent>((_) => _updateParticipants());
  }

  void _updateParticipants() {
    final names = <String>[];
    for (final p in room.remoteParticipants.values) {
      names.add(p.identity);
    }
    participants.value = names;
  }

  void toggleMute() {
    isMuted.value = !isMuted.value;
    room.localParticipant?.setMicrophoneEnabled(!isMuted.value);
  }

  void toggleOverlay() {
    if (isLandscape.value) {
      // In landscape, toggle control bar instead of entire overlay
      // This keeps the hint button visible so user can always restore controls
      toggleControlBar();
    }
  }

  Future<void> disconnect() async {
    await room.disconnect();
    Get.back();
  }

  @override
  void onClose() {
    WidgetsBinding.instance.removeObserver(this);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.portraitUp,
      DeviceOrientation.portraitDown,
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    _listener.dispose();
    room.disconnect();
    room.dispose();
    super.onClose();
  }
}
