import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/token_service.dart';
import '../app/routes.dart';

class ConnectController extends GetxController {
  final serverUrlController = TextEditingController();
  final usernameController = TextEditingController();
  final secretController = TextEditingController();
  final isConnecting = false.obs;
  final errorMessage = ''.obs;

  final _tokenService = Get.find<TokenService>();

  @override
  void onInit() {
    super.onInit();
    _loadSaved();
  }

  Future<void> _loadSaved() async {
    final prefs = await SharedPreferences.getInstance();
    final savedUrl = prefs.getString('server_url');
    final savedUser = prefs.getString('username');
    if (savedUrl != null && savedUrl.isNotEmpty) {
      serverUrlController.text = savedUrl;
    }
    if (savedUser != null && savedUser.isNotEmpty) {
      usernameController.text = savedUser;
    }
  }

  Future<void> _saveCreds(String url, String username) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
    await prefs.setString('username', username);
  }

  String _normalizeUrl(String input) {
    var url = input.trim();
    if (url.isEmpty) return '';
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://$url';
    }
    if (url.endsWith('/')) {
      url = url.substring(0, url.length - 1);
    }
    return url;
  }

  Future<void> connect() async {
    final url = _normalizeUrl(serverUrlController.text);
    final username = usernameController.text.trim();
    final secret = secretController.text;

    if (url.isEmpty) {
      errorMessage.value = 'Please enter server address';
      return;
    }
    if (username.isEmpty || secret.isEmpty) {
      errorMessage.value = 'Please enter username and password';
      return;
    }

    isConnecting.value = true;
    errorMessage.value = '';

    try {
      final tokenResp = await _tokenService.fetchToken(
        url,
        username: username,
        secret: secret,
      );
      if (tokenResp.url.isEmpty) {
        throw Exception('LIVEKIT_URL not configured on server');
      }
      await _saveCreds(serverUrlController.text.trim(), username);
      Get.toNamed(AppRoutes.room, arguments: {
        'jwt': tokenResp.jwt,
        'livekitUrl': tokenResp.url,
        'serverUrl': url,
        'username': username,
        'secret': secret,
      });
    } catch (e) {
      errorMessage.value = e.toString().replaceFirst('Exception: ', '');
    } finally {
      isConnecting.value = false;
    }
  }

  @override
  void onClose() {
    serverUrlController.dispose();
    usernameController.dispose();
    secretController.dispose();
    super.onClose();
  }
}
