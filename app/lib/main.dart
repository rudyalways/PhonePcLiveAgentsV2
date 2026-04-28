import 'dart:io';

import 'package:flutter/material.dart';
import 'package:get/get.dart';

import 'app/routes.dart';
import 'app/bindings.dart';

class _LocalHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    return super.createHttpClient(context)
      ..badCertificateCallback = (cert, host, port) {
        return _isPrivateIp(host);
      };
  }

  bool _isPrivateIp(String host) {
    return host.startsWith('192.168.') ||
        host.startsWith('10.') ||
        host.startsWith('172.') ||
        host == 'localhost' ||
        host == '127.0.0.1';
  }
}

void main() {
  HttpOverrides.global = _LocalHttpOverrides();
  runApp(const SutandoApp());
}

class SutandoApp extends StatelessWidget {
  const SutandoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return GetMaterialApp(
      title: 'Sutando Remote',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF0A0A12),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF2563EB),
          error: Color(0xFFDC2626),
        ),
      ),
      initialBinding: AppBindings(),
      initialRoute: AppRoutes.connect,
      getPages: AppRoutes.pages,
    );
  }
}
