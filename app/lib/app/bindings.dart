import 'package:get/get.dart';
import '../services/token_service.dart';

class AppBindings extends Bindings {
  @override
  void dependencies() {
    Get.lazyPut(() => TokenService(), fenix: true);
  }
}
