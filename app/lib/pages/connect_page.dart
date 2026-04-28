import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../controllers/connect_controller.dart';

class ConnectPage extends GetView<ConnectController> {
  const ConnectPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Text(
                'Sutando Remote',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w600,
                  color: Color(0xFFE0E0F0),
                ),
              ),
              const SizedBox(height: 40),
              TextField(
                controller: controller.serverUrlController,
                style: const TextStyle(color: Colors.white),
                decoration: InputDecoration(
                  hintText: '192.168.1.100:8080',
                  hintStyle: TextStyle(color: Colors.grey[600]),
                  labelText: 'Server Address',
                  labelStyle: const TextStyle(color: Color(0xFFC0C0D0)),
                  filled: true,
                  fillColor: const Color(0xFF1A1A2E),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: Color(0xFF2A2A4E)),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: Color(0xFF2A2A4E)),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: Color(0xFF2563EB)),
                  ),
                ),
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.go,
                onSubmitted: (_) => controller.connect(),
              ),
              const SizedBox(height: 16),
              Obx(() {
                if (controller.errorMessage.value.isNotEmpty) {
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Text(
                      controller.errorMessage.value,
                      style: const TextStyle(color: Color(0xFFBF6F6F), fontSize: 13),
                      textAlign: TextAlign.center,
                    ),
                  );
                }
                return const SizedBox.shrink();
              }),
              SizedBox(
                width: double.infinity,
                height: 50,
                child: Obx(() => ElevatedButton(
                  onPressed: controller.isConnecting.value ? null : controller.connect,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF2563EB),
                    disabledBackgroundColor: const Color(0xFF333333),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  child: controller.isConnecting.value
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Text(
                          'Connect',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                            color: Colors.white,
                          ),
                        ),
                )),
              ),
              const SizedBox(height: 24),
              Text(
                'PC and phone must be on the same WiFi.',
                style: TextStyle(fontSize: 12, color: Colors.grey[600]),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
