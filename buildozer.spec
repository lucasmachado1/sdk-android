[app]
title = AcoNuWiFi
package.name = aconuwifi
package.domain = com.aconu.wifi
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 2.0.0
 
requirements = python3,kivy==2.3.0,kivymd==1.2.0,pyjnius,pillow

orientation = portrait
fullscreen = 0

# Permissões do Android
android.permissions = CAMERA, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, ACCESS_WIFI_STATE, CHANGE_WIFI_STATE, ACCESS_NETWORK_STATE, CHANGE_NETWORK_STATE

# Dependências nativas Gradle (Google ML Kit OCR)
android.gradle_dependencies = com.google.mlkit:text-recognition:16.0.0

android.api = 33
android.accept_sdk_license = True
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1

