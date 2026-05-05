import 'dart:io';
import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/foundation.dart';

class InputControlService {
  late final Dio _dio;
  final String _baseUrl;

  int? _screenWidth;
  int? _screenHeight;

  int? get screenWidth => _screenWidth;
  int? get screenHeight => _screenHeight;

  InputControlService({
    required String host,
    required int port,
    required String username,
    required String secret,
  }) : _baseUrl = 'http://$host:$port' {
    _dio = Dio(BaseOptions(
      baseUrl: _baseUrl,
      connectTimeout: const Duration(seconds: 3),
      receiveTimeout: const Duration(seconds: 5),
      headers: {
        'X-User': username,
        'X-Secret': secret,
        'Content-Type': 'application/json',
      },
    ));

    // Trust LAN self-signed certs
    (_dio.httpClientAdapter as IOHttpClientAdapter).createHttpClient = () {
      final client = HttpClient();
      client.badCertificateCallback = (cert, host, port) {
        return host.startsWith('192.168.') ||
            host.startsWith('10.') ||
            host.startsWith('172.') ||
            host == 'localhost' ||
            host == '127.0.0.1';
      };
      return client;
    };
  }

  Future<bool> fetchScreenInfo() async {
    try {
      final response = await _dio.get('/screen/info');
      if (response.statusCode == 200) {
        _screenWidth = response.data['width'];
        _screenHeight = response.data['height'];
        debugPrint('[InputControl] Screen: ${_screenWidth}x$_screenHeight');
        return true;
      }
    } catch (e) {
      debugPrint('[InputControl] fetchScreenInfo error: $e');
    }
    return false;
  }

  Future<bool> click(int x, int y,
      {String button = 'left', bool doubleClick = false}) async {
    try {
      final response = await _dio.post('/input/click', data: {
        'x': x,
        'y': y,
        'button': button,
        'double': doubleClick,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] click error: $e');
      return false;
    }
  }

  Future<bool> key(String key, {List<String>? modifiers}) async {
    try {
      final response = await _dio.post('/input/key', data: {
        'key': key,
        if (modifiers != null) 'modifiers': modifiers,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] key error: $e');
      return false;
    }
  }

  Future<bool> type(String text) async {
    try {
      final response = await _dio.post('/input/type', data: {
        'text': text,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] type error: $e');
      return false;
    }
  }

  Future<bool> scroll({int deltaX = 0, int deltaY = 0}) async {
    try {
      final response = await _dio.post('/input/scroll', data: {
        'deltaX': deltaX,
        'deltaY': deltaY,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] scroll error: $e');
      return false;
    }
  }

  Future<bool> volume({int? level, bool? mute}) async {
    try {
      final response = await _dio.post('/system/volume', data: {
        if (level != null) 'level': level,
        if (mute != null) 'mute': mute,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] volume error: $e');
      return false;
    }
  }

  Future<bool> brightness(int level) async {
    try {
      final response = await _dio.post('/system/brightness', data: {
        'level': level,
      });
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] brightness error: $e');
      return false;
    }
  }

  Future<bool> healthCheck() async {
    try {
      final response = await _dio.get('/health');
      return response.statusCode == 200 && response.data['status'] == 'ok';
    } catch (e) {
      debugPrint('[InputControl] health check error: $e');
      return false;
    }
  }
}
