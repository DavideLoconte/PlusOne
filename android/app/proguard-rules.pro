# WebRTC calls into its Java classes from native code, by name.
-keep class org.webrtc.** { *; }
-dontwarn org.webrtc.**
