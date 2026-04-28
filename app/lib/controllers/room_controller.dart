import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';
import 'package:livekit_client/livekit_client.dart';

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
  }

  void _checkOrientation() {
    final window = WidgetsBinding.instance.platformDispatcher.views.first;
    final size = window.physicalSize / window.devicePixelRatio;
    final landscape = size.width > size.height;
    if (isLandscape.value != landscape) {
      isLandscape.value = landscape;
      showOverlay.value = !landscape;
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
      showOverlay.value = !showOverlay.value;
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
