import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/token_service.dart';
import '../app/routes.dart';

class ConnectController extends GetxController {
  final serverUrlController = TextEditingController();
  final isConnecting = false.obs;
  final errorMessage = ''.obs;

  final _tokenService = Get.find<TokenService>();

  @override
  void onInit() {
    super.onInit();
    _loadSavedUrl();
  }

  Future<void> _loadSavedUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString('server_url');
    if (saved != null && saved.isNotEmpty) {
      serverUrlController.text = saved;
    }
  }

  Future<void> _saveUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
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
    if (url.isEmpty) {
      errorMessage.value = 'Please enter server address';
      return;
    }

    isConnecting.value = true;
    errorMessage.value = '';

    try {
      final tokenResp = await _tokenService.fetchToken(url);
      if (tokenResp.url.isEmpty) {
        throw Exception('LIVEKIT_URL not configured on server');
      }
      await _saveUrl(serverUrlController.text.trim());
      Get.toNamed(AppRoutes.room, arguments: {
        'jwt': tokenResp.jwt,
        'livekitUrl': tokenResp.url,
        'serverUrl': url,
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
    super.onClose();
  }
}
