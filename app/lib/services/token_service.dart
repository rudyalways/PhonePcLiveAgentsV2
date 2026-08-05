import 'dart:io';

import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import '../config/app_config.dart';
import '../models/token_response.dart';

class TokenService {
  Dio _createDio() {
    final dio = Dio(BaseOptions(
      connectTimeout: const Duration(seconds: 5),
      receiveTimeout: const Duration(seconds: 5),
    ));
    // Trust self-signed certs on LAN
    (dio.httpClientAdapter as IOHttpClientAdapter).createHttpClient = () {
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
    return dio;
  }

  Future<TokenResponse> fetchToken(
    String serverUrl, {
    required String username,
    required String secret,
  }) async {
    final dio = _createDio();
    final resp = await dio.get(
      '$serverUrl${AppConfig.tokenPath}',
      queryParameters: {
        'user': username,
        'secret': secret,
        'identity': AppConfig.phoneIdentity,
        'name': AppConfig.phoneDisplayName,
      },
    );
    return TokenResponse.fromJson(resp.data as Map<String, dynamic>);
  }
}
