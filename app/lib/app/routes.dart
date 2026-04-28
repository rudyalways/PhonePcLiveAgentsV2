import 'package:get/get.dart';
import '../pages/connect_page.dart';
import '../pages/room_page.dart';
import '../controllers/connect_controller.dart';
import '../controllers/room_controller.dart';

class AppRoutes {
  static const connect = '/connect';
  static const room = '/room';

  static final pages = [
    GetPage(
      name: connect,
      page: () => const ConnectPage(),
      binding: BindingsBuilder(() {
        Get.lazyPut(() => ConnectController());
      }),
    ),
    GetPage(
      name: room,
      page: () => const RoomPage(),
      binding: BindingsBuilder(() {
        Get.lazyPut(() => RoomController());
      }),
    ),
  ];
}
