class TokenResponse {
  final String jwt;
  final String url;
  final String room;
  final String identity;

  TokenResponse({
    required this.jwt,
    required this.url,
    required this.room,
    required this.identity,
  });

  factory TokenResponse.fromJson(Map<String, dynamic> json) {
    return TokenResponse(
      jwt: json['jwt'] as String,
      url: json['url'] as String,
      room: json['room'] as String? ?? '',
      identity: json['identity'] as String? ?? '',
    );
  }
}
