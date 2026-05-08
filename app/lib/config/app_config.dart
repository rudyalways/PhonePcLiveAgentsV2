class AppConfig {
  /// HTTPS `screen-publisher-server` port (must match deploy `CLIENT_PORT` default).
  static const defaultPort = '7081';

  /// Placeholder LAN IP for the connect screen (replace with your PC’s address).
  static const defaultServerHintHost = '192.168.1.100';

  /// Hint / initial template: `host:port` without scheme (scheme added on connect).
  static String get serverAddressHint => '$defaultServerHintHost:$defaultPort';

  static String get defaultHttpsServerUrl =>
      'https://$defaultServerHintHost:$defaultPort';

  static const tokenPath = '/token';
  static const phoneIdentity = 'phone-user';
  static const phoneDisplayName = 'Phone';
  static const mobileControlPort = 7901;
}
