plugins {
    id("com.android.application")
}

android {
    namespace = "io.github.gabrielesantangelo.plusone"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.gabrielesantangelo.plusone"
        // Front-buffered rendering needs Android 10.
        minSdk = 29
        targetSdk = 36
        versionCode = 1
        versionName = "0.5.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
}

dependencies {
    // Ink drawn straight into the front buffer, and the pen's next samples predicted.
    implementation("androidx.graphics:graphics-core:1.0.4")
    implementation("androidx.input:input-motionprediction:1.0.0")
}
